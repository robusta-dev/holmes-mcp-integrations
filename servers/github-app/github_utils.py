import logging
import os
import re
import threading
import time
from types import SimpleNamespace

from github import Auth, GithubIntegration
from github.GithubException import GithubException

OWNER_KEYS = ("owner", "org", "organization", "username", "user")
QUALIFIER = re.compile(r"\b(?:org|user|owner):([\w-]+)|\brepo:([\w-]+)/")
EXPIRY_MARGIN_SEC = 300
REDISCOVERY_COOLDOWN_SEC = 60


class TokenError(Exception):
    pass


def extract_owner(body):
    if isinstance(body, list):
        return next((owner for owner in map(extract_owner, body) if owner), None)
    if not isinstance(body, dict) or body.get("method") != "tools/call":
        return None
    params = body.get("params")
    args = params.get("arguments") if isinstance(params, dict) else None
    if not isinstance(args, dict):
        return None
    for key in OWNER_KEYS:
        if isinstance(args.get(key), str) and args[key].strip():
            return args[key].strip().split("/")[0].lower()
    for key in ("query", "q"):
        if isinstance(args.get(key), str) and (found := QUALIFIER.search(args[key])):
            return (found.group(1) or found.group(2)).lower()
    return None


def api_base():
    host = (os.environ.get("GITHUB_HOST") or "").split("://")[-1].strip("/ ").lower()
    if not host or host in ("github.com", "www.github.com", "api.github.com"):
        return "https://api.github.com"
    if host.endswith(".ghe.com"):
        return f"https://{host}" if host.startswith("api.") else f"https://api.{host}"
    return f"https://{host}/api/v3"


def static_token(token):
    return SimpleNamespace(token_for=lambda owner=None: token, ready=lambda: True)


class AppTokens:
    def __init__(self, app_id, private_key, pinned=None):
        self._api = GithubIntegration(
            auth=Auth.AppAuth(app_id, private_key.replace("\\n", "\n")),
            base_url=api_base(), per_page=100)
        self._pinned = int(pinned) if pinned else None
        self._lock = threading.Lock()
        self._installations, self._tokens, self._discovered_at = {}, {}, 0.0

    def ready(self):
        return bool(self._pinned or self._installations)

    def discover(self):
        if self._pinned:
            return
        try:
            found = {login.lower(): entry.id for entry in self._api.get_installations()
                     if (login := (entry.raw_data.get("account") or {}).get("login"))}
        except Exception:
            logging.warning("Installation discovery failed, keeping previous map", exc_info=True)
            return
        with self._lock:
            self._installations, self._discovered_at = found, time.time()
        logging.info("Serving installations: %s", ", ".join(sorted(found)) or "none")

    def token_for(self, owner):
        owner = (owner or "").lower()
        if not self._pinned and owner not in self._installations \
                and time.time() - self._discovered_at > REDISCOVERY_COOLDOWN_SEC:
            self.discover()
        with self._lock:
            installation = (self._pinned or self._installations.get(owner)
                            or next(iter(self._installations.values()), None))
            if not installation:
                raise TokenError("No GitHub App installations found. Install the App on an "
                                 "organization, or set GITHUB_APP_INSTALLATION_ID.")
            token, expires_at = self._tokens.get(installation, (None, 0.0))
            if expires_at - time.time() > EXPIRY_MARGIN_SEC:
                return token
            try:
                minted = self._api.get_access_token(installation)
            except GithubException as e:
                raise TokenError(f"Cannot mint a token for installation {installation}: {e}") from e
            self._tokens[installation] = (minted.token, minted.expires_at.timestamp())
            logging.info("Minted a token for installation %s, expiring %s",
                         installation, minted.expires_at)
            return minted.token
