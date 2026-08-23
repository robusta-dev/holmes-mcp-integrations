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
    - Diagnostic-pod target policy: cloud-metadata/link-local/loopback refused in
      every IP spelling, external targets off by default, redirect-following
      refused — plus an egress NetworkPolicy on the pod as the CNI-enforced
      backstop (diagnostic-pod-networkpolicy.yaml)
    - Per-command timeout
"""

import os
import subprocess
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


# Create MCP server
mcp = FastMCP(name="kubernetes-remediation", version="1.2.0")


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
