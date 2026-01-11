#!/usr/bin/env python3

import os
import sys
import logging
import shlex
import subprocess

from aws_auth import setup_aws_profiles, has_valid_config, config_file_exists, AWS_ACCOUNT_ROLES_FILE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    try:
        setup_aws_profiles()
        
        cmd = os.environ.get('AWS_MCP_SERVER_CMD', 'python3 -m awslabs.aws_api_mcp_server')
        cmd_parts = shlex.split(cmd)
        
        logger.info(f"Running: {cmd}")
        # Use subprocess to keep wrapper alive so refresh thread continues
        proc = subprocess.Popen(cmd_parts)
        sys.exit(proc.wait())
        
    except Exception as e:
        logger.error(f"Failed to start: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
