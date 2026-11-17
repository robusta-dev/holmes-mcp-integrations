"""Build each MCP server's Dockerfile, run it, and call one read-only tool.

Secrets come from MCP_TEST_* env vars; a server whose secrets are missing is skipped (failed under CI).
Servers without test credentials yet run against a stand-in provider (see `mock`).
Run one server with: pytest tests -k github-app
"""
import json
import os
import shutil
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
PREFECT_IMAGE = "prefecthq/prefect:3.9.3-python3.12"
FAKE_GCS_IMAGE = "fsouza/fake-gcs-server:1.52.2"
GCP_PROJECT = "mcp-test-project"
KIND_CLUSTER = os.environ.get("KIND_CLUSTER", "mcp-test")
PREFECT_SEED_FLOW = ("import urllib.request; urllib.request.urlopen(urllib.request.Request("
                     "'http://localhost:4200/api/flows/', b'{\"name\": \"mcp-test-flow\"}', {'Content-Type': 'application/json'}))")
GITHUB_TEST_COMMITS = ("list_commits", {"owner": "kotlickya", "repo": "argo-repo", "perPage": 1}, "sha")


@dataclass
class ServerCase:
    name: str
    tool: str
    arguments: dict
    expect: str
    container_env: dict = field(default_factory=dict)  # container var -> MCP_TEST_* secret
    env: dict = field(default_factory=dict)  # container var -> literal value
    token_secret: str = ""  # MCP_TEST_* secret sent as the Bearer token
    path: str = "/"
    transport: str = "http"
    mock: str = ""  # fixture starting a stand-in provider on the test network
    command: list = field(default_factory=list)  # replaces the image CMD
    entrypoint: str = ""  # script in tests/ run before the image's own CMD
    files: str = ""  # fixture whose directory is mounted read-only at /mcp-test
    network: str = ""  # docker network to run on instead of the test network, e.g. kind


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
    ServerCase("prefect", "get_flows", {}, "mcp-test-flow", path="/sse", transport="sse", mock="prefect_server",
               env={"PREFECT_API_URL": "http://prefect-server:4200/api"}),
    # TODO: use an Atlassian Cloud test site (MCP_TEST_CONFLUENCE_*) instead of fake_atlassian.py
    ServerCase("mcp-atlassian", "confluence_search", {"query": "mcp-test-7f3a"}, "MCP test page", path="/sse",
               transport="sse", mock="fake_atlassian",
               env={"TRANSPORT": "sse", "PORT": "8000", "CONFLUENCE_URL": "http://fake-atlassian:8080/wiki",
                    "CONFLUENCE_USERNAME": "mcp-test@example.com", "CONFLUENCE_API_TOKEN": "fake",
                    "READ_ONLY_MODE": "true", "ALLOW_GLOBAL_CRED_FALLBACK": "true",
                    "ENABLED_TOOLS": "confluence_search,confluence_get_page"}),
    # TODO: use a Viewer-only service account key on a GCP test project instead of fake_gcp.py and fake-gcs-server
    ServerCase("gcp/gcloud", "run_gcloud_command", {"args": ["projects", "describe", GCP_PROJECT]}, GCP_PROJECT,
               path="/sse", transport="sse", mock="fake_gcp", files="gcp_files",
               env={"CLOUDSDK_AUTH_ACCESS_TOKEN_FILE": "/mcp-test/token",
                    "CLOUDSDK_API_ENDPOINT_OVERRIDES_CLOUDRESOURCEMANAGER": "http://fake-gcp:8080/"}),
    ServerCase("gcp/observability", "list_log_names", {"parent": f"projects/{GCP_PROJECT}"}, "mcp-test-log",
               path="/sse", transport="sse", mock="fake_gcp", files="gcp_files",
               command=["--port", "8000", "--stdio", "observability-mcp"],
               env={"GOOGLE_APPLICATION_CREDENTIALS": "/mcp-test/key.json", "GOOGLE_CLOUD_UNIVERSE_DOMAIN": "fake.test",
                    "NODE_EXTRA_CA_CERTS": "/mcp-test/ca.crt", "GOOGLE_CLOUD_PROJECT": GCP_PROJECT}),
    ServerCase("gcp/storage", "list_objects", {"bucket_name": "mcp-test-bucket"}, "mcp-test-marker.txt",
               path="/sse", transport="sse", mock="fake_gcs", command=["--port", "8000", "--stdio", "storage-mcp"],
               env={"STORAGE_EMULATOR_HOST": "http://fake-gcs:4443/storage/v1", "GOOGLE_CLOUD_PROJECT": GCP_PROJECT}),
    ServerCase("kubernetes-mcp-server", "namespaces_list", {}, "kube-system", path="/mcp", files="kind_files",
               network="kind", command=["--port", "8000", "--read-only"], env={"KUBECONFIG": "/mcp-test/kubeconfig"}),
    # get is off the default verb allowlist, which only holds approval-gated mutations
    ServerCase("kubernetes-remediation", "run_kubectl_command", {"args": ["get", "namespace", "kube-system"]},
               "kube-system", path="/mcp", files="kind_files", network="kind",
               env={"KUBECONFIG": "/mcp-test/kubeconfig", "KUBECTL_ALLOWED_COMMANDS": "get"}),
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


