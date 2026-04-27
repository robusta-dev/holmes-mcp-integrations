#!/usr/bin/env python3

import logging
import os
import shlex
import sys

from github_app_auth import setup_github_app_auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    try:
        if not setup_github_app_auth():
            # Fall back: GITHUB_PERSONAL_ACCESS_TOKEN must already be set
            if not os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
                logger.error("No GitHub credentials configured. "
                             "Set GITHUB_APP_ID/GITHUB_APP_INSTALLATION_ID/GITHUB_APP_PRIVATE_KEY "
                             "or GITHUB_PERSONAL_ACCESS_TOKEN.")
                sys.exit(1)
            logger.info("Using existing GITHUB_PERSONAL_ACCESS_TOKEN")

        cmd = os.environ.get("GITHUB_MCP_SERVER_CMD", "github-mcp-server stdio")
        cmd_parts = shlex.split(cmd)

        logger.info("Running: %s", cmd)
        # exec replaces this process with github-mcp-server — no child,
        # no zombie. Token refresh thread is unnecessary in stateless mode
        # since each request spawns a fresh wrapper with a fresh token.
        os.execvp(cmd_parts[0], cmd_parts)

    except Exception as e:
        logger.error("Failed to start: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
