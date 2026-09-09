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
    - run_preapproved_kubectl_exec_command  (read-only in-container binary allowlist)
    - run_preapproved_diagnostic_image      (troubleshooting image allowlist)
    - run_gpu_node_diagnostics          (named GPU/driver checks via a node-pinned
                                         privileged pod that runs the NODE'S OWN
                                         binaries through the host filesystem,
                                         mounted read-only; auto-approvable
                                         because the caller picks only check
                                         NAMES — every command is server-owned)
    - get_remediation_mcp_config        (effective policy, debugging)

  Approval-gated (HolmesGPT always prompts a human):
    - run_kubectl_command               (mutations / arbitrary exec)

All *policy* (command/image/path allowlists, the arbitrary toggle, the hard verb
allowlist, the flag blocklist) lives here in the server. HolmesGPT only maps
tool name -> approval via approval_required_tools.

Defense in depth (independent of approval):
    - Hard verb allowlist for run_kubectl_command
    - Dangerous flag blocklist
    - Shell metacharacter rejection (and shell=False everywhere)
    - Path policy can never read secret/token mounts
    - Diagnostic-pod target policy: cloud-metadata/link-local/loopback refused in
      every IP spelling, external targets off by default, redirect-following
      refused — plus an egress NetworkPolicy on the pod as the CNI-enforced
      backstop (diagnostic-pod-networkpolicy.yaml)
    - Per-command timeout