def run_fake(network, script, *args):
    alias = script.replace("_", "-")
    return docker_run("--network", network, "--network-alias", alias, "-v", f"{TESTS_DIR / script}.py:/{script}.py:ro",
                      *args, MOCK_PYTHON_IMAGE, "python3", f"/{script}.py")


def openssl(*args):
    subprocess.run(["openssl", *args], check=True, capture_output=True)


@pytest.fixture(scope="session")
def gcp_files(tmp_path_factory):
    """Fake service account key, access token and a test CA for the fake.test universe."""
    files = tmp_path_factory.mktemp("gcp")
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=mcp-test-ca",
            "-keyout", files / "ca.key", "-out", files / "ca.crt")
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=fake.test",
            "-keyout", files / "server.key", "-out", files / "server.csr")
    (files / "san.cnf").write_text("subjectAltName=DNS:logging.fake.test,DNS:oauth2.fake.test\n")
    openssl("x509", "-req", "-days", "1", "-in", files / "server.csr", "-CA", files / "ca.crt", "-CAkey", files / "ca.key",
            "-CAcreateserial", "-extfile", files / "san.cnf", "-out", files / "server.crt")
    openssl("genrsa", "-out", files / "sa.pem", "2048")
    (files / "key.json").write_text(json.dumps({
        "type": "service_account", "project_id": GCP_PROJECT, "private_key_id": "1",
        "private_key": (files / "sa.pem").read_text(), "client_email": f"mcp-test@{GCP_PROJECT}.iam.gserviceaccount.com",
        "client_id": "1", "token_uri": "https://oauth2.fake.test/token", "universe_domain": "fake.test"}))
    (files / "token").write_text("fake")
    for path in [files, *files.iterdir()]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    return files


@pytest.fixture
def fake_sentry(network):
    container = run_fake(network, "fake_sentry")
    yield container
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


@pytest.fixture
def fake_atlassian(network):
    container = run_fake(network, "fake_atlassian")
    yield container
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


@pytest.fixture(scope="session")
def kind_files(tmp_path_factory):
    """Kubeconfig for the kind cluster as seen from containers on the kind network."""
    kubeconfig = subprocess.run(["kind", "get", "kubeconfig", "--internal", "--name", KIND_CLUSTER],
                                capture_output=True, text=True) if shutil.which("kind") else None
    if (not kubeconfig or kubeconfig.returncode) and os.environ.get("CI"):
        pytest.fail(f"kind cluster {KIND_CLUSTER} not found")
    if not kubeconfig or kubeconfig.returncode:
        pytest.skip(f"kind cluster {KIND_CLUSTER} not found, create it with: kind create cluster --name {KIND_CLUSTER}")
    files = tmp_path_factory.mktemp("kind")
    (files / "kubeconfig").write_text(kubeconfig.stdout)
    files.chmod(0o755)
    (files / "kubeconfig").chmod(0o644)
    return files


@pytest.fixture
def fake_gcp(network, gcp_files):
    container = run_fake(network, "fake_gcp", "--network-alias", "logging.fake.test", "--network-alias",
                         "oauth2.fake.test", "-v", f"{gcp_files}:/mcp-test:ro")
    yield container
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


@pytest.fixture
def fake_gcs(network, tmp_path):
    bucket = tmp_path / "mcp-test-bucket"
    bucket.mkdir(parents=True)
    (bucket / "mcp-test-marker.txt").write_text("mcp test")
    for path in [tmp_path, bucket, *bucket.iterdir()]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    container = docker_run("--network", network, "--network-alias", "fake-gcs", "-v", f"{tmp_path}:/data:ro",
                           FAKE_GCS_IMAGE, "-scheme", "http", "-port", "4443", "-data", "/data")
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


@pytest.fixture
def prefect_server(network):
    container = docker_run("--network", network, "--network-alias", "prefect-server", PREFECT_IMAGE,
                           "prefect", "server", "start", "--host", "0.0.0.0")
    deadline = time.time() + 120
    while subprocess.run(["docker", "exec", container, "python", "-c", PREFECT_SEED_FLOW], capture_output=True).returncode:
        if time.time() > deadline:
            pytest.fail("Prefect server not ready after 120s")
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
    env_flags += [flag for var, value in case.env.items() for flag in ("-e", f"{var}={value}")]
    container_env = {var: os.environ[secret] for var, secret in case.container_env.items()}
    command = case.command
    entrypoint_flags = []
    if case.entrypoint:
        command = command or image_cmd(image)
        entrypoint_flags = ["-v", f"{TESTS_DIR}:/tests:ro", "--entrypoint", f"/tests/{case.entrypoint}"]
    if case.files:
        entrypoint_flags += ["-v", f"{request.getfixturevalue(case.files)}:/mcp-test:ro"]
    container = docker_run("--network", case.network or network, "-p", f"{port}:8000", *env_flags, *entrypoint_flags,
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
