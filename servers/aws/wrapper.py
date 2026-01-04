#!/usr/bin/env python3

import os
import sys
import logging
import shlex

from aws_auth import setup_aws_profiles, has_valid_config, config_file_exists, AWS_ACCOUNT_ROLES_FILE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    try:
        if not config_file_exists():
            logger.info("No custom aws profile file found")
        elif has_valid_config():
            profiles = setup_aws_profiles()
            logger.info(f"Set up {len(profiles)} AWS profiles: {', '.join(profiles)}")
        else:
            logger.error(f"Custom config file {AWS_ACCOUNT_ROLES_FILE} invalid format, skipping profile setup")
        
        cmd = os.environ.get('AWS_MCP_SERVER_CMD', 'python3 -m awslabs.aws_api_mcp_server')
        cmd_parts = shlex.split(cmd)
        
        logger.info(f"Running: {cmd}")
        os.execvp(cmd_parts[0], cmd_parts)
        
    except Exception as e:
        logger.error(f"Failed to start: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