"""

import os
import subprocess
import hmac
import ipaddress
import json
import logging
import posixpath
import re
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

# Read-only diagnostic binaries that may run inside a container via
# `run_preapproved_kubectl_exec_command` (auto-approved, no human approval). The
# server builds `kubectl exec <pod> -n <ns> [-c <container>] -- <binary> [args]`
# itself, so only the bare binary name is configured — no patterns, no wildcards.
# Deliberately excludes `cat` (use read_file_from_container) and `env` (leaks
# secrets).
PREAPPROVED_EXEC_BINARIES = set(
    _split_csv(
        os.getenv("KUBECTL_PREAPPROVED_EXEC_BINARIES", "ps,top,df,ls,netstat,ss")
    )
)

# Pre-approved read-only troubleshooting images for run_preapproved_diagnostic_image.
# Matched on the repository (tag is supplied by the server from this pin).
DIAGNOSTIC_IMAGES = _split_csv(
    os.getenv(
        "KUBECTL_DIAGNOSTIC_IMAGES",
        "nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1",
    )
)

# run_preapproved_diagnostic_image target policy.
#
# The diagnostic images are network-probing tools (curl/dig/wget/tcpdump) and the
# tool is auto-approved, so the *targets* are the security boundary: without one,
# prompt-injected agent output can point curl at the cloud metadata service or at
# an external collector and read the response back (ROB-910).
#
# Targets are classified as cluster-internal or external. External targets are
# refused unless the operator opts in here; link-local/metadata/loopback are
# refused unconditionally by the policy (see DIAGNOSTIC_HARD_DENIED_* below).
ALLOW_EXTERNAL_DIAGNOSTIC_TARGETS = _env_bool(
    "KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS", False
)

# Master switch for the whole target policy. Escape hatch for operators whose
# environment the policy misjudges (a custom cluster domain the suffix list can't
# express, probing an appliance on a public address, etc.).
#
# Setting this false turns off *every* target check, including the
# unconditionally-denied metadata ranges, and restores the pre-ROB-910 behaviour:
# an auto-approved probe can then be aimed at the cloud metadata service and its
# response returned to the agent. Prefer the narrower
# KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS, or route the specific call through
# the approval-gated run_kubectl_command, before reaching for this.
DIAGNOSTIC_TARGET_POLICY_ENABLED = _env_bool(
    "KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED", True
)

# DNS suffixes treated as cluster-internal. Override for a custom cluster domain.
DIAGNOSTIC_INTERNAL_DNS_SUFFIXES = _split_csv(
    os.getenv(
        "KUBECTL_DIAGNOSTIC_INTERNAL_DNS_SUFFIXES",
        ".svc,.svc.cluster.local,.cluster.local",
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

# Networks a diagnostic pod may NEVER be pointed at, regardless of
# KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS (not operator-removable, same
# rationale as HARD_DENIED_PATHS). These hold credentials or node-local
# services that a network probe has no legitimate reason to reach:
#   169.254.0.0/16   link-local: AWS/Azure/OpenStack IMDS (169.254.169.254),
#                    ECS task metadata (169.254.170.2). Reachable from a pod
#                    even with automountServiceAccountToken:false, and IMDSv1
#                    /GCP/Azure need no pod credential at all.
#   127.0.0.0/8, ::1 loopback: the node's own kubelet/sidecar ports.
#   0.0.0.0/8, ::    "this host" — another spelling of loopback.
#   100.100.100.200  Alibaba Cloud metadata (a /32, so CGNAT-addressed
#                    clusters are unaffected).
#   192.0.0.192      Oracle Cloud metadata.
#   fe80::/10        IPv6 link-local.
#   fd00:ec2::254    AWS IPv6 IMDS.
DIAGNOSTIC_HARD_DENIED_NETWORKS = [
    "169.254.0.0/16",
    "127.0.0.0/8",
    "0.0.0.0/8",
    "100.100.100.200/32",
    "192.0.0.192/32",
    "::1/128",
    "::/128",
    "fe80::/10",
    "fd00:ec2::254/128",
]

# Hostnames that resolve to a metadata service. Blocked by name as well as by
# address, because the name is what the agent would typically use and DNS is
# resolved inside the diagnostic pod (i.e. after our checks).
DIAGNOSTIC_HARD_DENIED_HOSTNAMES = {
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "instance-data.ec2.internal",
}

# curl/wget flags that make the *effective* target differ from the target we
# validated, by following a server-controlled redirect. An in-cluster service
# under attacker influence can 302 to 169.254.169.254, so these are refused
# even when external targets are permitted.
DIAGNOSTIC_REDIRECT_FLAGS = {
    "-L",
    "--location",
    "--location-trusted",
}

# Binaries whose redirect-following is worth blocking (see above). Only these get
# the bundled-short-flag check, so `ls -L` in busybox is not caught by it.
DIAGNOSTIC_HTTP_CLIENTS = {"curl", "wget"}

# ── GPU node diagnostics ──────────────────────────────────────────────────────
#
# One tool, AUTO-APPROVED: run_gpu_node_diagnostics launches a short-lived pod
# pinned to the node — privileged, hostPID, with the host root mounted
# READ-ONLY at /host — and runs the NODE'S OWN binaries (nvidia-smi, dmesg,
# lspci, modinfo, journalctl) via `chroot /host`. No special image: the pod
# image only supplies a shell (busybox). nvidia-smi is resolved from the host
# PATH, with a fallback to /run/nvidia/driver for GPU Operator clusters that
# run the containerized driver. The dcgm_* checks run `dcgmi` from the host
# too, and are opt-in (DCGM_ENABLED) since not every node has DCGM installed.
#
# What makes this auto-approvable under the "approval is a property of the
# tool" design: the caller picks only the node and check NAMES (plus a couple
# of strictly validated scalars: a pid, a PCI bus id, a dcgm diag level). Every
# command string is server-owned — nothing the model sends is interpolated into
# a shell except those validated scalars — so even prompt-injected content can
# only trigger the fixed read-only checks, never compose a command.

# Master switch for GPU node diagnostics.
GPU_DIAG_ENABLED = _env_bool("GPU_DIAG_ENABLED", True)

# Pod image: only needs a shell — every diagnostic binary comes from the node.
GPU_DIAG_IMAGE = os.getenv("GPU_DIAG_IMAGE", "busybox:1.37.0")

# DCGM checks (dcgmi discovery/health/diag) are opt-in. When enabled, dcgmi is
# assumed to be installed ON THE HOST (with its nv-hostengine service running)
# and runs via `chroot /host` like every other check; if it isn't there, the
# check returns dcgmi's own error verbatim. Off by default.
DCGM_ENABLED = _env_bool("DCGM_ENABLED", False)
# Highest `dcgmi diag -r <level>` the auto-approved tool may run. Levels 2 and 3
# take minutes and level 3 actively stresses the GPU, so operators opt in.
GPU_DIAG_DCGM_MAX_DIAG_LEVEL = int(os.getenv("GPU_DIAG_DCGM_MAX_DIAG_LEVEL", "1"))

# Namespace the diagnostic pods run in (the Helm chart sets the release
# namespace, where the diagnostic-pod egress NetworkPolicy is applied).
GPU_DIAG_NAMESPACE = os.getenv("GPU_DIAG_NAMESPACE", "default")

# Per-check timeout. Larger than KUBECTL_TIMEOUT because the first run on a
# node pulls the CUDA/DCGM image, and dcgmi diag takes a minute by itself.
GPU_DIAG_TIMEOUT = int(os.getenv("GPU_DIAG_TIMEOUT", "300"))

# The nvidia-smi field list for utilization sampling (kept out of the f-string
# below for readability).
_GPU_UTIL_QUERY = (
    "--query-gpu=timestamp,name,temperature.gpu,utilization.gpu,"
    "memory.used,memory.total,clocks_throttle_reasons.active"
)

# Every requested check runs under one `sh -c` script INSIDE the throwaway
# host pod; the script starts with this prelude, which resolves the NODE'S OWN
# nvidia-smi: on the host PATH (driver installed on the host — most managed
# GPU node images), or under /run/nvidia/driver (GPU Operator's containerized
# driver, whose root is bind-visible through the /host mount). No image ever
# supplies nvidia-smi.
_NVSMI_PRELUDE = (
    "if chroot /host sh -c 'command -v nvidia-smi' >/dev/null 2>&1; then "
    "nvsmi() { chroot /host nvidia-smi \"$@\"; }; "
    "elif [ -x /host/run/nvidia/driver/usr/bin/nvidia-smi ]; then "
    "nvsmi() { chroot /host/run/nvidia/driver nvidia-smi \"$@\"; }; "
    "else "
    "nvsmi() { echo 'nvidia-smi not found on the node (checked the host PATH "
    "and /run/nvidia/driver for the GPU Operator containerized driver)'; return 1; }; "
    "fi"
)

# nvidia-smi checks — the node's own binary via the prelude above. Every string
# is a server-owned constant — callers select by name only — so the shell here
# is not an injection surface.
GPU_CHECKS: Dict[str, str] = {
    # nvidia-smi: driver alive? temperature, power, memory, ECC summary, processes
    "overview": "nvsmi",
    # full per-GPU detail: throttle reasons, ECC counts, retired pages, clocks
    "details": "nvsmi -q",
    # throttling investigation: temperature/power/clock sections only
    "throttling": "nvsmi -q -d TEMPERATURE,POWER,CLOCK",
    # ~30s of live samples (6 samples, 5s apart) to catch transient spikes
    "utilization_samples": (
        f"nvsmi {_GPU_UTIL_QUERY} --format=csv; "
        f"for i in 1 2 3 4 5; do sleep 5; "
        f"nvsmi {_GPU_UTIL_QUERY} --format=csv,noheader; done"
    ),
    # volatile + aggregate ECC error counts
    "ecc": "nvsmi -q -d ECC",
    # retired pages (pending retirement => node needs a reboot)
    "page_retirement": "nvsmi -q -d PAGE_RETIREMENT",
    # A100/H100-generation equivalent of page retirement
    "row_remapper": "nvsmi -q -d ROW_REMAPPER",
    # which processes hold GPU memory (zombie/leak hunting)
    "compute_processes": (
        "nvsmi --query-compute-apps=pid,process_name,used_memory --format=csv"
    ),
}

# DCGM check commands — the host's own dcgmi, like everything else. Opt-in via
# DCGM_ENABLED; when enabled, dcgmi (and its nv-hostengine service) is assumed
# to be installed on the host, and its own error is returned verbatim if not.
DCGM_CHECKS: Dict[str, str] = {
    # does DCGM see the GPUs at all
    "dcgm_discovery": "chroot /host dcgmi discovery -l",
    # background health watches: set watches on group 0 (all GPUs), then check
    "dcgm_health": (
        "chroot /host dcgmi health -g 0 -s a >/dev/null 2>&1; "
        "chroot /host dcgmi health -g 0 -c"
    ),
    # active diagnostic; the -r level is appended after validation
    "dcgm_diag": "chroot /host dcgmi diag -r",
}

# Kernel/driver/PCIe checks. Same pod, same rules: host binaries run via
# `chroot /host`, and the host's processes/kernel are visible through the pod's
# own /proc (shared kernel + hostPID). {pid} / {bus_id} placeholders are filled
# only with values that passed the strict validators below.
HOST_CHECKS: Dict[str, str] = {
    # XID errors, "GPU has fallen off the bus", driver load failures
    "kernel_gpu_errors": (
        "(chroot /host dmesg -T 2>/dev/null || dmesg) "
        "| grep -iE 'xid|nvrm|nvidia' | tail -n 200 "
        "|| echo 'no NVIDIA-related kernel messages found'"
    ),
    # last 2h of kernel journal mentions (systemd hosts)
    "kernel_log_journal": (
        "chroot /host journalctl -k --since '-2h' --no-pager 2>&1 "
        "| grep -i nvidia | tail -n 200 "
        "|| echo 'journalctl unavailable or no NVIDIA kernel-journal entries in the last 2h'"
    ),
    # kernel module loaded? module version vs /proc/driver/nvidia/version
    # (a mismatch here is the classic post-driver-upgrade 'Driver/library
    # version mismatch' failure)
    "driver_info": (
        "echo '== loaded nvidia kernel modules (lsmod) =='; "
        "lsmod | grep -i nvidia || echo 'no nvidia modules loaded'; "
        "echo; echo '== modinfo nvidia (on-disk module) =='; "
        "chroot /host modinfo nvidia 2>&1 | head -n 20; "
        "echo; echo '== /proc/driver/nvidia/version (running driver) =='; "
        "cat /proc/driver/nvidia/version 2>&1"
    ),
    # does the PCIe bus even see the GPU
    "pci": (
        "chroot /host lspci 2>/dev/null | grep -i nvidia "
        "|| { echo 'lspci unavailable on host; scanning /sys for NVIDIA (0x10de) devices:'; "
        "grep -li 0x10de /sys/bus/pci/devices/*/vendor 2>/dev/null "
        "|| echo 'no NVIDIA PCI devices found'; }"
    ),
    # PCIe link width/speed degradation for one device ({bus_id} validated)
    "pci_link": (
        "chroot /host lspci -vvv -s {bus_id} 2>&1 | grep -iE 'lnk|lnkcap|lnksta' "
        "|| echo 'lspci unavailable on host or no link info for device {bus_id}'"
    ),
    # nvidia-fabricmanager must run on NVSwitch (HGX A100/H100) systems
    "fabric_manager": (
        "chroot /host systemctl status nvidia-fabricmanager --no-pager 2>&1 "
        "|| { echo '--- systemctl unavailable, falling back to process check ---'; "
        "ps 2>/dev/null | grep -i 'fabricmanager' | grep -v grep "
        "|| echo 'nv-fabricmanager process not found'; }"
    ),
    # which host processes hold /dev/nvidia* open (zombie processes keeping
    # GPU memory allocated after their pod died)
    "gpu_device_holders": (
        "found=0; "
        "for p in /proc/[0-9]*; do "
        "if ls -l \"$p/fd\" 2>/dev/null | grep -q '/dev/nvidia'; then "
        "echo \"pid $(basename \"$p\") comm=$(cat \"$p/comm\" 2>/dev/null)\"; found=1; "
        "fi; done; "
        "if [ \"$found\" -eq 0 ]; then echo 'no processes holding /dev/nvidia* devices'; fi"
    ),
    # inspect one host process by pid ({pid} validated numeric)
    "process_info": (
        "echo '== /proc/{pid} =='; ls -la /proc/{pid}/ 2>&1; "
        "echo; echo '== status =='; head -n 25 /proc/{pid}/status 2>&1; "
        "echo; echo '== cmdline =='; tr '\\0' ' ' < /proc/{pid}/cmdline 2>/dev/null; echo"
    ),
}

# Checks that take a parameter, and the parameter they require.
HOST_CHECK_REQUIRED_PARAM = {"pci_link": "pci_bus_id", "process_info": "pid"}

_PCI_BUS_ID_RE = re.compile(r"^[0-9a-fA-F:.]{1,16}$")

# The always-available catalog; the opt-in DCGM_CHECKS run in the same pod
# when DCGM_ENABLED is set.
NODE_CHECKS: Dict[str, str] = {**GPU_CHECKS, **HOST_CHECKS}


# Create MCP server
mcp = FastMCP(name="kubernetes-remediation", version="1.3.0")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP transport authentication
# ─────────────────────────────────────────────────────────────────────────────

class BearerAuthMiddleware:
    """ASGI middleware requiring `Authorization: Bearer <token>` on every request.

    The HTTP transport exposes the full tool surface (including cluster
    mutations via the pod's elevated ServiceAccount) to anyone who can reach
    the socket, so callers must be authenticated server-side; the client-side
    human-approval gate only binds callers that go through HolmesGPT.
    Only the shared-token check lives here — everything else (verb allowlist,
    flag blocklist, path policy) still runs in the tools themselves.
    """

    def __init__(self, app, token: str):
        self.app = app
        self._expected = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return

        provided = b""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                scheme, _, credentials = value.partition(b" ")
                if scheme.lower() == b"bearer":
                    provided = credentials.strip()
                break

        if not hmac.compare_digest(provided, self._expected):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = json.dumps(
                {"error": "Unauthorized: missing or invalid bearer token"}
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


def build_http_app():
    """Build the ASGI app for the HTTP transport, wrapping it with bearer-token
    auth when MCP_AUTH_TOKEN is set. Unset keeps the pre-1.2.0 behavior
    (unauthenticated) so existing manual deployments don't break on upgrade."""
    app = mcp.http_app()
    token = os.getenv("MCP_AUTH_TOKEN", "")
    if token:
        logger.info("HTTP transport authentication enabled (MCP_AUTH_TOKEN is set)")
        return BearerAuthMiddleware(app, token)
    logger.warning(
        "MCP_AUTH_TOKEN is not set — the HTTP transport accepts UNAUTHENTICATED "
        "requests, giving anyone who can reach this port the full tool surface "
        "with this pod's ServiceAccount RBAC. Set MCP_AUTH_TOKEN (the Holmes "
        "Helm chart does this automatically) or restrict access with a "
        "NetworkPolicy."
    )
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Low-level execution
# ─────────────────────────────────────────────────────────────────────────────

def _run_kubectl(args: List[str], timeout: Optional[int] = None) -> Dict[str, Any]:
    """Execute kubectl with shell=False and a timeout. Returns a result dict."""
    effective_timeout = timeout if timeout is not None else TIMEOUT
    try:
        logger.info(f"Executing kubectl with args: {args}")
        result = subprocess.run(
            ["kubectl"] + args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        logger.error(
            f"Command timed out after {effective_timeout}s: kubectl {' '.join(args)}"
        )
        return {
            "success": False,
            "error": f"Command timed out after {effective_timeout} seconds",
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


# ── diagnostic-pod target policy ─────────────────────────────────────────────
#
# The checks below decide what a network probe launched by the auto-approved
# run_preapproved_diagnostic_image may be pointed at. They are the *first* of two
# layers; the second is the egress NetworkPolicy in
# diagnostic-pod-networkpolicy.yaml, which is what actually contains a target we
# failed to recognise here (DNS that resolves to link-local only inside the pod,
# a redirect we did not block, wget's follow-by-default). Neither layer is
# sufficient alone: this one gives the model a legible refusal it can correct,
# the NetworkPolicy gives CNI-enforced containment.

_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$"
)


def _parse_ipv4_component(part: str) -> Optional[int]:
    """Parse one component of an inet_aton-style address (hex/octal/decimal)."""
    if not part:
        return None
    try:
        if part.lower().startswith("0x"):
            return int(part, 16)
        if part.startswith("0") and len(part) > 1:
            return int(part, 8)
        if not part.isdigit():
            return None
        return int(part, 10)
    except ValueError:
        return None


def _decode_ipv4_variants(text: str) -> Optional[Any]:
    """
    Decode the non-dotted-quad IPv4 spellings that inet_aton (and therefore
    curl/wget/ping) accepts, so `http://2852039166/` and `http://0xA9FEA9FE/`
    are recognised as 169.254.169.254 rather than treated as hostnames.

    Accepts a.b.c.d, a.b.c, a.b and a, each component decimal, 0-prefixed octal
    or 0x-prefixed hex. A single bare component must exceed 0xFFFFFF to count as
    an address, so ordinary numeric arguments (`--max-time 5`, `-p 53`, MTU
    `1500`) are not misread as 0.0.0.5 / 0.0.0.53 / 0.0.5.220.
    """
    parts = text.split(".")
    if len(parts) > 4:
        return None
    values = [_parse_ipv4_component(p) for p in parts]
    if any(v is None for v in values):
        return None

    n = len(values)
    # Every component except the last is a single byte; the last absorbs the
    # remaining (5 - n) bytes (so a.b.c.d -> 1, a.b.c -> 2, a.b -> 3, a -> 4).
    if any(v < 0 or v > 0xFF for v in values[:-1]):
        return None
    trailing_bytes = 5 - n
    if values[-1] < 0 or values[-1] > 256 ** trailing_bytes - 1:
        return None
    if n == 1 and values[0] <= 0xFFFFFF:
        return None

    packed = 0
    for v in values[:-1]:
        packed = (packed << 8) | v
    packed = (packed << (8 * trailing_bytes)) | values[-1]
    try:
        return ipaddress.IPv4Address(packed)
    except ValueError:
        return None


def _literal_ip(host: str) -> Optional[Any]:
    """Return the IP address `host` denotes, in any spelling, else None."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = _decode_ipv4_variants(host)
    if addr is None:
        return None
    # ::ffff:169.254.169.254 must be judged as the v4 address it carries.
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped or addr


def _strip_port(value: str) -> str:
    """Strip a trailing :port and IPv6 brackets from an authority."""
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            return host
    return value


def _candidate_target_hosts(token: str) -> List[str]:
    """
    Extract host-like values from one command token.

    Every token is inspected, not just the ones we expect to be targets, so
    values that reach the network through a flag are covered too: `-x
    http://collector` and `--proxy=socks5://collector` route the real connection
    at the proxy host, and `dig @169.254.169.254 svc` at the @server.
    """
    t = token.strip()
    if not t:
        return []
    if t.startswith("@"):  # dig/nslookup server syntax
        t = t[1:]
    if "://" in t:  # covers bare URLs and --flag=scheme://host
        authority = t.split("://", 1)[1].split("/", 1)[0]
        authority = authority.split("?", 1)[0].split("#", 1)[0]
        if "@" in authority:  # strip userinfo
            authority = authority.rsplit("@", 1)[1]
        return [_strip_port(authority)] if authority else []
    if t.startswith("-"):
        # A flag: only its inline value can name a host (`--proxy=host:3128`).
        if "=" not in t:
            return []
        t = t.split("=", 1)[1]
    return [_strip_port(t)] if t else []


def _is_internal_hostname(host: str) -> bool:
    """True if `host` is a cluster-internal DNS name (bare service name or a
    configured cluster suffix). Namespace-qualified shortcuts like
    `kubernetes.default` are deliberately NOT internal: they are
    indistinguishable from `evil.com` by shape, and the refusal tells the caller
    to use the FQDN."""
    lowered = host.lower().rstrip(".")
    if "." not in lowered:
        return True
    return any(
        lowered.endswith(suffix.lower()) for suffix in DIAGNOSTIC_INTERNAL_DNS_SUFFIXES
    )


def _assert_target_not_hard_denied(host: str, token: str) -> Optional[Any]:
    """
    Raise ValueError if `host` is a target no configuration may permit
    (metadata/link-local/loopback). Returns the literal IP `host` denotes, if
    any, so the caller does not have to decode it twice.
    """
    lowered = host.lower().rstrip(".")
    if not lowered:
        return None

    if lowered in DIAGNOSTIC_HARD_DENIED_HOSTNAMES:
        raise ValueError(
            f"Target {host!r} in {token!r} is a cloud metadata endpoint and is always "
            f"refused: it can return instance credentials to the agent. This is not "
            f"operator-configurable. Use run_kubectl_command (requires human approval) "
            f"if a human has judged this specific request safe."
        )

    ip = _literal_ip(lowered)
    if ip is not None:
        for cidr in DIAGNOSTIC_HARD_DENIED_NETWORKS:
            network = ipaddress.ip_network(cidr)
            if ip.version == network.version and ip in network:
                raise ValueError(
                    f"Target {host!r} in {token!r} resolves to {ip}, inside "
                    f"{cidr}, which is always refused for diagnostic pods "
                    f"(cloud metadata / link-local / loopback — these return "
                    f"instance credentials or expose node-local services). This is "
                    f"not operator-configurable. Use run_kubectl_command (requires "
                    f"human approval) if a human has judged this request safe."
                )
    return ip


def _assert_target_in_scope(host: str, token: str, ip: Optional[Any]) -> None:
    """
    Raise ValueError if `host` is outside the configured target scope. Assumes
    _assert_target_not_hard_denied has already passed for this host.
    """
    if ALLOW_EXTERNAL_DIAGNOSTIC_TARGETS:
        return
    lowered = host.lower().rstrip(".")
    if not lowered:
        return

    if ip is not None:
        if ip.is_private:
            return
        raise ValueError(
            f"Target {host!r} in {token!r} is outside the cluster. Diagnostic pods "
            f"are restricted to in-cluster targets so an auto-approved probe cannot "
            f"send cluster data to an external host. Set "
            f"KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS=true to permit external "
            f"probing, or use run_kubectl_command (requires human approval)."
        )

    if not _HOSTNAME_RE.match(lowered):
        return  # not a host at all (e.g. a header value, a format string)

    if _is_internal_hostname(lowered):
        return
    raise ValueError(
        f"Target {host!r} in {token!r} is not a recognised cluster-internal name. "
        f"Diagnostic pods are restricted to in-cluster targets so an auto-approved "
        f"probe cannot send cluster data to an external host. Use the fully-qualified "
        f"service name (e.g. 'svc.namespace.svc.cluster.local'), or set "
        f"KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS=true to permit external probing, "
        f"or use run_kubectl_command (requires human approval)."
    )


def _assert_no_redirect_following(command: List[str]) -> None:
    """
    Refuse explicit redirect-following for HTTP clients.

    Following a redirect makes the effective target server-controlled: an
    in-cluster service can 302 to 169.254.169.254 and defeat the target checks
    above. Note wget follows redirects by default and cannot be argument-checked
    this way — that residual case is what the egress NetworkPolicy covers.
    """
    if not command or command[0] not in DIAGNOSTIC_HTTP_CLIENTS:
        return
    for token in command:
        base = token.split("=", 1)[0]
        if token in DIAGNOSTIC_REDIRECT_FLAGS or base in DIAGNOSTIC_REDIRECT_FLAGS:
            raise ValueError(
                f"Flag {token!r} is not permitted for diagnostic pods: following a "
                f"redirect lets the responding server choose the real target (e.g. a "
                f"302 to the metadata service). Re-run without it, or use "
                f"run_kubectl_command (requires human approval)."
            )
        # Bundled short flags, e.g. `curl -fsSL <url>`.
        if re.fullmatch(r"-[A-Za-z]{2,}", token) and "L" in token[1:]:
            raise ValueError(
                f"Flag {token!r} bundles '-L' (follow redirects), which is not "
                f"permitted for diagnostic pods: it lets the responding server choose "
                f"the real target. Re-run without '-L', or use run_kubectl_command "
                f"(requires human approval)."
            )


def validate_diagnostic_command(command: List[str]) -> None:
    """
    Validate the in-pod argv of a diagnostic image against the target policy.

    Raises ValueError on the first refused token. Called by
    run_preapproved_diagnostic_image before anything is executed.

    Hard denials are checked across every token before the configurable
    target-scope check, so a command that names both an external host and a
    metadata address is refused with the metadata reason rather than whichever
    token happened to come first.

    No-op when the operator has disabled the policy via
    KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED=false.
    """
    if not DIAGNOSTIC_TARGET_POLICY_ENABLED:
        logger.warning(
            "Diagnostic target policy is DISABLED "
            "(KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED=false); running %r without "
            "target validation. Cloud-metadata and external targets are reachable "
            "from this auto-approved tool.",
            command,
        )
        return
    _assert_no_redirect_following(command)
    candidates = [
        (token, host)
        for token in command
        for host in _candidate_target_hosts(token)
    ]
    resolved = [
        (token, host, _assert_target_not_hard_denied(host, token))
        for token, host in candidates
    ]
    for token, host, ip in resolved:
        _assert_target_in_scope(host, token, ip)


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


def is_preapproved_exec_command(command: List[str]) -> bool:
    """True if `command` (the in-container argv) is an allowlisted read-only diagnostic.

    `command` is exactly the argv that runs *inside* the container; the pod,
    namespace, and container are separate, server-controlled parameters of
    run_preapproved_kubectl_exec_command, and the server is the only thing that
    writes the `kubectl exec ... --` boundary. There is therefore no `--` to
    parse and nowhere to hide a second command before a trailing allowlisted
    token — the bypass class the earlier joined-glob matcher allowed. Only the
    binary (argv[0]) is checked, and exactly (so `psql` ≠ `ps`); its trailing
    args cannot start a new process (shell=False).
    """
    return bool(command) and command[0] in PREAPPROVED_EXEC_BINARIES


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
    name="run_preapproved_kubectl_exec_command",
    description=(
        "AUTO-APPROVED (runs immediately, no human needed). Run one of the "
        "operator's pre-approved read-only diagnostic binaries INSIDE a container "
        "via `kubectl exec` (e.g. ps/top/df/ls/netstat/ss). Pass the pod, "
        "namespace, optional container, and the in-container command as a list; "
        "the server builds the `kubectl exec ... -- <command>` invocation itself.\n\n"
        "Only the command's binary (command[0]) needs to be on the allowlist; if "
        "it isn't you get a structured refusal telling you to use "
        "run_kubectl_command (which requires human approval). To read a file use "
        "read_file_from_container instead of `cat`.\n\n"
        "Example: run_preapproved_kubectl_exec_command(pod=\"api-xxx\", namespace=\"prod\", command=[\"ps\",\"aux\"])"
    ),
)
def run_preapproved_kubectl_exec_command(
    pod: str,
    command: List[str],
    namespace: str = "default",
    container: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run a pre-approved read-only diagnostic command inside a container.

    The server owns the `kubectl exec ... --` boundary: pod/namespace/container
    are separate, validated parameters, so a caller cannot smuggle a second
    command or a fake separator into the invocation.

    Args:
        pod: Pod name (required)
        command: In-container argv, e.g. ["ps", "aux"]; command[0] must be allowlisted
        namespace: Namespace of the pod (default: "default")
        container: Optional container name within the pod

    Returns:
        Dictionary with success status, stdout, stderr
    """
    try:
        _validate_identifier(pod, "pod")
        _validate_identifier(namespace, "namespace")
        if container:
            _validate_identifier(container, "container")
        if not command:
            raise ValueError("No command provided")
        # Defense in depth: reject shell metacharacters in the in-container argv.
        # (command args run after `--`, so kubectl never interprets them as flags.)
        for part in command:
            _reject_shell_chars(part, "command")
        if not is_preapproved_exec_command(command):
            raise ValueError(
                f"Command binary {command[0]!r} is not pre-approved. "
                f"Allowed binaries: {', '.join(sorted(PREAPPROVED_EXEC_BINARIES))}. "
                f"Use run_kubectl_command (requires human approval) for anything else."
            )
    except ValueError as e:
        logger.warning(f"run_preapproved_kubectl_exec_command refused: {e}")
        return {"success": False, "error": str(e)}

    exec_args = ["exec", pod, "-n", namespace]
    if container:
        exec_args.extend(["-c", container])
    exec_args.extend(["--", *command])
    return _run_kubectl(exec_args)


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
        "TARGETS ARE RESTRICTED. Probes must point at in-cluster targets: a bare "
        "service name, a name under .svc/.cluster.local, or a private IP. Cloud "
        "metadata and link-local/loopback addresses (169.254.0.0/16, "
        "metadata.google.internal, 127.0.0.0/8, ...) are refused in every spelling "
        "and cannot be re-enabled by an operator. Redirect-following (curl -L) is "
        "refused because it hands target selection to the responding server. "
        "External targets are refused unless the operator enabled them. Use the "
        "FQDN if a short name is refused; use run_kubectl_command (human approval) "
        "when a probe genuinely needs a restricted target.\n\n"
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
            # Shell-char rejection does not constrain *where* the probe points:
            # ':' '/' '.' '?' '=' are all legal, so a URL passes it untouched.
            # Enforce the target policy before anything runs (ROB-910).
            validate_diagnostic_command(command)
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
    #   - hostNetwork/hostPID/hostIPC: false   a hostNetwork pod is exempt from
    #     NetworkPolicy and shares the node's stack, which would defeat the
    #     egress policy below. These are already the defaults; pinned so the
    #     containment does not rest on a default.
    #   - label robusta.dev/diagnostic-pod     selected by the egress
    #     NetworkPolicy in diagnostic-pod-networkpolicy.yaml, which denies
    #     link-local/metadata and (by default) all non-cluster egress. That
    #     policy is the CNI-enforced backstop for targets the argument checks
    #     cannot see: a DNS name that only resolves to link-local inside the
    #     pod, or wget following a redirect it was never told to follow.
    overrides = {
        "metadata": {"labels": {"robusta.dev/diagnostic-pod": "true"}},
        "spec": {
            "automountServiceAccountToken": False,
            "hostNetwork": False,
            "hostPID": False,
            "hostIPC": False,
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


def _build_node_diagnostic_overrides(pod_name: str, node: str) -> Dict[str, Any]:
    """
    Build the server-controlled pod override for a node-pinned diagnostic pod.

    The shape is what `kubectl debug node/` would build: privileged + hostPID +
    the host root mounted READ-ONLY at /host, so every diagnostic runs the
    NODE'S OWN binaries — the pod image contributes nothing but a shell. The
    exposure stays bounded because the tool only runs the fixed check catalog.

    Shared hardening with run_preapproved_diagnostic_image: no ServiceAccount
    token, memory-capped, and the robusta.dev/diagnostic-pod label so the
    operator's egress NetworkPolicy applies. nodeName pins the pod to the node
    under investigation (bypassing the scheduler, so a cordoned node can still
    be diagnosed); the blanket toleration keeps NoExecute taints (common on GPU
    nodes) from evicting the pod mid-check.
    """
    return {
        "metadata": {"labels": {"robusta.dev/diagnostic-pod": "true"}},
        "spec": {
            "nodeName": node,
            "automountServiceAccountToken": False,
            "hostNetwork": False,
            "hostIPC": False,
            "hostPID": True,
            "tolerations": [{"operator": "Exists"}],
            "volumes": [{"name": "host-root", "hostPath": {"path": "/"}}],
            "containers": [
                {
                    "name": pod_name,
                    "securityContext": {"privileged": True},
                    "volumeMounts": [
                        {"name": "host-root", "mountPath": "/host", "readOnly": True}
                    ],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"memory": "512Mi"},
                    },
                }
            ],
        },
    }


def _run_node_diagnostic_pod(
    node: str,
    checks: List[str],
    argv: List[str],
) -> Dict[str, Any]:
    """Launch a node-pinned diagnostic pod, capture its output, auto-delete it."""
    pod_name = f"k8s-remediation-gpu-{uuid.uuid4().hex[:8]}"
    overrides = _build_node_diagnostic_overrides(pod_name, node)
    run_args = [
        "run",
        pod_name,
        f"--image={GPU_DIAG_IMAGE}",
        "--restart=Never",
        "--rm",
        "-i",
        "-n",
        GPU_DIAG_NAMESPACE,
        f"--pod-running-timeout={GPU_DIAG_TIMEOUT}s",
        "--override-type=strategic",
        "--overrides",
        json.dumps(overrides),
        "--command",
        "--",
        *argv,
    ]
    try:
        result = _run_kubectl(run_args, timeout=GPU_DIAG_TIMEOUT)
    finally:
        # Ensure the pod is gone even if --rm didn't fire (e.g. on timeout).
        subprocess.run(
            ["kubectl", "delete", "pod", pod_name, "-n", GPU_DIAG_NAMESPACE,
             "--ignore-not-found", "--wait=false"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    # Context so the model (and logs) can tell which checks on which node this
    # output belongs to, and self-correct on failure.
    result["node"] = node
    result["checks"] = checks
    result["image"] = GPU_DIAG_IMAGE
    return result


def _gpu_check_names() -> List[str]:
    names = sorted(NODE_CHECKS)
    if DCGM_ENABLED:
        names += sorted(DCGM_CHECKS)
    return names


def _build_multi_check_script(names: List[str], commands: Dict[str, str]) -> str:
    """
    Concatenate the requested checks into one shell script so a single pod
    answers them all. Each check prints a `===== <name> =====` header before
    its output, a failing check does not stop the ones after it, and the
    script exits non-zero if any check failed. All inputs are server-owned
    constants (plus the already-validated pid/bus-id/diag-level
    substitutions), so this is not an injection surface.
    """
    parts = ["failed=0"]
    for name in names:
        parts.append(f"echo '===== {name} ====='")
        parts.append(f"{{ {commands[name]} ; }} || failed=1")
        parts.append("echo")
    parts.append("exit $failed")
    return "\n".join(parts)


@mcp.tool(
    name="run_gpu_node_diagnostics",
    description=(
        "AUTO-APPROVED (runs immediately, no human needed). Run one or more named "
        "GPU/driver diagnostic checks on a specific node. A short-lived pod is "
        "pinned to that node and runs the NODE'S OWN binaries through the host "
        "filesystem (mounted read-only) — nvidia-smi, dmesg, lspci, modinfo, "
        "journalctl all come from the node, and the pod is auto-deleted. All "
        "requested checks run in a single pod — prefer one call with several "
        "checks over several calls.\n\n"
        "Checks (pass a list of names as `checks`):\n"
        "- overview: nvidia-smi — is the driver alive; temperature, power, memory, processes\n"
        "- details: nvidia-smi -q — full per-GPU detail\n"
        "- throttling: temperature/power/clock sections and active throttle reasons\n"
        "- utilization_samples: ~30s of csv samples (temp, utilization, memory)\n"
        "- ecc: volatile + aggregate ECC error counts\n"
        "- page_retirement: retired pages (pending retirement means the node needs a reboot)\n"
        "- row_remapper: A100/H100-generation remapped-rows state\n"
        "- compute_processes: which processes hold GPU memory\n"
        "- kernel_gpu_errors: dmesg XID/NVRM errors ('GPU has fallen off the bus', "
        "driver load failures)\n"
        "- kernel_log_journal: last 2h of NVIDIA kernel-journal entries\n"
        "- driver_info: loaded module vs on-disk module vs running driver version "
        "(detects driver/library version mismatch)\n"
        "- pci: does the PCIe bus see the GPU at all\n"
        "- pci_link: link width/speed for one device — requires pci_bus_id "
        "(e.g. \"01:00.0\", from the `pci` check)\n"
        "- fabric_manager: nvidia-fabricmanager status (NVSwitch/HGX systems)\n"
        "- gpu_device_holders: host processes holding /dev/nvidia* open "
        "(zombie processes keeping GPU memory allocated)\n"
        "- process_info: inspect one host process — requires pid (from "
        "gpu_device_holders or compute_processes)\n"
        "- dcgm_discovery / dcgm_health / dcgm_diag: DCGM checks via the host's "
        "dcgmi (only when the operator enabled DCGM; dcgm_diag takes "
        "dcgm_diag_level — 1=quick, higher levels run longer and may be capped "
        "by the operator)\n\n"
        "Start with [\"overview\"] when debugging a GPU node, then batch the "
        "follow-ups (e.g. [\"throttling\",\"ecc\",\"kernel_gpu_errors\"]).\n\n"
        "Example: run_gpu_node_diagnostics(node=\"gpu-node-1\", checks=[\"overview\",\"kernel_gpu_errors\"])"
    ),
)
def run_gpu_node_diagnostics(
    node: str,
    checks: List[str],
    dcgm_diag_level: int = 1,
    pid: Optional[int] = None,
    pci_bus_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run one or more pre-defined GPU diagnostic checks on a node, using the
    node's own binaries via a read-only host-filesystem mount.

    Read-only and auto-approved: the caller selects checks by name and every
    command is server-owned, so the exposure is bounded to the fixed catalog.

    Args:
        node: Name of the node to diagnose (required)
        checks: Names of the checks to run (see NODE_CHECKS/DCGM_CHECKS)
        dcgm_diag_level: dcgmi diag -r level, only used by the dcgm_diag check
        pid: Host process id, required by the process_info check
        pci_bus_id: PCI bus id (e.g. "01:00.0"), required by the pci_link check

    Returns:
        Dictionary with success status, stdout (one `===== <check> =====` section
        per check), stderr, and the node/checks context
    """
    try:
        if not GPU_DIAG_ENABLED:
            raise ValueError(
                "GPU node diagnostics are disabled on this server "
                "(GPU_DIAG_ENABLED=false)."
            )
        _validate_identifier(node, "node")
        checks = list(dict.fromkeys(checks))  # dedupe, keep order
        if not checks:
            raise ValueError(
                f"No checks provided. Available checks: {', '.join(_gpu_check_names())}."
            )
        unknown = [c for c in checks if c not in NODE_CHECKS and c not in DCGM_CHECKS]
        if unknown:
            raise ValueError(
                f"Unknown checks {unknown!r}. Available checks: "
                f"{', '.join(_gpu_check_names())}."
            )
        dcgm_names = [c for c in checks if c in DCGM_CHECKS]
        if dcgm_names and not DCGM_ENABLED:
            raise ValueError(
                f"Checks {dcgm_names!r} are disabled: the operator has turned DCGM "
                f"checks off (DCGM_ENABLED=false). Available checks: "
                f"{', '.join(_gpu_check_names())}."
            )

        commands: Dict[str, str] = {}
        for name in checks:
            command = NODE_CHECKS.get(name) or DCGM_CHECKS[name]
            required = HOST_CHECK_REQUIRED_PARAM.get(name)
            if required == "pid":
                # Validated strictly because it is substituted into the (server-
                # owned) shell command below.
                if pid is None or not 0 < int(pid) < 2**22:
                    raise ValueError(
                        "The process_info check requires `pid`: a positive host "
                        "process id (see gpu_device_holders or compute_processes)."
                    )
                command = command.replace("{pid}", str(int(pid)))
            elif required == "pci_bus_id":
                # Same: only hex digits, ':' and '.' may reach the shell string.
                if not pci_bus_id or not _PCI_BUS_ID_RE.match(pci_bus_id):
                    raise ValueError(
                        "The pci_link check requires `pci_bus_id` in lspci form "
                        "(e.g. \"01:00.0\" or \"0000:01:00.0\"); run the `pci` check "
                        "first to find it."
                    )
                command = command.replace("{bus_id}", pci_bus_id)
            elif name == "dcgm_diag":
                if not 1 <= dcgm_diag_level <= GPU_DIAG_DCGM_MAX_DIAG_LEVEL:
                    raise ValueError(
                        f"dcgm_diag_level must be between 1 and "
                        f"{GPU_DIAG_DCGM_MAX_DIAG_LEVEL} (operator-configured cap "
                        f"GPU_DIAG_DCGM_MAX_DIAG_LEVEL; higher levels run longer and "
                        f"level 3 stress-tests the GPU). Got: {dcgm_diag_level}."
                    )
                command = f"{command} {dcgm_diag_level}"
            commands[name] = command
    except ValueError as e:
        logger.warning(f"run_gpu_node_diagnostics refused: {e}")
        return {"success": False, "error": str(e)}

    # ALL requested checks share ONE throwaway pod on the node; the prelude
    # resolves the node's own nvidia-smi (host PATH or /run/nvidia/driver).
    script = _NVSMI_PRELUDE + "\n" + _build_multi_check_script(checks, commands)
    return _run_node_diagnostic_pod(node=node, checks=checks, argv=["sh", "-c", script])


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
        "preapproved_exec_binaries": sorted(PREAPPROVED_EXEC_BINARIES),
        "diagnostic_images": list(DIAGNOSTIC_IMAGES),
        "diagnostic_target_policy_enabled": DIAGNOSTIC_TARGET_POLICY_ENABLED,
        "diagnostic_allow_external_targets": ALLOW_EXTERNAL_DIAGNOSTIC_TARGETS,
        "diagnostic_internal_dns_suffixes": list(DIAGNOSTIC_INTERNAL_DNS_SUFFIXES),
        "diagnostic_hard_denied_networks": list(DIAGNOSTIC_HARD_DENIED_NETWORKS),
        "diagnostic_hard_denied_hostnames": sorted(DIAGNOSTIC_HARD_DENIED_HOSTNAMES),
        "file_read_allowed_paths": list(FILE_READ_ALLOWED_PATHS),
        "file_read_denied_paths": list(FILE_READ_DENIED_PATHS),
        "allow_arbitrary_kubectl_commands": ALLOW_ARBITRARY_COMMANDS,
        "timeout_seconds": TIMEOUT,
        "gpu_node_diagnostics": {
            "enabled": GPU_DIAG_ENABLED,
            "image": GPU_DIAG_IMAGE,
            "checks": sorted(NODE_CHECKS),
            "dcgm_enabled": DCGM_ENABLED,
            "dcgm_checks": sorted(DCGM_CHECKS),
            "dcgm_max_diag_level": GPU_DIAG_DCGM_MAX_DIAG_LEVEL,
            "namespace": GPU_DIAG_NAMESPACE,
            "timeout_seconds": GPU_DIAG_TIMEOUT,
        },
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
    logger.info(f"Pre-approved exec binaries: {sorted(PREAPPROVED_EXEC_BINARIES)}")
    logger.info(f"Diagnostic images: {DIAGNOSTIC_IMAGES}")
    if DIAGNOSTIC_TARGET_POLICY_ENABLED:
        logger.info("Diagnostic target policy: ENABLED")
    else:
        logger.warning(
            "Diagnostic target policy: DISABLED via "
            "KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED=false. The auto-approved "
            "run_preapproved_diagnostic_image tool can be pointed at cloud-metadata "
            "endpoints and external hosts (ROB-910). Re-enable unless you have a "
            "specific reason not to."
        )
    logger.info(
        f"Diagnostic external targets allowed: {ALLOW_EXTERNAL_DIAGNOSTIC_TARGETS}"
    )
    logger.info(
        f"Diagnostic internal DNS suffixes: {DIAGNOSTIC_INTERNAL_DNS_SUFFIXES}"
    )
    logger.info(f"File-read allowed paths: {FILE_READ_ALLOWED_PATHS}")
    logger.info(f"File-read denied paths: {FILE_READ_DENIED_PATHS}")
    logger.info(f"Allow arbitrary kubectl commands: {ALLOW_ARBITRARY_COMMANDS}")
    logger.info(f"Timeout: {TIMEOUT}s")
    logger.info(f"GPU node diagnostics enabled: {GPU_DIAG_ENABLED}")
    if GPU_DIAG_ENABLED:
        logger.info(f"GPU diagnostics image: {GPU_DIAG_IMAGE}")
        logger.info(
            f"GPU DCGM checks enabled: {DCGM_ENABLED} "
            f"(max diag level: {GPU_DIAG_DCGM_MAX_DIAG_LEVEL})"
        )
        logger.info(f"GPU diagnostics namespace: {GPU_DIAG_NAMESPACE}")
        logger.info(f"GPU diagnostics timeout: {GPU_DIAG_TIMEOUT}s")

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

        uvicorn.run(build_http_app(), host=host, port=port, log_level="info")
    else:
        mcp.run()
