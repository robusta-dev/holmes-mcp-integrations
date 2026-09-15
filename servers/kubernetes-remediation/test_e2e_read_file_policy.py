#!/usr/bin/env python3
"""
End-to-end tests for the read_file_from_container path policy against a REAL
cluster (ROB-973). Skipped unless KUBECTL_E2E=1 and kubectl can reach a cluster
(KUBECONFIG). Creates namespace `rob973-e2e` with pods carrying planted fake
credentials, exercises the tool functions with real `kubectl exec`, and cleans
up.

Run with:  KUBECTL_E2E=1 pytest test_e2e_read_file_policy.py -v
"""

import os
import subprocess

import pytest

import kubernetes_remediation as k

pytestmark = pytest.mark.skipif(
    os.getenv("KUBECTL_E2E") != "1", reason="set KUBECTL_E2E=1 with a reachable cluster"
)

NS = "rob973-e2e"
MANIFEST = f"""
apiVersion: v1
kind: Namespace
metadata:
  name: {NS}
---
apiVersion: v1
kind: Secret
metadata:
  name: fake-gcp-key
  namespace: {NS}
stringData:
  key.json: '{{"type":"service_account","private_key":"FAKE-ROB973-GCP-PRIVATE-KEY"}}'
---
apiVersion: v1
kind: Pod
metadata:
  name: victim
  namespace: {NS}
spec:
  volumes:
  - name: gcp
    secret:
      secretName: fake-gcp-key
  containers:
  - name: app
    image: busybox:1.37.0
    volumeMounts:
    - name: gcp
      mountPath: /var/secrets/gcp
      readOnly: true
    command: ["sh", "-c"]
    args:
    - |
      mkdir -p /app /root/.aws /var/log /vault/secrets /etc/app
      echo 'server: {{port: 8080}}' > /app/config.yaml
      echo 'DB_PASSWORD=FAKE-ROB973-DOTENV-SECRET' > /app/.env
      printf '[default]\\naws_secret_access_key = FAKE-ROB973-AWS-SECRET\\n' > /root/.aws/credentials
      echo 'FAKE-ROB973-VAULT-TOKEN' > /vault/secrets/token
      echo 'FAKE-ROB973-TLS-KEY' > /etc/app/server.key
      echo 'request ok 200' > /var/log/app.log
      ln -s /var/run/secrets/kubernetes.io/serviceaccount/token /app/link-to-token
      ln -s /var/secrets/gcp/key.json /app/gcp-key-link
      ln -s /var/log/app.log /app/log-link
      sleep 1d
---
apiVersion: v1
kind: Pod
metadata:
  name: noreadlink
  namespace: {NS}
spec:
  containers:
  - name: app
    image: busybox:1.37.0
    command: ["sh", "-c"]
    args:
    - |
      mkdir -p /app
      ln -s /var/run/secrets/kubernetes.io/serviceaccount/token /app/link-to-token
      echo 'ok' > /app/config.yaml
      rm /bin/readlink
      sleep 1d
"""


def _kubectl(*args, **kw):
    return subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True, **kw)


@pytest.fixture(scope="module", autouse=True)
def cluster_fixtures():
    _kubectl("apply", "-f", "-", input=MANIFEST)
    _kubectl("-n", NS, "wait", "--for=condition=ready", "pod/victim", "pod/noreadlink",
             "--timeout=180s")
    yield
    if os.getenv("KUBECTL_E2E_KEEP") != "1":
        subprocess.run(["kubectl", "delete", "ns", NS, "--wait=false"], capture_output=True)


def _read(pod, path):
    return k.read_file_from_container(namespace=NS, pod=pod, path=path)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/app/config.yaml", "server: {port: 8080}"),
        ("/var/log/app.log", "request ok 200"),
        ("/app/log-link", "request ok 200"),  # symlink into an allowed root reads fine
    ],
)
def test_legitimate_files_are_readable(path, expected):
    result = _read("victim", path)
    assert result["success"] is True, result
    assert expected in result["stdout"]


@pytest.mark.parametrize(
    "path",
    [
        "/var/secrets/gcp/key.json",
        "/root/.aws/credentials",
        "/app/.env",
        "/vault/secrets/token",
        "/etc/app/server.key",
        "/app/gcp-key-link",  # symlink under an allowed root -> secret mount
        "/app/link-to-token",  # symlink -> SA token
        "/proc/1/environ",
    ],
)
def test_credentials_are_refused_and_never_leak(path):
    result = _read("victim", path)
    assert result["success"] is False, result
    assert "FAKE-ROB973" not in str(result)
    assert "eyJ" not in str(result)  # no JWT bytes
    assert "restricted" in result["error"] or "not under any allowed root" in result["error"]


def test_missing_readlink_fails_closed():
    result = _read("noreadlink", "/app/link-to-token")
    assert result["success"] is False, result
    assert "could not be canonicalized" in result["error"]
    assert "readlink" in result["error"]
    assert "eyJ" not in str(result)


def test_missing_readlink_refuses_even_legitimate_files():
    result = _read("noreadlink", "/app/config.yaml")
    assert result["success"] is False, result
    assert "run_kubectl_command" in result["error"]


def test_config_reports_hardened_policy():
    cfg = k.get_remediation_mcp_config()
    assert "/" not in cfg["file_read_allowed_paths"]
    assert cfg["file_read_require_canonicalization"] is True
    assert "/var/secrets" in cfg["file_read_hard_denied_paths"]
