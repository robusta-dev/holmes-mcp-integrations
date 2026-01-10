#!/usr/bin/env python3
"""
Kubernetes Remediation MCP Server

An MCP server that allows running kubectl commands safely.
It runs as a pod inside a Kubernetes cluster, relying on RBAC for namespace/resource restrictions.

Security features:
- Subcommand allowlist (configurable)
- Dangerous flags blocklist
- Shell metacharacter rejection (defense in depth)
- Image allowlist for run_image tool
- Command timeout
- shell=False for subprocess execution
"""

import os
import subprocess
import logging
import re
import json
from typing import Any, Dict, List, Optional, Union

from fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
ALLOWED_COMMANDS = set(
    os.getenv("KUBECTL_ALLOWED_COMMANDS", "get,describe,logs,edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate").split(",")
)

DANGEROUS_FLAGS = set(
    os.getenv(
        "KUBECTL_DANGEROUS_FLAGS",
        "--kubeconfig,--context,--cluster,--user,--token,--as,--as-group,--as-uid"
    ).split(",")
)

TIMEOUT = int(os.getenv("KUBECTL_TIMEOUT", "60"))

ALLOWED_IMAGES = set(
    filter(None, os.getenv("KUBECTL_ALLOWED_IMAGES", "").split(","))
)

# Shell metacharacters to reject (defense in depth)
SHELL_CHARS = set(";|&$`\\'\"\n\r")

# Characters that could be used for flag injection
FLAG_INJECTION_CHARS = set("=")

# Create MCP server
mcp = FastMCP(
    name="kubernetes-remediation",
    version="1.0.0"
)


def validate_args(args: List[str]) -> List[str]:
    """
    Validate kubectl arguments for security.

    Raises ValueError if validation fails.
    Returns the validated args (with 'kubectl' stripped if present).
    """
    if not args:
        raise ValueError("No arguments provided")

    # Strip 'kubectl' if it's the first argument
    if args[0] == "kubectl":
        args = args[1:]

    if not args:
        raise ValueError("No command provided after 'kubectl'")

    # Check subcommand is allowed
    command = args[0]
    if command not in ALLOWED_COMMANDS:
        raise ValueError(
            f"Command '{command}' not allowed. "
            f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}"
        )

    # Check for dangerous flags and shell metacharacters
    for arg in args:
        # Extract flag name (handle --flag=value format)
        flag = arg.split("=")[0]

        if flag in DANGEROUS_FLAGS:
            raise ValueError(f"Flag '{flag}' is not permitted")

        # Block --overrides flag (privilege escalation risk)
        if flag == "--overrides":
            raise ValueError("Flag '--overrides' is not permitted")

        # Reject shell metacharacters (defense in depth)
        if any(c in arg for c in SHELL_CHARS):
            raise ValueError(f"Invalid characters in argument: {arg}")

    return args


def validate_pod_name(name: str) -> None:
    """
    Validate pod name according to Kubernetes naming conventions.

    Must be alphanumeric with hyphens, cannot start with a hyphen.
    """
    if not name:
        raise ValueError("Pod name cannot be empty")

    if name.startswith("-"):
        raise ValueError("Pod name cannot start with a hyphen")

    if not all(c.isalnum() or c == "-" for c in name):
        raise ValueError(
            f"Invalid pod name '{name}': must contain only alphanumeric characters and hyphens"
        )

    # Additional Kubernetes naming constraints
    if len(name) > 253:
        raise ValueError("Pod name cannot exceed 253 characters")

    if not re.match(r'^[a-z0-9]', name):
        raise ValueError("Pod name must start with a lowercase letter or number")


def validate_image(image: str) -> None:
    """
    Validate image is in the allowed list.
    """
    if not ALLOWED_IMAGES:
        raise ValueError(
            "run_image tool is disabled: KUBECTL_ALLOWED_IMAGES not configured"
        )

    if image not in ALLOWED_IMAGES:
        raise ValueError(
            f"Image '{image}' not allowed. "
            f"Allowed images: {', '.join(sorted(ALLOWED_IMAGES))}"
        )


def validate_command_args(command: List[str]) -> None:
    """
    Validate command arguments for shell metacharacters.
    """
    for arg in command:
        if any(c in arg for c in SHELL_CHARS):
            raise ValueError(f"Invalid characters in command argument: {arg}")


def run_kubectl(args: List[str]) -> Dict[str, Any]:
    """
    Execute kubectl with the given arguments.

    Uses shell=False for security.
    """
    try:
        logger.info(f"Executing kubectl with args: {args}")

        result = subprocess.run(
            ["kubectl"] + args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }

    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {TIMEOUT}s: kubectl {' '.join(args)}")
        return {
            "success": False,
            "error": f"Command timed out after {TIMEOUT} seconds",
            "stdout": "",
            "stderr": ""
        }
    except Exception as e:
        logger.error(f"Error executing kubectl: {e}")
        return {
            "success": False,
            "error": str(e),
            "stdout": "",
            "stderr": ""
        }


