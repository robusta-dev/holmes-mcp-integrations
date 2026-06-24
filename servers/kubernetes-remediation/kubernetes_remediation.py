#!/usr/bin/env python3
"""
Kubernetes Remediation MCP Server

An MCP server that lets HolmesGPT diagnose *and* remediate a cluster beyond what
the agent pod's own RBAC allows. It runs as a pod inside the cluster and relies
on its ServiceAccount's RBAC for resource-level restrictions.

Approval legibility through tool separation
-------------------------------------------
Each tool is *either* always auto-approved *or* always approval-gated — the split
is encoded in the tool set, never guessed per-command:

  Auto-approved (read-only / data-gathering, never prompt):
    - read_file_from_container          (path allow/deny policy)
    - run_preapproved_kubectl_command   (read-only command allowlist)
    - run_preapproved_diagnostic_image              (troubleshooting image allowlist)
    - get_remediation_mcp_config        (effective policy, debugging)

  Approval-gated (mutations / arbitrary exec — HolmesGPT always prompts a human):
    - run_kubectl_command

All *policy* (command/image/path allowlists, the arbitrary toggle, the hard verb
allowlist, the flag blocklist) lives here in the server. HolmesGPT only maps
tool name -> approval via approval_required_tools.

Defense in depth (independent of approval):
    - Hard verb allowlist for run_kubectl_command
    - Dangerous flag blocklist
    - Shell metacharacter rejection (and shell=False everywhere)
    - Path policy can never read secret/token mounts
    - Per-command timeout
"""

import os
import subprocess
import json
import logging
import posixpath
import uuid
from typing import Any, Dict, List, Optional
import sys
import uvicorn

from fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _split_csv(value: str) -> List[str]:
    """Split a comma-separated env var into a clean list (no empties/whitespace)."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration (all policy lives here, in the server)
# ─────────────────────────────────────────────────────────────────────────────

# Hard verb allowlist for the approval-gated `run_kubectl_command`.
ALLOWED_COMMANDS = set(
    _split_csv(
        os.getenv(
            "KUBECTL_ALLOWED_COMMANDS",
            "edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec",
        )
    )
)

# Flags that are always blocked (credential/context hijacking, impersonation).
DANGEROUS_FLAGS = set(
    _split_csv(
        os.getenv(
            "KUBECTL_DANGEROUS_FLAGS",
            "--kubeconfig,--context,--cluster,--user,--token,--as,--as-group,--as-uid",
        )
    )
)

# Read-only diagnostic commands that run immediately (no human approval).
# Each entry is a `kubectl exec <target> [flags] -- <binary>` diagnostic; only
# the in-container <binary> is allowlisted (matching is structural, see
# match_preapproved_command — NOT a glob over the joined command). Deliberately
# excludes `cat` (use read_file_from_container) and `env` (leaks secrets).
PREAPPROVED_COMMANDS = _split_csv(
    os.getenv(
        "KUBECTL_PREAPPROVED_COMMANDS",
        "exec * -- ps*,exec * -- top*,exec * -- df*,exec * -- ls*,exec * -- netstat*,exec * -- ss*",
    )
)


def _preapproved_exec_binaries(patterns: List[str]) -> set:
    """
    Derive the set of allowlisted in-container binaries from the configured
    `exec ... -- <binary>` patterns.

    The preapproved path only auto-approves `kubectl exec ... -- <binary> [args]`
    diagnostics. From each pattern we take the token after the `--` separator and
    strip a trailing glob `*`, yielding the bare binary name (`exec * -- ps*` ->
    `ps`). Patterns that are not of this exec shape are ignored — they simply
    never auto-approve (fail safe to the approval-gated path).
    """
    binaries = set()
    for pattern in patterns:
        tokens = pattern.split()
        if not tokens or tokens[0] != "exec" or "--" not in tokens:
            continue
        rest = tokens[tokens.index("--") + 1:]
        if rest and rest[0]:
            binaries.add(rest[0].rstrip("*"))
    return binaries


PREAPPROVED_EXEC_BINARIES = _preapproved_exec_binaries(PREAPPROVED_COMMANDS)

# Pre-approved read-only troubleshooting images for run_preapproved_diagnostic_image.
# Matched on the repository (tag is supplied by the server from this pin).
DIAGNOSTIC_IMAGES = _split_csv(
    os.getenv(
        "KUBECTL_DIAGNOSTIC_IMAGES",
        "nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1",
    )
)

# read_file_from_container path policy.
FILE_READ_ALLOWED_PATHS = _split_csv(
    os.getenv("KUBECTL_FILE_READ_ALLOWED_PATHS", "/")
) or ["/"]
FILE_READ_DENIED_PATHS = _split_csv(
    os.getenv(
        "KUBECTL_FILE_READ_DENIED_PATHS",
        "/var/run/secrets/,/run/secrets/,/var/run/secrets/kubernetes.io/serviceaccount/",
    )
)

# Whether the approval-gated fallback is enabled at all.
ALLOW_ARBITRARY_COMMANDS = _env_bool("KUBECTL_ALLOW_ARBITRARY_COMMANDS", True)

TIMEOUT = int(os.getenv("KUBECTL_TIMEOUT", "60"))

# Shell metacharacters to reject (defense in depth even though shell=False).
SHELL_CHARS = set(";|&$`\\'\"\n\r")

# Pseudo-filesystem roots that are NEVER readable, regardless of the configured
# allow/deny lists (not operator-removable). The configured deny list is a
# string-prefix filter on the requested path; these roots are how that filter
# can be routed around, so they are blocked unconditionally:
#   /proc  -> /proc/<pid>/environ leaks env-injected secrets, and
#             /proc/<pid>/root/... reaches a secret mount by a path that is not
#             string-prefixed by any deny entry.
#   /sys   -> kernel/device internals.
#   /dev   -> raw devices (e.g. /dev/mem).
# None of these hold application source code, so blocking them costs nothing.
HARD_DENIED_PATHS = ["/proc", "/sys", "/dev"]


# Create MCP server
mcp = FastMCP(name="kubernetes-remediation", version="1.1.0")


# ─────────────────────────────────────────────────────────────────────────────
# Low-level execution
# ─────────────────────────────────────────────────────────────────────────────

def _run_kubectl(args: List[str]) -> Dict[str, Any]:
    """Execute kubectl with shell=False and a timeout. Returns a result dict."""
    try:
        logger.info(f"Executing kubectl with args: {args}")
        result = subprocess.run(
            ["kubectl"] + args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {TIMEOUT}s: kubectl {' '.join(args)}")
        return {
            "success": False,
            "error": f"Command timed out after {TIMEOUT} seconds",
            "stdout": "",
            "stderr": "",
        }
    except Exception as e:
        logger.error(f"Error executing kubectl: {e}")
        return {"success": False, "error": str(e), "stdout": "", "stderr": ""}


# ─────────────────────────────────────────────────────────────────────────────
# Validators
# ─────────────────────────────────────────────────────────────────────────────

def _reject_shell_chars(value: str, field: str) -> None:
    if any(c in value for c in SHELL_CHARS):
        raise ValueError(f"Invalid characters in {field}: {value!r}")


def _validate_identifier(value: str, field: str) -> None:
    """
    Validate a positional/option-value identifier (pod, namespace, container,
    pod name) that the auto-approved tools hand to kubectl.

    Beyond rejecting shell metacharacters, this rejects any value that begins
    with '-': kubectl would parse such a value as a flag rather than a
    positional, which is a flag-injection vector (e.g. pod="--kubeconfig=...")
    in tools that build their own kubectl invocation. The verb-based fallback
    (validate_kubectl_args) blocks flags via DANGEROUS_FLAGS instead; the
    dedicated tools have no legitimate use for a leading-'-' identifier at all.
    """
    _reject_shell_chars(value, field)
    if value.startswith("-"):
        raise ValueError(
            f"Invalid {field}: must not start with '-' (possible flag injection): {value!r}"
        )


def validate_kubectl_args(args: List[str]) -> List[str]:
    """
    Validate kubectl arguments for the approval-gated fallback.

    Enforces the hard verb allowlist, the dangerous-flag blocklist, and rejects
    shell metacharacters. Returns the validated args (with a leading 'kubectl'
    stripped if present). Raises ValueError on any violation.
    """
    if not args:
        raise ValueError("No arguments provided")

    if args[0] == "kubectl":
        args = args[1:]

    if not args:
        raise ValueError("No command provided after 'kubectl'")

    command = args[0]
    if command not in ALLOWED_COMMANDS:
        raise ValueError(
            f"Command '{command}' is not in the allowed verb list. "
            f"Allowed verbs: {', '.join(sorted(ALLOWED_COMMANDS))}"
        )

    for arg in args:
        flag = arg.split("=")[0]
        if flag in DANGEROUS_FLAGS:
            raise ValueError(f"Flag '{flag}' is not permitted")
        # Block --overrides flag (privilege escalation risk via pod spec).
        if flag == "--overrides":
            raise ValueError("Flag '--overrides' is not permitted")
        _reject_shell_chars(arg, "argument")

    return args


def _image_repository(image: str) -> str:
    """Return the repository part of an image reference (no tag, no digest)."""
    image = image.split("@", 1)[0]
    if "/" in image:
        prefix, last = image.rsplit("/", 1)
    else:
        prefix, last = "", image
    if ":" in last:
        last = last.split(":", 1)[0]
    return f"{prefix}/{last}" if prefix else last


def resolve_diagnostic_image(image: str) -> str:
    """
    Resolve a requested diagnostic image against the allowlist.

    Matching is on the repository; the pinned tag from the allowlist is what
    actually gets run (so the model can just name the repo). Raises ValueError
    if the repository is not allowlisted.
    """
    allowed_by_repo = {_image_repository(entry): entry for entry in DIAGNOSTIC_IMAGES}
    requested_repo = _image_repository(image)
    if requested_repo not in allowed_by_repo:
        raise ValueError(
            f"Image '{image}' is not a pre-approved diagnostic image. "
            f"Allowed images: {', '.join(sorted(DIAGNOSTIC_IMAGES))}. "
            f"To run an arbitrary image, use run_kubectl_command (requires human approval)."
        )
    return allowed_by_repo[requested_repo]


def _normalize_path(path: str) -> str:
    """Normalize an absolute container path, rejecting traversal and metachars."""
    _reject_shell_chars(path, "path")
    if not path.startswith("/"):
        raise ValueError(f"Path must be absolute: {path!r}")
    normalized = posixpath.normpath(path)
    # normpath collapses '..'; if any survive, the path tried to escape root.
    if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        raise ValueError(f"Path traversal is not permitted: {path!r}")
    return normalized


def _path_is_under(path: str, root: str) -> bool:
    """True if `path` is `root` itself or nested under `root`."""
    root = posixpath.normpath(root)
    if root == "/":
        return True
    return path == root or path.startswith(root.rstrip("/") + "/")


def _enforce_read_policy(candidate: str, original: str) -> None:
    """
    Enforce the read policy on an already-absolute path: hard-denied
    pseudo-filesystems, then the configured deny list, then the allow list.
    Denied wins ties. `original` is the user-supplied path, used in messages.
    Raises ValueError on refusal.
    """
    for hard in HARD_DENIED_PATHS:
        if _path_is_under(candidate, hard):
            raise ValueError(
                f"Path '{original}' is restricted: it resolves under '{hard}', a system "
                f"pseudo-filesystem that can expose secrets, env vars, or devices."
            )

    for denied in FILE_READ_DENIED_PATHS:
        if _path_is_under(candidate, denied):
            raise ValueError(
                f"Path '{original}' is restricted: it resolves under the denied path "
                f"'{denied}'. Secret and token mounts cannot be read."
            )

    if not any(_path_is_under(candidate, allowed) for allowed in FILE_READ_ALLOWED_PATHS):
        raise ValueError(
            f"Path '{original}' is not under any allowed root. "
            f"Allowed roots: {', '.join(FILE_READ_ALLOWED_PATHS)}."
        )


def validate_read_path(path: str) -> str:
    """
    Validate a requested file path against the allow/deny policy.

    This checks the *literal* path. Symlinks are resolved and re-checked
    separately, inside the container, by read_file_from_container (the canonical
    target can only be known there). Raises ValueError on refusal.
    """
    normalized = _normalize_path(path)
    _enforce_read_policy(normalized, path)
    return normalized


def _resolve_symlink_in_container(
    namespace: str, pod: str, container: Optional[str], path: str
) -> Optional[str]:
    """
    Best-effort: return the canonical path of `path` inside the container via
    `readlink -f`, or None if it can't be resolved (e.g. the container has no
    `readlink`). `path` is already validated to be absolute, so it cannot be
    parsed by readlink as a flag.
    """
    args = ["exec", pod, "-n", namespace]
    if container:
        args.extend(["-c", container])
    args.extend(["--", "readlink", "-f", path])
    try:
        result = subprocess.run(
            ["kubectl"] + args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except Exception as e:  # never let resolution failure crash the read
        logger.debug(f"symlink resolution failed for {path!r}: {e}")
        return None
    if result.returncode == 0:
        canonical = result.stdout.strip()
        return canonical or None
    return None


def match_preapproved_command(args: List[str]) -> bool:
    """
    True only for `kubectl exec <target> [flags] -- <binary> [args...]` where
    <binary> is on the preapproved read-only allowlist.

    Matching is structural, NOT a glob over the joined args. An earlier version
    joined the args and `fnmatch`-ed them against patterns like `exec * -- ps*`;
    because glob `*` translates to a greedy `.*` that spans spaces and the `--`
    separator, an arbitrary command could hide before a trailing allowlisted
    token — e.g. `exec pod -- rm -rf / -- ps` matched `exec * -- ps*` and ran
    `rm -rf /` auto-approved. We parse the structure instead and only ever look
    at the binary immediately after the FIRST `--`.
    """
    if not args or args[0] != "exec":
        return False
    if "--" not in args:
        return False
    # In-container argv, after the FIRST separator.
    cmd = args[args.index("--") + 1:]
    # A second `--` would let a real command hide after the allowlisted binary.
    if not cmd or "--" in cmd:
        return False
    # Only the binary (argv[0]) is checked: its trailing args cannot start a new
    # process (shell=False, single separator). Exact match also blocks lookalikes
    # such as `psql` that a `ps*` prefix glob would have allowed.
    return cmd[0] in PREAPPROVED_EXEC_BINARIES


# ─────────────────────────────────────────────────────────────────────────────
# Auto-approved tools
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="read_file_from_container",
    description=(
        "AUTO-APPROVED (runs immediately, no human needed). Read a single file "
        "from inside a running container — useful for config files and on-disk "
        "logs the agent's own pod cannot reach.\n\n"
        "The `path` is validated against the server's path policy BEFORE execution "
        "and symlinks are resolved and re-checked inside the container: it must be "
        "under an allowed root and under no denied root. Secret/token mounts "
        "(/var/run/secrets/, /run/secrets/) and the /proc, /sys, /dev "
        "pseudo-filesystems are always denied. Denied paths return a structured "
        "refusal naming the matched rule.\n\n"
        "Do NOT use this server for `get`/`describe`/`logs` — the built-in Kubernetes "
        "tools are faster and need no approval.\n\n"
        "Example: read_file_from_container(namespace=\"prod\", pod=\"api-xxx\", path=\"/app/config.yaml\")"
    ),
)
def read_file_from_container(
    namespace: str,
    pod: str,
    path: str,
    container: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Read a single file from inside a running container via `kubectl exec ... -- cat`.

    Args:
        namespace: Namespace of the pod (required)
        pod: Pod name (required)
        path: Absolute path of the file to read (validated against the path policy)
        container: Optional container name within the pod

    Returns:
        Dictionary with success status, stdout (file contents), stderr
    """
    try:
        _validate_identifier(namespace, "namespace")
        _validate_identifier(pod, "pod")
        if container:
            _validate_identifier(container, "container")
        validated_path = validate_read_path(path)
    except ValueError as e:
        logger.warning(f"read_file_from_container validation failed: {e}")
        return {"success": False, "error": str(e)}

    # Defense in depth against symlink routing around the path policy: resolve
    # the path inside the container and re-check the canonical target. A symlink
    # under an allowed root can otherwise point at a denied path (e.g. a secret
    # mount). Best-effort — if the container has no `readlink`, we fall back to
    # the literal-path checks above plus the hard /proc,/sys,/dev denial.
    canonical = _resolve_symlink_in_container(namespace, pod, container, validated_path)
    if canonical and canonical != validated_path and canonical.startswith("/"):
        try:
            _enforce_read_policy(posixpath.normpath(canonical), path)
        except ValueError as e:
            logger.warning(f"read_file_from_container refused after symlink resolution: {e}")
            return {"success": False, "error": str(e)}

    exec_args = ["exec", pod, "-n", namespace]
    if container:
        exec_args.extend(["-c", container])
    exec_args.extend(["--", "cat", validated_path])
    return _run_kubectl(exec_args)


