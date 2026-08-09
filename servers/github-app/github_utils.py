"""All GitHub-internal logic for the multi-org MCP proxy in one place.

Covers three concerns:

1. GitHub App credentials — delegated to PyGithub (``Auth.AppAuth`` +
   ``GithubIntegration``), which handles App JWT signing, installation
   enumeration (with pagination) and installation-token minting natively.
2. Installation routing — the owner -> installation map, default-installation
   selection and a small per-installation token cache (PyGithub's
   ``get_access_token`` does not cache, and its auto-refreshing
   ``AppInstallationAuth`` only works bound to a ``Github`` client, not as a
   raw-token source for proxy headers).
3. Owner extraction — pulling the target org/user out of an MCP JSON-RPC
   ``tools/call`` body so the proxy can pick the right installation token.

Background: an App JWT can enumerate every installation of the App via
``GET /app/installations`` and mint a short-lived (1 hour) installation access
token per installation. Installation tokens are scoped to a single org/user
account, so serving multiple organizations means holding one token per
installation and picking the right one per request.
"""

import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple

from github import Auth, GithubIntegration
from github.GithubException import GithubException

logger = logging.getLogger(__name__)

# Re-mint an installation token when it has less than this long left to live.
TOKEN_EXPIRY_MARGIN_SEC = 300

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


def get_api_base() -> str:
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


# ---------------------------------------------------------------------------
# Owner extraction from MCP JSON-RPC bodies
# ---------------------------------------------------------------------------

# Argument names used by github-mcp-server tools to identify an account,
# in priority order. Re-check this list when bumping the pinned binary version.
OWNER_ARG_KEYS = ("owner", "org", "organization", "username", "user")

# Search tools carry the account inside a free-text query argument.
QUERY_ARG_KEYS = ("query", "q")

# org:NAME / user:NAME / owner:NAME / repo:OWNER/NAME — first hit wins.
# GitHub logins: alphanumerics and inner hyphens.
_QUALIFIER_RE = re.compile(
    r"\b(?:org|user|owner):([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)"
    r"|\brepo:([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
)


def _owner_from_arguments(arguments: Any) -> Optional[str]:
    if not isinstance(arguments, dict):
        return None
    for key in OWNER_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            # Tolerate "owner/repo" pasted into an owner-only field.
            return value.strip().split("/", 1)[0].lower()
    for key in QUERY_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            match = _QUALIFIER_RE.search(value)
            if match:
                return (match.group(1) or match.group(2)).lower()
    return None


def extract_owner(body: Any) -> Optional[str]:
    """Return the GitHub account login targeted by a JSON-RPC message, or None.

    Only ``tools/call`` requests carry a target account; everything else
    (initialize, tools/list, notifications) returns None and is served with
    the default installation. Batch arrays are handled by taking the first
    tools/call element.
    """
    if isinstance(body, list):
        for element in body:
            owner = extract_owner(element)
            if owner:
                return owner
        return None
    if not isinstance(body, dict) or body.get("method") != "tools/call":
        return None
    params = body.get("params")
    if not isinstance(params, dict):
        return None
    return _owner_from_arguments(params.get("arguments"))


# ---------------------------------------------------------------------------
# Token managers
# ---------------------------------------------------------------------------


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

    Credential mechanics (JWT signing, installation listing, token minting)
    are delegated to PyGithub's ``GithubIntegration``.

    Two modes:
    - pinned (``pinned_installation_id`` set): discovery is skipped entirely and
      every request resolves to the pinned installation — identical behavior to
      the historical single-installation setup.
    - auto-discovery: ``get_installations()`` builds an owner -> installation
      map, refreshed periodically so new installations appear without restart.
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
        pinned_installation_id: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        # Handle literal \n in private key (common in CI/CD and K8s secrets)
        auth = Auth.AppAuth(app_id, private_key.replace("\\n", "\n"))
        self._integration = GithubIntegration(
            auth=auth, base_url=api_base or get_api_base(), per_page=100
        )
        self._pinned = int(pinned_installation_id) if pinned_installation_id else None

        self._lock = threading.Lock()
        self._installations: Dict[str, int] = {}  # lowercase login -> installation id
        self._installation_order: list = []  # ids in discovery order
        self._default_installation_id: Optional[int] = None
        self._tokens: Dict[int, Tuple[str, float]] = {}  # id -> (token, exp_epoch)

        default_id = os.environ.get("GITHUB_APP_DEFAULT_INSTALLATION_ID")
        self._env_default_id = int(default_id) if default_id else None
        self._env_default_owner = (
            os.environ.get("GITHUB_APP_DEFAULT_OWNER") or ""
        ).strip().lower() or None

    # -- Installation discovery ----------------------------------------------

    def refresh_installations(self) -> None:
        """Rebuild the owner -> installation map. No-op in pinned mode.

        On failure the previous map is kept so a transient GitHub error never
        wipes working state.
        """
        if self._pinned is not None:
            return
        owners: Dict[str, int] = {}
        order: list = []
        try:
            for inst in self._integration.get_installations():
                # raw_data instead of inst.account: PyGithub only populates the
                # account property when target_type maps to User/Organization.
                login = ((inst.raw_data.get("account") or {}).get("login") or "").lower()
                if not login or not inst.id:
                    continue
                owners[login] = inst.id
                order.append(inst.id)
        except Exception:
            logger.warning(
                "Failed to refresh GitHub App installations, keeping previous map",
                exc_info=True,
            )
            return

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
            try:
                authorization = self._integration.get_access_token(installation_id)
            except GithubException as e:
                # str(GithubException) carries status + API body for the LLM.
                raise TokenMintError(
                    f"Minting an access token for installation {installation_id} "
                    f"failed: {e}"
                ) from e
            token = authorization.token
            expires_at = (
                authorization.expires_at.timestamp()
                if authorization.expires_at
                else time.time() + 3600
            )
            self._tokens[installation_id] = (token, expires_at)
            logger.info(
                "Minted installation token for installation %s (%s), expires at %s",
                installation_id,
                _mask_token(token),
                authorization.expires_at or "unknown",
            )
            return token

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