def parse_args(args: Union[str, List[str]]) -> List[str]:
    """
    Parse args from either a JSON string or a list.

    Handles cases where the client accidentally sends a JSON-encoded string
    instead of an actual array.
    """
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, list):
                return parsed
            else:
                raise ValueError(f"Expected array, got {type(parsed).__name__}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in args string: {e}")
    return args


@mcp.tool(
    name="kubectl",
    description="Execute a kubectl command with validated arguments. "
                "The command is validated against an allowlist of safe subcommands "
                "and dangerous flags are blocked."
)
def kubectl(args: Union[str, List[str]]) -> Dict[str, Any]:
    """
    Execute a kubectl command with validated arguments.

    Args:
        args: Command arguments, e.g. ["get", "pods", "-n", "default"]
              or ["kubectl", "get", "pods"]
              Can also be a JSON string: '["get", "pods"]'

    Returns:
        Dictionary with success status, stdout, stderr
    """
    try:
        parsed_args = parse_args(args)
        validated_args = validate_args(parsed_args)
        return run_kubectl(validated_args)
    except ValueError as e:
        logger.warning(f"Validation failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="run_image",
    description="Run a pod with a pre-approved image. This tool provides a safer way "
                "to run containers by restricting images to an allowlist."
)
def run_image(
    name: str,
    image: str,
    namespace: Optional[str] = None,
    command: Optional[Union[str, List[str]]] = None,
    rm: bool = True
) -> Dict[str, Any]:
    """
    Run a pod with a pre-approved image.

    Args:
        name: Pod name (required)
        image: Image to run, must be in allowed list (required)
        namespace: Optional namespace
        command: Optional command to run in container (list or JSON string)
        rm: Delete pod after exit (default: True)

    Returns:
        Dictionary with success status, stdout, stderr
    """
    try:
        # Validate inputs
        validate_pod_name(name)
        validate_image(image)

        # Parse command if it's a JSON string
        parsed_command = None
        if command:
            parsed_command = parse_args(command) if isinstance(command, str) else command
            validate_command_args(parsed_command)

        # Build kubectl run command
        kubectl_args = ["run", name, f"--image={image}", "--restart=Never"]

        if namespace:
            # Validate namespace doesn't have shell chars
            if any(c in namespace for c in SHELL_CHARS):
                raise ValueError(f"Invalid characters in namespace: {namespace}")
            kubectl_args.extend(["-n", namespace])

        if rm:
            kubectl_args.extend(["--rm", "-i"])

        # Add command if provided
        if parsed_command:
            kubectl_args.append("--")
            kubectl_args.extend(parsed_command)

        logger.info(f"Running pod with args: {kubectl_args}")

        result = subprocess.run(
            ["kubectl"] + kubectl_args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }

    except subprocess.TimeoutExpired:
        logger.error(f"run_image command timed out after {TIMEOUT}s")
        return {
            "success": False,
            "error": f"Command timed out after {TIMEOUT} seconds",
            "stdout": "",
            "stderr": ""
        }
    except ValueError as e:
        logger.warning(f"Validation failed for run_image: {e}")
        return {
            "success": False,
            "error": str(e)
        }
    except Exception as e:
        logger.error(f"Error in run_image: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="get_config",
    description="Get the current configuration of the MCP server (allowed commands, "
                "images, timeout, etc.) for debugging purposes."
)
def get_config() -> Dict[str, Any]:
    """
    Get the current server configuration.

    Returns:
        Dictionary with current configuration
    """
    return {
        "allowed_commands": sorted(ALLOWED_COMMANDS),
        "dangerous_flags": sorted(DANGEROUS_FLAGS),
        "timeout_seconds": TIMEOUT,
        "allowed_images": sorted(ALLOWED_IMAGES) if ALLOWED_IMAGES else [],
        "run_image_enabled": bool(ALLOWED_IMAGES)
    }


# Main entry point
if __name__ == "__main__":
    import sys
    import uvicorn

    # Log configuration
    logger.info("Starting Kubernetes Remediation MCP Server")
    logger.info(f"Allowed commands: {sorted(ALLOWED_COMMANDS)}")
    logger.info(f"Dangerous flags: {sorted(DANGEROUS_FLAGS)}")
    logger.info(f"Timeout: {TIMEOUT}s")
    logger.info(f"Allowed images: {sorted(ALLOWED_IMAGES) if ALLOWED_IMAGES else 'None (run_image disabled)'}")

    # Parse command line arguments for transport mode
    if "--transport" in sys.argv and "http" in sys.argv:
        # Run in HTTP mode
        logger.info("Starting in HTTP transport mode")
        host = "0.0.0.0"
        port = 8000

        # Parse host and port from command line if provided
        if "--host" in sys.argv:
            host_idx = sys.argv.index("--host") + 1
            if host_idx < len(sys.argv):
                host = sys.argv[host_idx]

        if "--port" in sys.argv:
            port_idx = sys.argv.index("--port") + 1
            if port_idx < len(sys.argv):
                port = int(sys.argv[port_idx])

        # Run with uvicorn for HTTP transport
        uvicorn.run(
            mcp.http_app(),
            host=host,
            port=port,
            log_level="info"
        )
    else:
        # Default to stdio
        mcp.run()