@mcp.tool(
    name="run_preapproved_kubectl_command",
    description=(
        "AUTO-APPROVED (runs immediately, no human needed). Run a kubectl command "
        "from the operator's pre-approved read-only diagnostics allowlist (e.g. "
        "`exec ... -- ps/top/df/ls/netstat/ss`). Reach for this before the "
        "approval-gated fallback.\n\n"
        "If the command does not match the allowlist you get a structured refusal "
        "telling you to use run_kubectl_command (which requires human approval). "
        "To read a file use read_file_from_container instead of `cat`.\n\n"
        "Example: run_preapproved_kubectl_command(args=[\"exec\",\"api-xxx\",\"-n\",\"prod\",\"--\",\"ps\",\"aux\"])"
    ),
)
def run_preapproved_kubectl_command(args: List[str]) -> Dict[str, Any]:
    """
    Run a kubectl command from the pre-approved read-only allowlist.

    Args:
        args: Command arguments, e.g. ["exec", "api-xxx", "-n", "prod", "--", "ps", "aux"]

    Returns:
        Dictionary with success status, stdout, stderr
    """
    try:
        if args and args[0] == "kubectl":
            args = args[1:]
        if not args:
            raise ValueError("No command provided")
        # Defense in depth: still reject dangerous flags / shell metacharacters.
        for arg in args:
            flag = arg.split("=")[0]
            if flag in DANGEROUS_FLAGS:
                raise ValueError(f"Flag '{flag}' is not permitted")
            _reject_shell_chars(arg, "argument")
        if not match_preapproved_command(args):
            raise ValueError(
                f"Command {args!r} is not pre-approved. "
                f"Pre-approved patterns: {', '.join(PREAPPROVED_COMMANDS)}. "
                f"Use run_kubectl_command (requires human approval) for anything else."
            )
    except ValueError as e:
        logger.warning(f"run_preapproved_kubectl_command refused: {e}")
        return {"success": False, "error": str(e)}

    return _run_kubectl(args)


