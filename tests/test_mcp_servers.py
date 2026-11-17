"""Build each MCP server's Dockerfile, run it, and call one read-only tool.

Secrets come from MCP_TEST_* env vars; a server whose secrets are missing is skipped (failed under CI).
Servers without test credentials yet run against a stand-in provider (see `mock`).
Run one server with: pytest tests -k github-app
"""
import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mcp_tester import HttpSession, SseSession, call_tool

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
TOPAZ_IMAGE = "thecloudtheory/topaz-host:v1.10.222-preview"
MOCK_PYTHON_IMAGE = "python:3.13-alpine"
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
    transport: str = "http"
    mock: str = ""  # fixture starting a stand-in provider on the test network
    command: list = field(default_factory=list)  # replaces the image CMD
    entrypoint: str = ""  # script in tests/ run before the image's own CMD


SERVERS = [
    ServerCase("github", *GITHUB_TEST_COMMITS, token_secret="MCP_TEST_GITHUB_PAT"),
    ServerCase("github-app", *GITHUB_TEST_COMMITS, container_env={
        "GITHUB_APP_ID": "MCP_TEST_GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY": "MCP_TEST_GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_INSTALLATION_ID": "MCP_TEST_GITHUB_APP_INSTALLATION_ID",
    }),
    # TODO: use a free Sentry Developer-plan org token (MCP_TEST_SENTRY_AUTH_TOKEN) instead of fake_sentry.py
    ServerCase("sentry", "find_organizations", {}, "mcp-test-org", path="/sse", transport="sse",
               mock="fake_sentry", command=["--port", "8000", "--stdio",
                                            "sentry-mcp --access-token=fake --host=fake-sentry:8080 --insecure-http"]),
    # TODO: use a real Reader-only service principal (MCP_TEST_AZURE_*) instead of the Topaz emulator
    ServerCase("azure", "call_az", {"cli_command": "az group list"}, "mcp-test-rg", path="/mcp",
               mock="topaz", entrypoint="azure_topaz_entrypoint.sh"),
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def required_secrets(case):
    return [*case.container_env.values(), *([case.token_secret] if case.token_secret else [])]


def docker_run(*args, env=None):
    return subprocess.run(["docker", "run", "-d", *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def image_cmd(image):
    inspected = subprocess.run(["docker", "image", "inspect", "-f", "{{json .Config.Cmd}}", image],
                               check=True, capture_output=True, text=True).stdout
    return json.loads(inspected) or []


@pytest.fixture(scope="session")
def network():
    name = f"mcp-test-{os.getpid()}"
    subprocess.run(["docker", "network", "create", name], check=True, capture_output=True)
    yield name
    subprocess.run(["docker", "network", "rm", name], capture_output=True)


@pytest.fixture
def fake_sentry(network):
    container = docker_run("--network", network, "--network-alias", "fake-sentry",
                           "-v", f"{TESTS_DIR / 'fake_sentry.py'}:/fake_sentry.py:ro",
                           MOCK_PYTHON_IMAGE, "python3", "/fake_sentry.py")
    yield container
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


@pytest.fixture
def topaz(network):
    container = docker_run("--network", network, "--network-alias", "topaz.local.dev", TOPAZ_IMAGE,
                           "--default-subscription", "00000000-0000-0000-0000-000000000001")
    deadline = time.time() + 120
    while "Topaz is ready" not in subprocess.run(["docker", "logs", container], capture_output=True, text=True).stdout:
        if time.time() > deadline:
            pytest.fail("Topaz emulator not ready after 120s")
        time.sleep(2)
    yield container
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


@pytest.fixture(params=SERVERS, ids=lambda case: case.name)
def server(request, network):
    case = request.param
    missing = [secret for secret in required_secrets(case) if not os.environ.get(secret)]
    if missing and os.environ.get("CI"):
        pytest.fail(f"missing secrets: {', '.join(missing)}")
    if missing:
        pytest.skip(f"missing secrets: {', '.join(missing)}")
    if case.mock:
        request.getfixturevalue(case.mock)

    image = f"mcp-test-{case.name}"
    subprocess.run(["docker", "build", "-q", "-t", image, str(REPO_ROOT / "servers" / case.name)],
                   check=True, stdout=subprocess.DEVNULL)
    port = free_port()
    env_flags = [flag for var in case.container_env for flag in ("-e", var)]
    container_env = {var: os.environ[secret] for var, secret in case.container_env.items()}
    command = case.command
    entrypoint_flags = []
    if case.entrypoint:
        command = command or image_cmd(image)
        entrypoint_flags = ["-v", f"{TESTS_DIR}:/tests:ro", "--entrypoint", f"/tests/{case.entrypoint}"]
    container = docker_run("--network", network, "-p", f"{port}:8000", *env_flags, *entrypoint_flags,
                           image, *command, env=os.environ | container_env)
    yield case, f"http://localhost:{port}{case.path}"
    if request.node.rep_call.failed:
        subprocess.run(["docker", "logs", "--tail", "50", container])
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


def test_read_only_tool(server):
    case, url = server
    session = SseSession(url) if case.transport == "sse" else HttpSession(url, os.environ.get(case.token_secret))
    result = call_tool(session, case.tool, case.arguments)
    assert case.expect in json.dumps(result), f"{case.tool} result does not contain {case.expect!r}"
