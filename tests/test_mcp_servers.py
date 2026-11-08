"""Build each MCP server's Dockerfile, run it, and call one read-only tool.

Secrets come from MCP_TEST_* env vars; a server whose secrets are missing is skipped (failed under CI).
Run one server with: pytest tests -k github-app
"""
import json
import os
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mcp_tester import call_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
GITHUB_TEST_COMMITS = ("list_commits", {"owner": "kotlickya", "repo": "argo-repo", "perPage": 1}, "sha")


@dataclass
class ServerCase:
    name: str
    tool: str
    arguments: dict
    expect: str
    container_env: dict = field(default_factory=dict)  # container var -> MCP_TEST_* secret
    token_secret: str = ""  # MCP_TEST_* secret sent as the Bearer token
    path: str = "/"


SERVERS = [
    ServerCase("github", *GITHUB_TEST_COMMITS, token_secret="MCP_TEST_GITHUB_PAT"),
    ServerCase("github-app", *GITHUB_TEST_COMMITS, container_env={
        "GITHUB_APP_ID": "MCP_TEST_GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY": "MCP_TEST_GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_INSTALLATION_ID": "MCP_TEST_GITHUB_APP_INSTALLATION_ID",
    }),
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def required_secrets(case):
    return [*case.container_env.values(), *([case.token_secret] if case.token_secret else [])]


@pytest.fixture(params=SERVERS, ids=lambda case: case.name)
def server(request):
    case = request.param
    missing = [secret for secret in required_secrets(case) if not os.environ.get(secret)]
    if missing and os.environ.get("CI"):
        pytest.fail(f"missing secrets: {', '.join(missing)}")
    if missing:
        pytest.skip(f"missing secrets: {', '.join(missing)}")

    image = f"mcp-test-{case.name}"
    subprocess.run(["docker", "build", "-q", "-t", image, str(REPO_ROOT / "servers" / case.name)],
                   check=True, stdout=subprocess.DEVNULL)
    port = free_port()
    env_flags = [flag for var in case.container_env for flag in ("-e", var)]
    container_env = {var: os.environ[secret] for var, secret in case.container_env.items()}
    container = subprocess.run(["docker", "run", "-d", "-p", f"{port}:8000", *env_flags, image],
                               check=True, capture_output=True, text=True,
                               env=os.environ | container_env).stdout.strip()
    yield case, f"http://localhost:{port}{case.path}"
    if request.node.rep_call.failed:
        subprocess.run(["docker", "logs", "--tail", "50", container])
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


def test_read_only_tool(server):
    case, url = server
    result = call_tool(url, case.tool, case.arguments, os.environ.get(case.token_secret))
    assert case.expect in json.dumps(result), f"{case.tool} result does not contain {case.expect!r}"