@mcp.tool(
    name="run_preapproved_diagnostic_image",
    description=(
        "AUTO-APPROVED (runs immediately, no human needed). Launch a short-lived pod "
        "from a pre-approved read-only troubleshooting image to gather data the agent "
        "cannot otherwise reach (network/DNS/HTTP probing, etc.). The server picks the "
        "pinned tag, captures the output, and auto-deletes the pod.\n\n"
        "Pre-approved images: nicolaka/netshoot (dig, curl, tcpdump, netstat, ss, "
        "nslookup, iperf), busybox (ls, cat, ps, wget, nslookup), curlimages/curl "
        "(HTTP/endpoint reachability). A non-allowlisted image returns a structured "
        "refusal listing the allowed images and pointing to run_kubectl_command.\n\n"
        "Example: run_preapproved_diagnostic_image(image=\"nicolaka/netshoot\", namespace=\"prod\", command=[\"dig\",\"my-svc\"])"
    ),
)
def run_preapproved_diagnostic_image(
    image: str,
    namespace: str,
    command: Optional[List[str]] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run a short-lived pod from a pre-approved diagnostic image and return its output.

    Args:
        image: Diagnostic image repository (e.g. "nicolaka/netshoot"); must be allowlisted
        namespace: Namespace to run the pod in (required)
        command: Optional command to run in the container
        name: Optional pod name (generated if omitted)

    Returns:
        Dictionary with success status, stdout, stderr
    """
    try:
        _validate_identifier(namespace, "namespace")
        resolved_image = resolve_diagnostic_image(image)
        if command:
            # Command tokens run inside the diagnostic container (after `--`), so
            # leading '-' is legitimate here (e.g. curl -s); only block shell chars.
            for part in command:
                _reject_shell_chars(part, "command")
        if name:
            _validate_identifier(name, "name")
            pod_name = name
        else:
            image_base = _image_repository(image).split("/")[-1].lower()
            image_base = "".join(c if c.isalnum() else "-" for c in image_base)[:20]
            pod_name = f"k8s-remediation-{image_base}-{uuid.uuid4().hex[:8]}"
    except ValueError as e:
        logger.warning(f"run_preapproved_diagnostic_image refused: {e}")
        return {"success": False, "error": str(e)}

    # Harden the short-lived diagnostic pod without crippling network tooling.
    # We control this override (not the caller), so it is safe to use here even
    # though --overrides is blocked on the approval-gated fallback:
    #   - automountServiceAccountToken: false  removes API access the pod never
    #     needs (a real escalation vector) and does not affect net/DNS/HTTP probes.
    #   - allowPrivilegeEscalation: false       blocks setuid escalation.
    #   - memory limit + requests                cap node impact; NO cpu limit so
    #     throughput tests (iperf) aren't throttled, and capabilities are left
    #     untouched so tcpdump/ping still work.
    overrides = {
        "spec": {
            "automountServiceAccountToken": False,
            "containers": [
                {
                    "name": pod_name,
                    "securityContext": {"allowPrivilegeEscalation": False},
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"memory": "256Mi"},
                    },
                }
            ],
        }
    }
    run_args = [
        "run",
        pod_name,
        f"--image={resolved_image}",
        "--restart=Never",
        "--rm",
        "-i",
        "-n",
        namespace,
        "--override-type=strategic",
        "--overrides",
        json.dumps(overrides),
    ]
    if command:
        run_args.append("--command")
        run_args.append("--")
        run_args.extend(command)

    try:
        return _run_kubectl(run_args)
    finally:
        # Cleanup is on by default: ensure the pod is gone even if --rm didn't
        # fire (e.g. on timeout). Best-effort, never blocks.
        subprocess.run(
            ["kubectl", "delete", "pod", pod_name, "-n", namespace,
             "--ignore-not-found", "--wait=false"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )


@mcp.tool(
    name="get_remediation_mcp_config",
    description=(
        "AUTO-APPROVED. Return the live effective policy of this server (verb "
        "allowlist, dangerous flags, pre-approved commands, diagnostic images, "
        "file-read allow/deny paths, the arbitrary-command toggle, and the timeout) "
        "for debugging."
    ),
)
def get_remediation_mcp_config() -> Dict[str, Any]:
    """Return the current effective server configuration."""
    return {
        "allowed_commands": sorted(ALLOWED_COMMANDS),
        "dangerous_flags": sorted(DANGEROUS_FLAGS),
        "preapproved_commands": list(PREAPPROVED_COMMANDS),
        "diagnostic_images": list(DIAGNOSTIC_IMAGES),
        "file_read_allowed_paths": list(FILE_READ_ALLOWED_PATHS),
        "file_read_denied_paths": list(FILE_READ_DENIED_PATHS),
        "allow_arbitrary_kubectl_commands": ALLOW_ARBITRARY_COMMANDS,
        "timeout_seconds": TIMEOUT,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Approval-gated fallback
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="run_kubectl_command",
    description=(
        "ALWAYS REQUIRES HUMAN APPROVAL — expect a wait. The catch-all for everything "
        "the auto-approved tools can't do: all mutations (edit/patch/delete/scale/"
        "rollout/cordon/drain/taint/label/annotate), arbitrary exec, and running "
        "non-allowlisted images via `kubectl run`. Reach for the no-approval tools "
        "first; use this only when a pre-approved tool can't accomplish the task, and "
        "express the full intent in one clear command.\n\n"
        "Refused (independent of approval): verbs outside the hard allowlist, blocked "
        "flags (--kubeconfig/--context/--token/--as/...), --overrides, and shell "
        "metacharacters. When the server runs in locked-down mode "
        "(allowArbitraryKubectlCommands=false) this tool is disabled.\n\n"
        "Example: run_kubectl_command(args=[\"rollout\",\"restart\",\"deployment/api\",\"-n\",\"prod\"])"
    ),
)
def run_kubectl_command(args: List[str]) -> Dict[str, Any]:
    """
    Execute an arbitrary (verb-allowlisted) kubectl command. Mutating; HolmesGPT
    gates this behind human approval via approval_required_tools.

    Args:
        args: Command arguments, e.g. ["rollout", "restart", "deployment/api", "-n", "prod"]

    Returns:
        Dictionary with success status, stdout, stderr
    """
    if not ALLOW_ARBITRARY_COMMANDS:
        return {
            "success": False,
            "error": (
                "run_kubectl_command is disabled: the server is in locked-down mode "
                "(allowArbitraryKubectlCommands=false). Only the auto-approved tools "
                "are available."
            ),
        }
    try:
        validated_args = validate_kubectl_args(args)
    except ValueError as e:
        logger.warning(f"run_kubectl_command validation failed: {e}")
        return {"success": False, "error": str(e)}
    return _run_kubectl(validated_args)


# Main entry point
if __name__ == "__main__":
    logger.info("Starting Kubernetes Remediation MCP Server")
    logger.info(f"Allowed verbs (run_kubectl_command): {sorted(ALLOWED_COMMANDS)}")
    logger.info(f"Dangerous flags: {sorted(DANGEROUS_FLAGS)}")
    logger.info(f"Pre-approved commands: {PREAPPROVED_COMMANDS}")
    logger.info(f"Diagnostic images: {DIAGNOSTIC_IMAGES}")
    logger.info(f"File-read allowed paths: {FILE_READ_ALLOWED_PATHS}")
    logger.info(f"File-read denied paths: {FILE_READ_DENIED_PATHS}")
    logger.info(f"Allow arbitrary kubectl commands: {ALLOW_ARBITRARY_COMMANDS}")
    logger.info(f"Timeout: {TIMEOUT}s")

    if "--transport" in sys.argv and "http" in sys.argv:
        logger.info("Starting in HTTP transport mode")
        host = "0.0.0.0"
        port = 8000

        if "--host" in sys.argv:
            host_idx = sys.argv.index("--host") + 1
            if host_idx < len(sys.argv):
                host = sys.argv[host_idx]

        if "--port" in sys.argv:
            port_idx = sys.argv.index("--port") + 1
            if port_idx < len(sys.argv):
                port = int(sys.argv[port_idx])

        uvicorn.run(mcp.http_app(), host=host, port=port, log_level="info")
    else:
        mcp.run()
