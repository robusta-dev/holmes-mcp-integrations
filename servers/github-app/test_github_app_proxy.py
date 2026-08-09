"""Unit tests for the multi-org GitHub App MCP proxy.

Run with: pytest servers/github-app/
Dev dependencies (not shipped in the image): pytest, responses.
Runtime dependencies (same as the image): PyGithub, requests.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
import responses
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import proxy as proxy_module
from github_utils import (
    InstallationTokenManager,
    StaticTokenManager,
    TokenMintError,
    extract_owner,
)
from proxy import run_proxy

# PyGithub builds request URLs with an explicit port (":443"), and the
# responses library matches URLs literally — so mocks must include it.
API = "https://api.github.com:443"


@pytest.fixture(scope="module")
def private_key_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _expires_at(seconds_from_now: int = 3600) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds_from_now)
    )


# ---------------------------------------------------------------------------
# Owner extraction
# ---------------------------------------------------------------------------


def _tool_call(arguments, method="tools/call"):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {"name": "some_tool", "arguments": arguments},
    }


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({"owner": "robusta-dev", "repo": "holmesgpt"}, "robusta-dev"),
        ({"org": "MyOrg"}, "myorg"),
        ({"organization": "acme"}, "acme"),
        ({"username": "octocat"}, "octocat"),
        ({"user": "octocat"}, "octocat"),
        # owner takes priority over other keys
        ({"owner": "org-a", "org": "org-b"}, "org-a"),
        # "owner/repo" pasted into an owner-only field
        ({"owner": "robusta-dev/holmesgpt"}, "robusta-dev"),
        # case folding
        ({"owner": "Robusta-Dev"}, "robusta-dev"),
        # search query qualifiers
        ({"query": "repo:robusta-dev/holmesgpt is:open"}, "robusta-dev"),
        ({"query": "org:Acme-Corp language:python"}, "acme-corp"),
        ({"query": "user:octocat stars:>10"}, "octocat"),
        ({"query": "owner:someorg topic:ai"}, "someorg"),
        ({"q": "org:acme"}, "acme"),
        # no owner anywhere
        ({"query": "language:python stars:>100"}, None),
        ({"repo": "holmesgpt"}, None),
        ({}, None),
        # non-string / empty values are ignored
        ({"owner": None, "org": 42, "username": "  "}, None),
        ({"owner": "", "query": "org:acme"}, "acme"),
    ],
)
def test_extract_owner_tools_call(arguments, expected):
    assert extract_owner(_tool_call(arguments)) == expected


@pytest.mark.parametrize(
    "body",
    [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _tool_call({"owner": "acme"}, method="resources/read"),
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": "bogus"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"arguments": "bogus"}},
        None,
        "not a dict",
        [],
    ],
)
def test_extract_owner_returns_none(body):
    assert extract_owner(body) is None


def test_extract_owner_batch_takes_first_tools_call():
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        _tool_call({"owner": "org-a"}),
        _tool_call({"owner": "org-b"}),
    ]
    assert extract_owner(batch) == "org-a"


# ---------------------------------------------------------------------------
# Installation discovery
# ---------------------------------------------------------------------------


def _installation(inst_id, login):
    return {"id": inst_id, "account": {"login": login}}


@responses.activate
def test_discovery_paginated_and_lowercased(private_key_pem, monkeypatch):
    monkeypatch.delenv("GITHUB_APP_DEFAULT_OWNER", raising=False)
    monkeypatch.delenv("GITHUB_APP_DEFAULT_INSTALLATION_ID", raising=False)
    # The Link header mimics real GitHub (no explicit port — PyGithub asserts
    # the next-URL port matches its base URL); the mock registration needs it.
    page2_link = "https://api.github.com/app/installations?per_page=100&page=2"
    responses.add(
        responses.GET,
        f"{API}/app/installations",
        json=[_installation(101, "Acme-Org")],
        headers={"Link": f'<{page2_link}>; rel="next"'},
    )
    responses.add(
        responses.GET,
        f"{API}/app/installations?per_page=100&page=2",
        json=[_installation(202, "octocat")],
    )

    mgr = InstallationTokenManager("12345", private_key_pem)
    mgr.refresh_installations()

    assert mgr.installation_count() == 2
    assert mgr.resolve_installation("acme-org") == 101
    assert mgr.resolve_installation("ACME-ORG") == 101
    assert mgr.resolve_installation("octocat") == 202  # user-scoped install
    # default = first discovered
    assert mgr.resolve_installation(None) == 101
    assert mgr.resolve_installation("unknown-org") == 101


@responses.activate
def test_discovery_default_owner_env(private_key_pem, monkeypatch):
    monkeypatch.setenv("GITHUB_APP_DEFAULT_OWNER", "octocat")
    responses.add(
        responses.GET,
        f"{API}/app/installations",
        json=[_installation(101, "acme-org"), _installation(202, "octocat")],
    )
    mgr = InstallationTokenManager("12345", private_key_pem)
    mgr.refresh_installations()
    assert mgr.resolve_installation(None) == 202


@responses.activate
def test_discovery_failure_keeps_previous_map(private_key_pem, monkeypatch):
    monkeypatch.delenv("GITHUB_APP_DEFAULT_OWNER", raising=False)
    monkeypatch.delenv("GITHUB_APP_DEFAULT_INSTALLATION_ID", raising=False)
    responses.add(
        responses.GET,
        f"{API}/app/installations",
        json=[_installation(101, "acme-org")],
    )
    responses.add(responses.GET, f"{API}/app/installations", status=500, body="boom")

    mgr = InstallationTokenManager("12345", private_key_pem)
    mgr.refresh_installations()
    assert mgr.resolve_installation("acme-org") == 101
    mgr.refresh_installations()  # second call fails with 500
    assert mgr.resolve_installation("acme-org") == 101  # map preserved


@responses.activate
def test_discovery_ghes_api_base(private_key_pem, monkeypatch):
    monkeypatch.setenv("GITHUB_HOST", "github.mycompany.com")
    try:
        responses.add(
            responses.GET,
            "https://github.mycompany.com:443/api/v3/app/installations",
            json=[_installation(7, "acme-org")],
        )
        mgr = InstallationTokenManager("12345", private_key_pem)
        mgr.refresh_installations()
        assert mgr.resolve_installation("acme-org") == 7
    finally:
        monkeypatch.delenv("GITHUB_HOST")


def test_no_installations_raises_detailed_error(private_key_pem):
    mgr = InstallationTokenManager("12345", private_key_pem)
    with pytest.raises(TokenMintError, match="No GitHub App installations"):
        mgr.resolve_installation("any-org")


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------


@responses.activate
def test_token_cached_until_expiry_margin(private_key_pem, monkeypatch):
    responses.add(
        responses.POST,
        f"{API}/app/installations/101/access_tokens",
        json={"token": "ghs_first", "expires_at": _expires_at(3600)},
    )
    mgr = InstallationTokenManager("12345", private_key_pem)

    assert mgr.get_token(101) == "ghs_first"
    assert mgr.get_token(101) == "ghs_first"
    assert len(responses.calls) == 1  # second call served from cache

    # Jump to within the 5-minute expiry margin -> re-mint
    responses.add(
        responses.POST,
        f"{API}/app/installations/101/access_tokens",
        json={"token": "ghs_second", "expires_at": _expires_at(7200)},
    )
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 3400)
    assert mgr.get_token(101) == "ghs_second"


@responses.activate
def test_distinct_tokens_per_installation(private_key_pem):
    responses.add(
        responses.POST,
        f"{API}/app/installations/101/access_tokens",
        json={"token": "ghs_org_a", "expires_at": _expires_at()},
    )
    responses.add(
        responses.POST,
        f"{API}/app/installations/202/access_tokens",
        json={"token": "ghs_org_b", "expires_at": _expires_at()},
    )
    mgr = InstallationTokenManager("12345", private_key_pem)
    assert mgr.get_token(101) == "ghs_org_a"
    assert mgr.get_token(202) == "ghs_org_b"


@responses.activate
def test_mint_error_surfaces_github_response(private_key_pem):
    responses.add(
        responses.POST,
        f"{API}/app/installations/101/access_tokens",
        status=401,
        body='{"message":"Integration not found"}',
    )
    mgr = InstallationTokenManager("12345", private_key_pem)
    with pytest.raises(TokenMintError, match="401.*Integration not found"):
        mgr.get_token(101)


# ---------------------------------------------------------------------------
# Pin mode (backwards compatibility)
# ---------------------------------------------------------------------------


@responses.activate
def test_pin_mode_never_discovers(private_key_pem):
    # Only the access_tokens endpoint is registered; a call to
    # /app/installations would fail the test with ConnectionError.
    responses.add(
        responses.POST,
        f"{API}/app/installations/555/access_tokens",
        json={"token": "ghs_pinned", "expires_at": _expires_at()},
    )
    mgr = InstallationTokenManager(
        "12345", private_key_pem, pinned_installation_id="555"
    )
    mgr.refresh_installations()  # must be a no-op
    assert mgr.start_refresh_thread() is None

    for owner in ("acme-org", "unknown", None):
        assert mgr.resolve_installation(owner) == 555
    assert mgr.get_token(555) == "ghs_pinned"
    assert mgr.installation_count() == 1
    assert all("/app/installations/555/" in c.request.url for c in responses.calls)


@responses.activate
def test_private_key_literal_newlines(private_key_pem):
    escaped = private_key_pem.replace("\n", "\\n")
    responses.add(
        responses.POST,
        f"{API}/app/installations/9/access_tokens",
        json={"token": "ghs_ok", "expires_at": _expires_at()},
    )
    mgr = InstallationTokenManager("12345", escaped)
    # Minting signs an App JWT with the key -> the \n normalization did its job
    assert mgr.get_token(9) == "ghs_ok"


def test_static_token_manager():
    mgr = StaticTokenManager("ghp_pat")
    assert mgr.resolve_installation("any") is None
    assert mgr.get_token(None) == "ghp_pat"
    assert mgr.installation_count() == 1


# ---------------------------------------------------------------------------
# Proxy round-trip (real sockets, stub upstream)
# ---------------------------------------------------------------------------


class FakeTokenManager:
    def __init__(self):
        self.tokens = {"org-a": "token-a", "org-b": "token-b"}

    def resolve_installation(self, owner):
        return owner or "default"

    def get_token(self, installation_id):
        return self.tokens.get(installation_id, "token-default")

    def installation_count(self):
        return 2


class StubUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/sse":
            # Chunked transfer-encoding, like Go's net/http produces for SSE.
            # A close-delimited body would be buffered to EOF by urllib3.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def chunk(data):
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            chunk(b"data: first\n\n")
            time.sleep(0.8)
            chunk(b"data: second\n\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        payload = json.dumps(
            {
                "authorization": self.headers.get("Authorization"),
                "mcp_session_id": self.headers.get("Mcp-Session-Id"),
                "accept": self.headers.get("Accept"),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def proxy_url(monkeypatch):
    # Threading matters: the proxy's pooled session keeps connections alive,
    # and a single-threaded stub would wedge inside handle_one_request,
    # making shutdown() block forever.
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), StubUpstreamHandler)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        proxy_module, "UPSTREAM_BASE", f"http://127.0.0.1:{upstream.server_port}"
    )
    server = run_proxy(FakeTokenManager(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    upstream.shutdown()


def test_proxy_injects_and_overwrites_authorization(proxy_url):
    resp = requests.post(
        f"{proxy_url}/mcp",
        json=_tool_call({"owner": "org-a", "repo": "x"}),
        headers={
            "Authorization": "Bearer client-supplied-should-be-dropped",
            "Mcp-Session-Id": "sess-42",
            "Accept": "application/json, text/event-stream",
        },
        timeout=10,
    )
    assert resp.status_code == 200
    echoed = resp.json()
    assert echoed["authorization"] == "Bearer token-a"
    assert echoed["mcp_session_id"] == "sess-42"
    assert echoed["accept"] == "application/json, text/event-stream"


def test_proxy_routes_by_owner(proxy_url):
    for owner, expected in (("org-b", "token-b"), (None, "token-default")):
        body = _tool_call({"owner": owner} if owner else {})
        resp = requests.post(f"{proxy_url}/mcp", json=body, timeout=10)
        assert resp.json()["authorization"] == f"Bearer {expected}"


def test_proxy_streams_sse(proxy_url):
    start = time.time()
    with requests.post(f"{proxy_url}/sse", json={}, stream=True, timeout=10) as resp:
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        chunks = []
        first_chunk_at = None
        for chunk in resp.iter_content(chunk_size=None):
            if first_chunk_at is None:
                first_chunk_at = time.time()
            chunks.append(chunk)
    assert b"".join(chunks) == b"data: first\n\ndata: second\n\n"
    # first chunk arrived before the stub's 0.8s delay elapsed -> streamed, not buffered
    assert first_chunk_at - start < 0.6


def test_proxy_unreachable_upstream_returns_json_rpc_error(monkeypatch):
    monkeypatch.setattr(proxy_module, "UPSTREAM_BASE", "http://127.0.0.1:1")
    server = run_proxy(FakeTokenManager(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = requests.post(
            f"http://127.0.0.1:{server.server_port}/mcp",
            json=_tool_call({"owner": "org-a"}),
            timeout=10,
        )
        assert resp.status_code == 502
        error = resp.json()
        assert error["id"] == 1
        assert "unreachable" in error["error"]["message"]
    finally:
        server.shutdown()


def test_proxy_health_endpoints(proxy_url):
    assert requests.get(f"{proxy_url}/healthz", timeout=10).status_code == 200
    assert requests.get(f"{proxy_url}/readyz", timeout=10).status_code == 200


def test_proxy_readyz_not_ready(monkeypatch):
    class EmptyManager(FakeTokenManager):
        def installation_count(self):
            return 0

    server = run_proxy(EmptyManager(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert requests.get(f"{base}/readyz", timeout=10).status_code == 503
        assert requests.get(f"{base}/healthz", timeout=10).status_code == 200
    finally:
        server.shutdown()
