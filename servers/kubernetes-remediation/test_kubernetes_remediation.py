#!/usr/bin/env python3
"""
Tests for Kubernetes Remediation MCP Server

Run with: pytest test_kubernetes_remediation.py -v

This test file mocks heavy dependencies (FastMCP, uvicorn) to allow
running tests without installing the full MCP server dependencies.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock
import subprocess


# Create a mock FastMCP that preserves decorated functions
class MockFastMCP:
    """Mock FastMCP that returns the original function from tool decorator"""
    def __init__(self, *args, **kwargs):
        pass

    def tool(self, *args, **kwargs):
        """Decorator that returns the original function unchanged"""
        def decorator(func):
            return func
        return decorator

    def http_app(self):
        return MagicMock()

    def run(self):
        pass


# Mock heavy dependencies before importing the module under test
mock_fastmcp = MagicMock()
mock_fastmcp.FastMCP = MockFastMCP
sys.modules['fastmcp'] = mock_fastmcp
sys.modules['uvicorn'] = MagicMock()
sys.modules['dotenv'] = MagicMock()
sys.modules['dotenv'].load_dotenv = MagicMock()

# Now import the module - the FastMCP decorator will preserve functions
import kubernetes_remediation as kr


class TestValidateKubectlArgs:
    """Unit tests for validate_kubectl_args function"""

    # Test all 14 allowed commands
    @pytest.mark.parametrize("command", [
        "get",
        "describe",
        "logs",
        "edit",
        "patch",
        "delete",
        "scale",
        "rollout",
        "cordon",
        "uncordon",
        "drain",
        "taint",
        "label",
        "annotate",
    ])
    def test_allowed_commands_are_accepted(self, command):
        """Each allowed command should pass validation"""
        args = [command, "pods"]
        result = kr.validate_kubectl_args(args)
        assert result[0] == command

    @pytest.mark.parametrize("command", [
        "get",
        "describe",
        "logs",
        "edit",
        "patch",
        "delete",
        "scale",
        "rollout",
        "cordon",
        "uncordon",
        "drain",
        "taint",
        "label",
        "annotate",
    ])
    def test_allowed_commands_with_kubectl_prefix(self, command):
        """Commands should work with 'kubectl' prefix"""
        args = ["kubectl", command, "pods"]
        result = kr.validate_kubectl_args(args)
        assert result[0] == command

    def test_disallowed_command_exec(self):
        """exec command should be rejected"""
        with pytest.raises(ValueError, match="not allowed"):
            kr.validate_kubectl_args(["exec", "pod-name", "--", "sh"])

    def test_disallowed_command_apply(self):
        """apply command should be rejected"""
        with pytest.raises(ValueError, match="not allowed"):
            kr.validate_kubectl_args(["apply", "-f", "manifest.yaml"])

    def test_disallowed_command_create(self):
        """create command should be rejected"""
        with pytest.raises(ValueError, match="not allowed"):
            kr.validate_kubectl_args(["create", "deployment", "nginx"])

    def test_disallowed_command_run(self):
        """run command should be rejected (use run_image tool instead)"""
        with pytest.raises(ValueError, match="not allowed"):
            kr.validate_kubectl_args(["run", "pod", "--image=nginx"])

    def test_empty_args_rejected(self):
        """Empty args should be rejected"""
        with pytest.raises(ValueError, match="No arguments provided"):
            kr.validate_kubectl_args([])

    def test_only_kubectl_rejected(self):
        """Just 'kubectl' with no command should be rejected"""
        with pytest.raises(ValueError, match="No command provided"):
            kr.validate_kubectl_args(["kubectl"])


class TestDangerousFlagsBlocking:
    """Tests for dangerous flag blocking"""

    @pytest.mark.parametrize("flag", [
        "--kubeconfig",
        "--context",
        "--cluster",
        "--user",
        "--token",
        "--as",
        "--as-group",
        "--as-uid",
    ])
    def test_dangerous_flags_rejected(self, flag):
        """Dangerous flags should be rejected"""
        with pytest.raises(ValueError, match="not permitted"):
            kr.validate_kubectl_args(["get", "pods", flag, "value"])

    @pytest.mark.parametrize("flag", [
        "--kubeconfig=/path/to/config",
        "--context=other-cluster",
        "--cluster=prod",
        "--user=admin",
        "--token=secret123",
        "--as=admin",
        "--as-group=system:masters",
        "--as-uid=0",
    ])
    def test_dangerous_flags_with_values_rejected(self, flag):
        """Dangerous flags with = syntax should be rejected"""
        with pytest.raises(ValueError, match="not permitted"):
            kr.validate_kubectl_args(["get", "pods", flag])

    def test_overrides_flag_rejected(self):
        """--overrides flag should be rejected (privilege escalation risk)"""
        with pytest.raises(ValueError, match="not permitted"):
            kr.validate_kubectl_args(["get", "pods", "--overrides", "{}"])


class TestShellCharacterRejection:
    """Tests for shell metacharacter rejection"""

    @pytest.mark.parametrize("char,desc", [
        (";", "semicolon"),
        ("|", "pipe"),
        ("&", "ampersand"),
        ("$", "dollar"),
        ("`", "backtick"),
        ("\\", "backslash"),
        ("'", "single quote"),
        # Note: double quote (") is allowed for JSON payloads
        ("\n", "newline"),
        ("\r", "carriage return"),
    ])
    def test_shell_metacharacters_rejected(self, char, desc):
        """Shell metacharacters should be rejected"""
        with pytest.raises(ValueError, match="Invalid characters"):
            kr.validate_kubectl_args(["get", f"pods{char}evil"])


class TestJSONPayloads:
    """
    Tests for JSON payload support.

    Double quotes (") are allowed because they're required for JSON payloads
    and shell=False makes them safe from injection.
    """

    def test_patch_with_json_works(self):
        """patch with JSON payload should work"""
        result = kr.validate_kubectl_args([
            "patch", "deployment", "my-app",
            "-p", '{"spec":{"replicas":3}}'
        ])
        assert result[0] == "patch"
        assert '{"spec":{"replicas":3}}' in result

    def test_jsonpath_output_works(self):
        """jsonpath output should work"""
        result = kr.validate_kubectl_args([
            "get", "pod", "my-pod",
            "-o", "jsonpath={.status.phase}"
        ])
        assert result[0] == "get"

    def test_label_selector_with_quotes_works(self):
        """Label selectors with quotes should work"""
        result = kr.validate_kubectl_args(["get", "pods", "-l", 'app="nginx"'])
        assert result[0] == "get"

    def test_patch_strategic_merge(self):
        """Strategic merge patch with JSON should work"""
        result = kr.validate_kubectl_args([
            "patch", "deployment", "my-app",
            "--type=strategic",
            "-p", '{"spec":{"template":{"spec":{"containers":[{"name":"app","image":"nginx:1.19"}]}}}}'
        ])
        assert result[0] == "patch"

    def test_patch_json_patch_type(self):
        """JSON patch type should work"""
        result = kr.validate_kubectl_args([
            "patch", "deployment", "my-app",
            "--type=json",
            "-p", '[{"op":"replace","path":"/spec/replicas","value":3}]'
        ])
        assert result[0] == "patch"


class TestKubectlCommandExamples:
    """Tests for realistic kubectl command examples"""

    def test_get_pods_default_namespace(self):
        """get pods in default namespace"""
        result = kr.validate_kubectl_args(["get", "pods"])
        assert result == ["get", "pods"]

    def test_get_pods_with_namespace(self):
        """get pods with namespace flag"""
        result = kr.validate_kubectl_args(["get", "pods", "-n", "kube-system"])
        assert result == ["get", "pods", "-n", "kube-system"]

    def test_get_pods_all_namespaces(self):
        """get pods across all namespaces"""
        result = kr.validate_kubectl_args(["get", "pods", "-A"])
        assert result == ["get", "pods", "-A"]

    def test_get_pods_with_output_format(self):
        """get pods with output format"""
        result = kr.validate_kubectl_args(["get", "pods", "-o", "wide"])
        assert result == ["get", "pods", "-o", "wide"]

    def test_get_pods_yaml_output(self):
        """get pods with yaml output"""
        result = kr.validate_kubectl_args(["get", "pods", "-o", "yaml"])
        assert result == ["get", "pods", "-o", "yaml"]

    def test_get_pods_json_output(self):
        """get pods with json output"""
        result = kr.validate_kubectl_args(["get", "pods", "-o", "json"])
        assert result == ["get", "pods", "-o", "json"]

    def test_get_pods_with_selector(self):
        """get pods with label selector (without quotes)"""
        result = kr.validate_kubectl_args(["get", "pods", "-l", "app=nginx"])
        assert result == ["get", "pods", "-l", "app=nginx"]

    def test_get_pods_with_field_selector(self):
        """get pods with field selector"""
        result = kr.validate_kubectl_args(["get", "pods", "--field-selector", "status.phase=Running"])
        assert result == ["get", "pods", "--field-selector", "status.phase=Running"]

    def test_describe_pod(self):
        """describe a specific pod"""
        result = kr.validate_kubectl_args(["describe", "pod", "my-pod", "-n", "default"])
        assert result == ["describe", "pod", "my-pod", "-n", "default"]

    def test_logs_pod(self):
        """get logs from a pod"""
        result = kr.validate_kubectl_args(["logs", "my-pod", "-n", "default"])
        assert result == ["logs", "my-pod", "-n", "default"]

    def test_logs_with_container(self):
        """get logs from specific container"""
        result = kr.validate_kubectl_args(["logs", "my-pod", "-c", "sidecar"])
        assert result == ["logs", "my-pod", "-c", "sidecar"]

    def test_logs_with_tail(self):
        """get logs with tail limit"""
        result = kr.validate_kubectl_args(["logs", "my-pod", "--tail", "100"])
        assert result == ["logs", "my-pod", "--tail", "100"]

    def test_logs_follow(self):
        """follow logs (streaming)"""
        result = kr.validate_kubectl_args(["logs", "my-pod", "-f"])
        assert result == ["logs", "my-pod", "-f"]

    def test_logs_previous(self):
        """get logs from previous container instance"""
        result = kr.validate_kubectl_args(["logs", "my-pod", "--previous"])
        assert result == ["logs", "my-pod", "--previous"]

    def test_delete_pod(self):
        """delete a pod"""
        result = kr.validate_kubectl_args(["delete", "pod", "my-pod", "-n", "default"])
        assert result == ["delete", "pod", "my-pod", "-n", "default"]

    def test_delete_pod_force(self):
        """force delete a pod"""
        result = kr.validate_kubectl_args(["delete", "pod", "my-pod", "--force", "--grace-period=0"])
        assert result == ["delete", "pod", "my-pod", "--force", "--grace-period=0"]

    def test_scale_deployment(self):
        """scale a deployment"""
        result = kr.validate_kubectl_args(["scale", "deployment", "my-app", "--replicas=3"])
        assert result == ["scale", "deployment", "my-app", "--replicas=3"]

    def test_rollout_status(self):
        """check rollout status"""
        result = kr.validate_kubectl_args(["rollout", "status", "deployment/my-app"])
        assert result == ["rollout", "status", "deployment/my-app"]

    def test_rollout_restart(self):
        """restart a deployment"""
        result = kr.validate_kubectl_args(["rollout", "restart", "deployment/my-app"])
        assert result == ["rollout", "restart", "deployment/my-app"]

    def test_rollout_undo(self):
        """undo a deployment rollout"""
        result = kr.validate_kubectl_args(["rollout", "undo", "deployment/my-app"])
        assert result == ["rollout", "undo", "deployment/my-app"]

    def test_rollout_history(self):
        """view rollout history"""
        result = kr.validate_kubectl_args(["rollout", "history", "deployment/my-app"])
        assert result == ["rollout", "history", "deployment/my-app"]

    def test_cordon_node(self):
        """cordon a node"""
        result = kr.validate_kubectl_args(["cordon", "node-1"])
        assert result == ["cordon", "node-1"]

    def test_uncordon_node(self):
        """uncordon a node"""
        result = kr.validate_kubectl_args(["uncordon", "node-1"])
        assert result == ["uncordon", "node-1"]

    def test_drain_node(self):
        """drain a node"""
        result = kr.validate_kubectl_args(["drain", "node-1", "--ignore-daemonsets"])
        assert result == ["drain", "node-1", "--ignore-daemonsets"]

    def test_drain_node_delete_local_data(self):
        """drain a node with local data deletion"""
        result = kr.validate_kubectl_args(["drain", "node-1", "--delete-emptydir-data", "--ignore-daemonsets"])
        assert result == ["drain", "node-1", "--delete-emptydir-data", "--ignore-daemonsets"]

    def test_taint_node(self):
        """add taint to a node"""
        result = kr.validate_kubectl_args(["taint", "nodes", "node-1", "key=value:NoSchedule"])
        assert result == ["taint", "nodes", "node-1", "key=value:NoSchedule"]

    def test_taint_node_remove(self):
        """remove taint from a node"""
        result = kr.validate_kubectl_args(["taint", "nodes", "node-1", "key:NoSchedule-"])
        assert result == ["taint", "nodes", "node-1", "key:NoSchedule-"]

    def test_label_pod(self):
        """add label to a pod"""
        result = kr.validate_kubectl_args(["label", "pods", "my-pod", "env=production"])
        assert result == ["label", "pods", "my-pod", "env=production"]

    def test_label_pod_overwrite(self):
        """overwrite existing label"""
        result = kr.validate_kubectl_args(["label", "pods", "my-pod", "env=staging", "--overwrite"])
        assert result == ["label", "pods", "my-pod", "env=staging", "--overwrite"]

    def test_label_remove(self):
        """remove a label"""
        result = kr.validate_kubectl_args(["label", "pods", "my-pod", "env-"])
        assert result == ["label", "pods", "my-pod", "env-"]

    def test_annotate_pod(self):
        """add annotation to a pod"""
        result = kr.validate_kubectl_args(["annotate", "pods", "my-pod", "description=my-description"])
        assert result == ["annotate", "pods", "my-pod", "description=my-description"]

    def test_annotate_remove(self):
        """remove an annotation"""
        result = kr.validate_kubectl_args(["annotate", "pods", "my-pod", "description-"])
        assert result == ["annotate", "pods", "my-pod", "description-"]

    def test_edit_deployment(self):
        """edit command should be allowed"""
        result = kr.validate_kubectl_args(["edit", "deployment", "my-app"])
        assert result == ["edit", "deployment", "my-app"]


class TestRunKubectlWithMock:
    """Integration tests for run_kubectl with mocked subprocess"""

    @patch("kubernetes_remediation.subprocess.run")
    def test_successful_kubectl_get(self, mock_run):
        """Test successful kubectl get command"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NAME    READY   STATUS    RESTARTS   AGE\nnginx   1/1     Running   0          1d\n",
            stderr=""
        )

        result = kr.run_kubectl(["get", "pods"])

        assert result["success"] is True
        assert "nginx" in result["stdout"]
        assert result["return_code"] == 0
        mock_run.assert_called_once()

    @patch("kubernetes_remediation.subprocess.run")
    def test_failed_kubectl_command(self, mock_run):
        """Test failed kubectl command"""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error from server (NotFound): pods \"nonexistent\" not found"
        )

        result = kr.run_kubectl(["get", "pod", "nonexistent"])

        assert result["success"] is False
        assert "NotFound" in result["stderr"]
        assert result["return_code"] == 1

    @patch("kubernetes_remediation.subprocess.run")
    def test_kubectl_timeout(self, mock_run):
        """Test kubectl command timeout"""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="kubectl", timeout=60)

        result = kr.run_kubectl(["logs", "my-pod", "-f"])

        assert result["success"] is False
        assert "timed out" in result["error"]


