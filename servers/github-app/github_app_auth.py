import logging
import os
import threading
import time

import jwt
import requests

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SEC = int(os.environ.get("GITHUB_APP_TOKEN_REFRESH_INTERVAL_SEC", "1800"))


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def _generate_jwt(app_id: str, private_key: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,  # 10 minutes (GitHub maximum)
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _exchange_jwt_for_token(encoded_jwt: str, installation_id: str) -> dict:
    response = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {encoded_jwt}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def refresh_token(app_id: str, installation_id: str, private_key: str) -> str:
    """Generate a new GitHub installation access token and set it as GITHUB_PERSONAL_ACCESS_TOKEN."""
    encoded_jwt = _generate_jwt(app_id, private_key)
    data = _exchange_jwt_for_token(encoded_jwt, installation_id)
    token = data["token"]

    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
    logger.info(
        "GitHub App token refreshed (%s), expires at %s",
        _mask_token(token),
        data.get("expires_at", "unknown"),
    )
    return token


def _refresh_loop(app_id: str, installation_id: str, private_key: str):
    while True:
        time.sleep(REFRESH_INTERVAL_SEC)
        try:
            refresh_token(app_id, installation_id, private_key)
        except Exception:
            logger.warning("Background refresh failed, will retry", exc_info=True)


def setup_github_app_auth():
    """Generate an installation token and start background refresh.

    Reads GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, and GITHUB_APP_PRIVATE_KEY
    from environment. Sets GITHUB_PERSONAL_ACCESS_TOKEN so the github-mcp-server
    binary can authenticate with GitHub.
    """
    app_id = os.environ.get("GITHUB_APP_ID")
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")

    if not all([app_id, installation_id, private_key]):
        logger.info("GitHub App env vars not set, skipping App auth setup")
        return False

    # Handle literal \n in private key (common in CI/CD and K8s secrets)
    private_key = private_key.replace("\\n", "\n")

    token = refresh_token(app_id, installation_id, private_key)
    logger.info("Initial token generated (%s)", _mask_token(token))

    thread = threading.Thread(
        target=_refresh_loop,
        args=(app_id, installation_id, private_key),
        daemon=True,
    )
    thread.start()
    logger.info("Started token refresh thread (interval: %ds)", REFRESH_INTERVAL_SEC)

    return True
