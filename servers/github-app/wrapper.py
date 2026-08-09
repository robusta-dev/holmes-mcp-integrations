#!/usr/bin/env python3
"""Entrypoint: token-routing proxy in front of github-mcp-server http.

Modes (checked in order):

1. GitHub App auth — GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY set.
   - GITHUB_APP_INSTALLATION_ID also set: pinned to that single installation
     (legacy single-org behavior, discovery disabled).
   - GITHUB_APP_INSTALLATION_ID unset: all installations of the App are
     auto-discovered and requests are routed to the right org's token by the
     tool call's owner/org argument.
2. PAT fallback — GITHUB_PERSONAL_ACCESS_TOKEN set: same proxy, static token.

The github-mcp-server binary runs in native http mode bound to localhost; the
proxy on port 8000 is the only externally reachable listener and owns the
Authorization header of every upstream request.
"""

import logging
import os
import signal
import subprocess
import sys
import threading

from github_utils import InstallationTokenManager, StaticTokenManager
from proxy import LISTEN_PORT, UPSTREAM_PORT, run_proxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def build_token_manager():
    app_id = os.environ.get("GITHUB_APP_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
    pat = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")

    if os.environ.get("GITHUB_APP_TOKEN_REFRESH_INTERVAL_SEC"):
        logger.warning(
            "GITHUB_APP_TOKEN_REFRESH_INTERVAL_SEC is deprecated and ignored — "
            "tokens are now minted on demand and cached until shortly before expiry"
        )

    if app_id and private_key:
        manager = InstallationTokenManager(
            app_id=app_id,
            private_key=private_key,
            pinned_installation_id=installation_id,
        )
        if installation_id:
            # Legacy pin mode: validate credentials by minting one token now.
            manager.get_token(int(installation_id))
            logger.info(
                "GitHub App auth pinned to installation %s (discovery disabled)",
                installation_id,
            )
        else:
            # Multi-org mode: fail fast if the App JWT is rejected outright.
            manager.refresh_installations()
            if manager.installation_count() == 0:
                logger.warning(
                    "No installations discovered yet — install the GitHub App on "
                    "at least one organization or user account. Will keep retrying."
                )
            manager.start_refresh_thread()
        return manager

    if pat:
        logger.info("Using GITHUB_PERSONAL_ACCESS_TOKEN (static token mode)")
        return StaticTokenManager(pat)

    missing = []
    if not app_id:
        missing.append("GITHUB_APP_ID")
    if not private_key:
        missing.append("GITHUB_APP_PRIVATE_KEY")
    logger.error(
        "No GitHub credentials configured. For GitHub App auth set %s "
        "(GITHUB_APP_INSTALLATION_ID is optional — omit it for multi-org "
        "auto-discovery). Alternatively set GITHUB_PERSONAL_ACCESS_TOKEN.",
        " and ".join(missing),
    )
    sys.exit(1)


def start_upstream() -> subprocess.Popen:
    # GITHUB_TOOLSETS / GITHUB_TOOLS / GITHUB_HOST are inherited from the pod
    # env. GITHUB_PERSONAL_ACCESS_TOKEN is intentionally NOT required by the
    # binary in http mode — auth arrives per request via Authorization headers
    # set by the proxy.
    cmd = [
        "github-mcp-server",
        "http",
        "--port",
        str(UPSTREAM_PORT),
        "--listen-host",
        "127.0.0.1",
    ]
    logger.info("Starting upstream: %s", " ".join(cmd))
    return subprocess.Popen(cmd)


def main():
    token_manager = build_token_manager()
    upstream = start_upstream()

    server = run_proxy(token_manager)
    shutting_down = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Received signal %s, shutting down", signum)
        shutting_down.set()
        upstream.terminate()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    def _watch_upstream():
        code = upstream.wait()
        if shutting_down.is_set():
            return
        # Not a deliberate shutdown: die loudly so Kubernetes restarts the
        # pod instead of leaving a proxy with no backend.
        logger.error("github-mcp-server exited with code %s", code)
        os._exit(1)

    threading.Thread(target=_watch_upstream, daemon=True, name="upstream-watch").start()

    try:
        server.serve_forever()
    finally:
        upstream.terminate()
        try:
            upstream.wait(timeout=10)
        except subprocess.TimeoutExpired:
            upstream.kill()


if __name__ == "__main__":
    main()