class TestKubectlToolWithMock:
    """Integration tests for kubectl MCP tool with mocked subprocess"""

    @patch("kubernetes_remediation.subprocess.run")
    def test_kubectl_tool_success(self, mock_run):
        """Test kubectl tool successful execution"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="deployment.apps/my-app scaled\n",
            stderr=""
        )

        result = kr.kubectl(["scale", "deployment", "my-app", "--replicas=5"])

        assert result["success"] is True
        assert "scaled" in result["stdout"]

    def test_kubectl_tool_validation_failure(self):
        """Test kubectl tool with invalid command"""
        result = kr.kubectl(["exec", "pod", "--", "sh"])

        assert result["success"] is False
        assert "error" in result
        assert "not allowed" in result["error"]

    def test_kubectl_tool_dangerous_flag_rejection(self):
        """Test kubectl tool rejects dangerous flags"""
        result = kr.kubectl(["get", "pods", "--context", "production"])

        assert result["success"] is False
        assert "error" in result
        assert "not permitted" in result["error"]


class TestValidateImage:
    """Tests for validate_image function"""

    def test_no_allowed_images_configured(self):
        """When no images are configured, validation should fail"""
        # Save original and clear allowed images
        original = kr.ALLOWED_IMAGES.copy()
        kr.ALLOWED_IMAGES.clear()

        try:
            with pytest.raises(ValueError, match="disabled"):
                kr.validate_image("nginx")
        finally:
            kr.ALLOWED_IMAGES.update(original)

    def test_allowed_image_passes(self):
        """When image is in allowlist, validation should pass"""
        original = kr.ALLOWED_IMAGES.copy()
        kr.ALLOWED_IMAGES.clear()
        kr.ALLOWED_IMAGES.add("nginx:latest")

        try:
            # Should not raise
            kr.validate_image("nginx:latest")
        finally:
            kr.ALLOWED_IMAGES.clear()
            kr.ALLOWED_IMAGES.update(original)

    def test_disallowed_image_rejected(self):
        """When image is not in allowlist, validation should fail"""
        original = kr.ALLOWED_IMAGES.copy()
        kr.ALLOWED_IMAGES.clear()
        kr.ALLOWED_IMAGES.add("nginx:latest")

        try:
            with pytest.raises(ValueError, match="not allowed"):
                kr.validate_image("alpine")
        finally:
            kr.ALLOWED_IMAGES.clear()
            kr.ALLOWED_IMAGES.update(original)


class TestRunImageToolWithMock:
    """Integration tests for run_image MCP tool"""

    @patch("kubernetes_remediation.subprocess.run")
    def test_run_image_disabled_by_default(self, mock_run):
        """run_image should fail when no images are configured"""
        original = kr.ALLOWED_IMAGES.copy()
        kr.ALLOWED_IMAGES.clear()

        try:
            result = kr.run_image("nginx")
            assert result["success"] is False
            assert "disabled" in result["error"]
        finally:
            kr.ALLOWED_IMAGES.update(original)

    @patch("kubernetes_remediation.subprocess.run")
    def test_run_image_with_allowed_image(self, mock_run):
        """run_image should work with allowed image"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Hello World\n",
            stderr=""
        )

        original = kr.ALLOWED_IMAGES.copy()
        kr.ALLOWED_IMAGES.clear()
        kr.ALLOWED_IMAGES.add("alpine")

        try:
            result = kr.run_image("alpine", command=["echo", "Hello World"])
            assert result["success"] is True
            mock_run.assert_called_once()
            # Verify the command includes 'run' and the image
            call_args = mock_run.call_args[0][0]
            assert "kubectl" in call_args[0]
            assert "run" in call_args
            assert any("alpine" in arg for arg in call_args)
        finally:
            kr.ALLOWED_IMAGES.clear()
            kr.ALLOWED_IMAGES.update(original)

    @patch("kubernetes_remediation.subprocess.run")
    def test_run_image_with_namespace(self, mock_run):
        """run_image should include namespace flag when specified"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr=""
        )

        original = kr.ALLOWED_IMAGES.copy()
        kr.ALLOWED_IMAGES.clear()
        kr.ALLOWED_IMAGES.add("busybox")

        try:
            result = kr.run_image("busybox", namespace="test-ns")
            assert result["success"] is True
            call_args = mock_run.call_args[0][0]
            assert "-n" in call_args
            assert "test-ns" in call_args
        finally:
            kr.ALLOWED_IMAGES.clear()
            kr.ALLOWED_IMAGES.update(original)

    def test_run_image_shell_injection_in_namespace(self):
        """run_image should reject shell characters in namespace"""
        original = kr.ALLOWED_IMAGES.copy()
        kr.ALLOWED_IMAGES.clear()
        kr.ALLOWED_IMAGES.add("alpine")

        try:
            result = kr.run_image("alpine", namespace="test;rm -rf /")
            assert result["success"] is False
            assert "Invalid characters" in result["error"]
        finally:
            kr.ALLOWED_IMAGES.clear()
            kr.ALLOWED_IMAGES.update(original)


class TestGetConfigTool:
    """Tests for get_config MCP tool"""

    def test_get_config_returns_expected_keys(self):
        """get_config should return all expected configuration keys"""
        result = kr.get_config()

        assert "allowed_commands" in result
        assert "dangerous_flags" in result
        assert "timeout_seconds" in result
        assert "allowed_images" in result
        assert "run_image_enabled" in result

    def test_get_config_commands_are_sorted(self):
        """get_config should return sorted command list"""
        result = kr.get_config()

        commands = result["allowed_commands"]
        assert commands == sorted(commands)

    def test_get_config_contains_all_allowed_commands(self):
        """get_config should list all 14 allowed commands"""
        result = kr.get_config()

        expected_commands = [
            "annotate", "cordon", "delete", "describe", "drain", "edit",
            "get", "label", "logs", "patch", "rollout", "scale", "taint", "uncordon"
        ]
        assert result["allowed_commands"] == expected_commands


class TestEdgeCases:
    """Edge case and regression tests"""

    def test_get_with_jsonpath_output(self):
        """jsonpath output should work (no quotes)"""
        result = kr.validate_kubectl_args([
            "get", "pod", "my-pod",
            "-o", "jsonpath={.status.phase}"
        ])
        assert result[0] == "get"

    def test_describe_events_only(self):
        """describe with events flag"""
        result = kr.validate_kubectl_args(["describe", "pod", "my-pod", "--show-events=true"])
        assert result[0] == "describe"

    def test_get_with_watch(self):
        """get with watch flag"""
        result = kr.validate_kubectl_args(["get", "pods", "-w"])
        assert result == ["get", "pods", "-w"]

    def test_logs_since_duration(self):
        """logs with since duration"""
        result = kr.validate_kubectl_args(["logs", "my-pod", "--since=1h"])
        assert result == ["logs", "my-pod", "--since=1h"]

    def test_logs_since_time(self):
        """logs with since-time"""
        result = kr.validate_kubectl_args(["logs", "my-pod", "--since-time=2024-01-01T00:00:00Z"])
        assert result == ["logs", "my-pod", "--since-time=2024-01-01T00:00:00Z"]

    def test_rollout_pause(self):
        """rollout pause command"""
        result = kr.validate_kubectl_args(["rollout", "pause", "deployment/my-app"])
        assert result == ["rollout", "pause", "deployment/my-app"]

    def test_rollout_resume(self):
        """rollout resume command"""
        result = kr.validate_kubectl_args(["rollout", "resume", "deployment/my-app"])
        assert result == ["rollout", "resume", "deployment/my-app"]

    def test_drain_with_pod_selector(self):
        """drain with pod selector"""
        result = kr.validate_kubectl_args([
            "drain", "node-1",
            "--pod-selector=app!=critical",
            "--ignore-daemonsets"
        ])
        assert result[0] == "drain"

    def test_label_all_pods_in_namespace(self):
        """label all pods matching selector"""
        result = kr.validate_kubectl_args([
            "label", "pods", "-l", "app=nginx", "version=v2", "-n", "default"
        ])
        assert result[0] == "label"

    def test_scale_statefulset(self):
        """scale a statefulset"""
        result = kr.validate_kubectl_args(["scale", "statefulset", "my-db", "--replicas=3"])
        assert result == ["scale", "statefulset", "my-db", "--replicas=3"]

    def test_scale_replicaset(self):
        """scale a replicaset"""
        result = kr.validate_kubectl_args(["scale", "replicaset", "my-rs", "--replicas=0"])
        assert result == ["scale", "replicaset", "my-rs", "--replicas=0"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
