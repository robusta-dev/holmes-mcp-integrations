#!/usr/bin/env python3
"""
Multi-account AWS MCP server wrapper.
Intercepts AWS CLI commands and switches credentials based on --profile flag.
"""

import os
import sys
import asyncio
import json
import subprocess
import logging
from typing import Any, Dict, List

from aws_auth import AWSMultiAccountAuth

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize auth
try:
    auth = AWSMultiAccountAuth()
    logger.info(f"Initialized auth with profiles: {', '.join(auth.list_profiles())}")
except Exception as e:
    logger.error(f"Failed to initialize auth: {e}")
    sys.exit(1)

# Import MCP components
from mcp.server import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolRequest, CallToolResult

def extract_profile_from_command(command: str) -> tuple[str, str]:
    """Extract profile from AWS CLI command and return (command_without_profile, profile)."""
    parts = command.split()
    profile = None
    filtered_parts = []
    
    i = 0
    while i < len(parts):
        if parts[i] == '--profile' and i + 1 < len(parts):
            profile = parts[i + 1]
            i += 2  # Skip both --profile and the profile name
        else:
            filtered_parts.append(parts[i])
            i += 1
    
    return ' '.join(filtered_parts), profile

def execute_aws_command(command: str) -> Dict[str, Any]:
    """Execute AWS CLI command with profile-specific credentials."""
    try:
        # Extract profile from command
        clean_command, profile = extract_profile_from_command(command)
        
        logger.info(f"Executing: {command}")
        if profile:
            logger.info(f"Using profile: {profile}")
        
        # Get environment for the specified profile
        env = os.environ.copy()
        if profile:
            try:
                profile_env = auth.get_aws_env_vars(profile)
                env.update(profile_env)
                logger.debug(f"Updated environment for profile: {profile}")
            except Exception as e:
                logger.error(f"Failed to get credentials for profile {profile}: {e}")
                return {
                    "response": {
                        "error": f"Failed to authenticate with profile '{profile}': {str(e)}",
                        "status_code": 401
                    }
                }
        
        # Execute the command
        result = subprocess.run(
            clean_command.split(),
            capture_output=True,
            text=True,
            timeout=300,
            env=env
        )
        
        # Format response like the original AWS MCP server
        response_data = {
            "error": None,
            "status_code": result.returncode,
            "error_code": None,
            "pagination_token": None,
            "json": result.stdout if result.returncode == 0 else None
        }
        
        if result.returncode != 0:
            response_data["error"] = result.stderr
            response_data["error_code"] = "CommandFailed"
        
        return {
            "response": response_data,
            "metadata": {
                "service": "aws-cli",
                "operation": clean_command.split()[1] if len(clean_command.split()) > 1 else "unknown",
                "region_name": env.get("AWS_DEFAULT_REGION", "us-east-2"),
                "profile": profile
            },
            "validation_failures": None,
            "missing_context_failures": None,
            "failed_constraints": []
        }
        
    except subprocess.TimeoutExpired:
        return {
            "response": {
                "error": "Command timed out after 300 seconds",
                "status_code": 408,
                "error_code": "Timeout"
            }
        }
    except Exception as e:
        return {
            "response": {
                "error": str(e),
                "status_code": 500,
                "error_code": "InternalError"
            }
        }

# Create MCP server
server = Server("multi-account-aws-mcp-server")

@server.list_tools()
async def list_tools() -> List[Tool]:
    """List available tools."""
    return [
        Tool(
            name="call_aws",
            description="Execute AWS CLI commands with multi-account support using --profile flag",
            inputSchema={
                "type": "object",
                "properties": {
                    "cli_command": {
                        "type": "string",
                        "description": "The complete AWS CLI command to execute. MUST start with 'aws'. Use --profile dev or --profile prod for multi-account access."
                    },
                    "max_results": {
                        "type": "string",
                        "description": "Optional limit for number of results (useful for pagination)"
                    }
                },
                "required": ["cli_command"]
            }
        ),
        Tool(
            name="suggest_aws_commands",
            description="Suggest AWS CLI commands based on a natural language query",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A natural language description of what you want to do in AWS"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="test_multi_account_auth",
            description="Test authentication for all configured profiles",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "string",
                        "description": "Specific profile to test (optional). If not provided, tests all profiles."
                    }
                }
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls."""
    
    if name == "call_aws":
        cli_command = arguments.get("cli_command", "")
        max_results = arguments.get("max_results")
        
        if not cli_command.startswith('aws '):
            result = {
                "response": {
                    "error": "Command must start with 'aws'",
                    "status_code": 400
                },
                "provided": cli_command
            }
        else:
            # Add max_results if specified and not already present
            if max_results and '--max-results' not in cli_command and '--max-items' not in cli_command:
                cli_command += f' --max-results {max_results}'
            
            result = execute_aws_command(cli_command)
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "suggest_aws_commands":
        query = arguments.get("query", "")
        
        # Simple suggestions (you can enhance this)
        suggestions = []
        query_lower = query.lower()
        
        if 'list' in query_lower or 'show' in query_lower:
            if 'instance' in query_lower:
                suggestions.append({
                    "command": "aws ec2 describe-instances --profile dev",
                    "description": "List EC2 instances in dev account"
                })
                suggestions.append({
                    "command": "aws ec2 describe-instances --profile prod",
                    "description": "List EC2 instances in prod account"
                })
        
        result = {
            "query": query,
            "suggestions": suggestions,
            "available_profiles": auth.list_profiles(),
            "usage": "Always include --profile dev or --profile prod in your AWS commands"
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "test_multi_account_auth":
        profile = arguments.get("profile")
        results = {}
        
        profiles_to_test = [profile] if profile else auth.list_profiles()
        
        for profile_name in profiles_to_test:
            try:
                result = auth.test_auth(profile_name)
                results[profile_name] = result
            except Exception as e:
                results[profile_name] = {
                    "success": False,
                    "error": str(e)
                }
        
        return [TextContent(type="text", text=json.dumps(results, indent=2))]
    
    else:
        error_result = {"error": f"Unknown tool: {name}"}
        return [TextContent(type="text", text=json.dumps(error_result))]

async def main():
    """Main function to run the MCP server."""
    logger.info("Starting Multi-Account AWS MCP Server...")
    
    # Test auth on startup
    logger.info("Testing authentication on startup...")
    for profile_name in auth.list_profiles():
        try:
            result = auth.test_auth(profile_name)
            if result['success']:
                logger.info(f"✓ {profile_name}: Account {result['account']}")
            else:
                logger.warning(f"✗ {profile_name}: {result['error']}")
        except Exception as e:
            logger.error(f"✗ {profile_name}: {e}")
    
    # Run the MCP server using stdio
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
