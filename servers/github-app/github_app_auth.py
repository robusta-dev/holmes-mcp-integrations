"""GitHub App authentication: installation discovery and token management.

An App JWT (signed with the App's private key) can enumerate every
installation of the App via ``GET /app/installations`` and mint a short-lived
(1 hour) installation access token per installation. Installation tokens are
scoped to a single org/user account, so serving multiple organizations means
holding one token per installation and picking the right one per request.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import jwt
import requests

logger = logging.getLogger(__name__)

# Re-mint an installation token when it has less than this long left to live.
TOKEN_EXPIRY_MARGIN_SEC = 300
# Re-sign the App JWT when it has less than this long left to live.
JWT_EXPIRY_MARGIN_SEC = 60
JWT_LIFETIME_SEC = 600  # 10 minutes (GitHub maximum)

DEFAULT_INSTALLATION_REFRESH_SEC = 300


class TokenMintError(Exception):
    """Raised when GitHub refuses to mint or list installation tokens.

    The message intentionally carries the full API error (status + body) so
    it can be surfaced to the caller/LLM for self-correction.
    """


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def _get_api_base() -> str:
    """Derive the GitHub REST API base URL from the GITHUB_HOST env var.

    Mirrors how github-mcp-server resolves hosts:
    - unset / github.com / api.github.com  -> https://api.github.com
    - *.ghe.com (GHE.com / data residency) -> https://api.<host>
    - any other host (GHES)                 -> https://<host>/api/v3
    """
    host = (os.environ.get("GITHUB_HOST") or "").strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.strip("/").lower()

    if not host or host in ("github.com", "www.github.com", "api.github.com"):
        return "https://api.github.com"
    if host.endswith(".ghe.com"):
        return f"https://{host}" if host.startswith("api.") else f"https://api.{host}"
    return f"https://{host}/api/v3"


def _parse_expires_at(expires_at: str) -> float:
    """Convert GitHub's ISO-8601 expires_at (e.g. 2016-07-11T22:14:10Z) to epoch."""
    return (
        datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


class StaticTokenManager:
    """Degenerate token manager for PAT mode: one static token for everything."""

    def __init__(self, token: str):
        self._token = token

    def resolve_installation(self, owner: Optional[str]) -> Optional[int]:
        return None

    def get_token(self, installation_id: Optional[int]) -> str:
        return self._token

    def installation_count(self) -> int:
        return 1  # always "ready"


class InstallationTokenManager:
    """Discovers GitHub App installations and caches one token per installation.

    Two modes:
    - pinned (``pinned_installation_id`` set): discovery is skipped entirely and
      every request resolves to the pinned installation — identical behavior to
      the historical single-installation setup.
    - auto-discovery: ``GET /app/installations`` builds an owner -> installation
      map, refreshed periodically so new installations appear without restart.
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
        pinned_installation_id: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        self._app_id = app_id
        # Handle literal \n in private key (common in CI/CD and K8s secrets)
        self._private_key = private_key.replace("\\n", "\n")
        self._api_base = api_base or _get_api_base()
        self._pinned = int(pinned_installation_id) if pinned_installation_id else None

        self._lock = threading.Lock()
        self._jwt_cache: Optional[Tuple[str, float]] = None  # (jwt, exp_epoch)
        self._installations: Dict[str, int] = {}  # lowercase login -> installation id
        self._installation_order: list = []  # ids in discovery order
        self._default_installation_id: Optional[int] = None
        self._tokens: Dict[int, Tuple[str, float]] = {}  # id -> (token, exp_epoch)

        default_id = os.environ.get("GITHUB_APP_DEFAULT_INSTALLATION_ID")
        self._env_default_id = int(default_id) if default_id else None
        self._env_default_owner = (
            os.environ.get("GITHUB_APP_DEFAULT_OWNER") or ""
        ).strip().lower() or None

    # -- App JWT ------------------------------------------------------------

    def _app_jwt(self) -> str:
        now = time.time()
        if self._jwt_cache and self._jwt_cache[1] - now > JWT_EXPIRY_MARGIN_SEC:
            return self._jwt_cache[0]
        payload = {
            "iat": int(now) - 60,  # clock-skew guard
            "exp": int(now) + JWT_LIFETIME_SEC,
            "iss": self._app_id,
        }
        encoded = jwt.encode(payload, self._private_key, algorithm="RS256")
        self._jwt_cache = (encoded, now + JWT_LIFETIME_SEC)
        return encoded

    # -- Installation discovery ----------------------------------------------

    def refresh_installations(self) -> None:
        """Rebuild the owner -> installation map. No-op in pinned mode.

        On failure the previous map is kept so a transient GitHub error never
        wipes working state.
        """
        if self._pinned is not None:
            return
        try:
            installations = self._list_installations()
        except Exception:
            logger.warning(
                "Failed to refresh GitHub App installations, keeping previous map",
                exc_info=True,
            )
            return

        owners = {}
        order = []
        for inst in installations:
            login = ((inst.get("account") or {}).get("login") or "").lower()
            inst_id = inst.get("id")
            if not login or not inst_id:
                continue
            owners[login] = inst_id
            order.append(inst_id)

        with self._lock:
            self._installations = owners
            self._installation_order = order
            self._default_installation_id = self._pick_default(owners, order)

        logger.info(
            "Discovered %d GitHub App installation(s): %s (default installation: %s)",
            len(owners),
            ", ".join(sorted(owners)) or "none",
            self._default_installation_id,
        )

    def _list_installations(self) -> list:
        url = f"{self._api_base}/app/installations?per_page=100"
        results = []
        while url:
            response = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {self._app_jwt()}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=30,
            )
            if response.status_code >= 400:
                raise TokenMintError(
                    f"GET {url} failed with HTTP {response.status_code}: {response.text}"
                )
            results.extend(response.json())
            url = response.links.get("next", {}).get("url")
        return results

    def _pick_default(self, owners: Dict[str, int], order: list) -> Optional[int]:
        if self._env_default_id is not None:
            return self._env_default_id
        if self._env_default_owner:
            if self._env_default_owner in owners:
                return owners[self._env_default_owner]
            logger.warning(
                "GITHUB_APP_DEFAULT_OWNER '%s' not found among installations",
                self._env_default_owner,
            )
        return order[0] if order else None

    def installation_count(self) -> int:
        if self._pinned is not None:
            return 1
        with self._lock:
            return len(self._installation_order)

    # -- Resolution + token minting -------------------------------------------

    def resolve_installation(self, owner: Optional[str]) -> int:
        if self._pinned is not None:
            return self._pinned
        with self._lock:
            installations = dict(self._installations)
            default_id = self._default_installation_id
        if owner:
            owner = owner.lower()
            if owner in installations:
                return installations[owner]
            logger.warning(
                "Owner '%s' not in installation map (%s); using default installation %s",
                owner,
                ", ".join(sorted(installations)) or "empty",
                default_id,
            )
        if default_id is None:
            raise TokenMintError(
                "No GitHub App installations discovered — install the App on at "
                "least one organization or user account, or set "
                "GITHUB_APP_INSTALLATION_ID to pin one."
            )
        return default_id

    def get_token(self, installation_id: int) -> str:
        now = time.time()
        with self._lock:
            cached = self._tokens.get(installation_id)
            if cached and cached[1] - now > TOKEN_EXPIRY_MARGIN_SEC:
                return cached[0]
            token, expires_at = self._mint_token(installation_id)
            self._tokens[installation_id] = (token, expires_at)
            return token

    def _mint_token(self, installation_id: int) -> Tuple[str, float]:
        url = f"{self._api_base}/app/installations/{installation_id}/access_tokens"
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self._app_jwt()}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise TokenMintError(
                f"POST {url} failed with HTTP {response.status_code}: {response.text}"
            )
        data = response.json()
        token = data["token"]
        expires_at = _parse_expires_at(data["expires_at"]) if data.get("expires_at") else time.time() + 3600
        logger.info(
            "Minted installation token for installation %s (%s), expires at %s",
            installation_id,
            _mask_token(token),
            data.get("expires_at", "unknown"),
        )
        return token, expires_at

    # -- Background refresh ----------------------------------------------------

    def start_refresh_thread(self) -> Optional[threading.Thread]:
        """Periodically re-discover installations so new orgs appear without a restart."""
        if self._pinned is not None:
            return None
        interval = int(
            os.environ.get(
                "GITHUB_APP_INSTALLATION_REFRESH_SEC",
                str(DEFAULT_INSTALLATION_REFRESH_SEC),
            )
        )

        def _loop():
            while True:
                time.sleep(interval)
                self.refresh_installations()

        thread = threading.Thread(target=_loop, daemon=True, name="installation-refresh")
        thread.start()
        logger.info("Started installation discovery refresh thread (interval: %ds)", interval)
        return thread
