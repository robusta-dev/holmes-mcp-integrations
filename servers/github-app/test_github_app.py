import time

import pytest
import responses
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from github_utils import AppTokens, TokenError, extract_owner

API = "https://api.github.com:443"
ACME = [{"id": 101, "account": {"login": "Acme-Org"}}]
OCTOCAT = [{"id": 202, "account": {"login": "octocat"}}]
NEXT_PAGE = {"Link": '<https://api.github.com/app/installations?page=2>; rel="next"'}


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(65537, 2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()


def call(arguments):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"arguments": arguments}}


def mint(installation, token, seconds=3600):
    responses.add(responses.POST, f"{API}/app/installations/{installation}/access_tokens",
                  json={"token": token, "expires_at": time.strftime(
                      "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))})


@pytest.mark.parametrize("body,expected", [
    (call({"owner": "Robusta-Dev", "repo": "x"}), "robusta-dev"), (call({"org": "acme"}), "acme"),
    (call({"organization": "acme"}), "acme"), (call({"username": "octocat"}), "octocat"),
    (call({"user": "octo"}), "octo"), (call({"owner": "a", "org": "b"}), "a"),
    (call({"owner": "robusta-dev/holmesgpt"}), "robusta-dev"), (call({"q": "user:octo"}), "octo"),
    (call({"query": "repo:Robusta-Dev/x is:open"}), "robusta-dev"),
    (call({"query": "org:acme lang:py"}), "acme"), (call({"query": "stars:>1"}), None),
    (call({"owner": None, "org": 42, "user": " "}), None),
    ({"method": "tools/list", "params": {"arguments": {"org": "a"}}}, None),
    ({"method": "tools/call", "params": "bogus"}, None), (None, None), ([], None)])
def test_extract_owner(body, expected):
    assert extract_owner(body) == expected


@responses.activate
def test_routes_each_organization_to_its_own_installation(key):
    responses.add(responses.GET, f"{API}/app/installations", json=ACME, headers=NEXT_PAGE)
    responses.add(responses.GET, f"{API}/app/installations?page=2", json=OCTOCAT)
    mint(101, "ghs_acme")
    mint(202, "ghs_octocat")
    tokens = AppTokens("12345", key)
    tokens.discover()

    assert tokens.token_for("ACME-ORG") == "ghs_acme"
    assert tokens.token_for("octocat") == "ghs_octocat"
    assert tokens.token_for(None) == tokens.token_for("not-installed") == "ghs_acme"
    assert tokens.ready() and len(responses.calls) == 4


@responses.activate
def test_rediscovers_after_a_failure_and_for_unknown_organizations(key):
    responses.add(responses.GET, f"{API}/app/installations", json=[])
    responses.add(responses.GET, f"{API}/app/installations", status=500, body="boom")
    responses.add(responses.GET, f"{API}/app/installations", json=ACME)
    mint(101, "ghs_acme")
    tokens = AppTokens("12345", key)
    tokens.discover()
    tokens.discover()

    with pytest.raises(TokenError, match="No GitHub App installations"):
        tokens.token_for("acme-org")
    tokens._discovered_at = 0
    assert tokens.token_for("acme-org") == "ghs_acme"


@responses.activate
def test_pinned_installation_skips_discovery_and_renews_its_token(key, monkeypatch):
    mint(555, "ghs_pinned")
    tokens = AppTokens("12345", key.replace("\n", "\\n"), pinned="555")
    tokens.discover()
    assert [tokens.token_for(owner) for owner in ("acme", None)] == ["ghs_pinned"] * 2
    assert len(responses.calls) == 1

    mint(555, "ghs_renewed", seconds=7200)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 3400)
    assert tokens.token_for(None) == "ghs_renewed"


@responses.activate
def test_mint_failure_reports_the_github_message(key):
    responses.add(responses.POST, f"{API}/app/installations/101/access_tokens",
                  status=401, body='{"message":"Integration not found"}')
    with pytest.raises(TokenError, match="401.*Integration not found"):
        AppTokens("12345", key, pinned="101").token_for(None)
