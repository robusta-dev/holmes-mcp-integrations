# Kubernetes Remediation MCP Server

An MCP server that lets HolmesGPT **diagnose and act** on a cluster beyond what
the agent pod's own limited RBAC allows — read files/processes it can't reach,
run diagnostic pods, and remediate (mutate) the cluster. It runs as a pod inside
the cluster and relies on its ServiceAccount's RBAC for resource-level
restrictions.

## Design principles

1. **Diagnose *and* act** beyond the agent pod's RBAC.
2. **Approval legibility through tool separation.** Each tool is *either* always
   auto-approved *or* always approval-gated. The model never guesses — the split
   is encoded in the tool set, not in hidden per-command logic.
3. **Plug-and-play.** Sensible default image/command/path allowlists ship in the
   box; enabling the addon works with zero further config.
4. **Safe by construction.** Nothing mutates without a human; reads can't touch
   secret mounts; RBAC is least-privilege (no `cluster-admin`); ingress is locked
   to HolmesGPT.

### Responsibility split

All **policy** lives here in the server: the command/image/path allowlists, the
arbitrary-command toggle, the hard verb allowlist, and the flag blocklist.
HolmesGPT only maps **tool name → approval** (`approval_required_tools`) and
carries the LLM instructions. The agent core stays free of command-parsing logic.

## Tool taxonomy

### Auto-approved tools (read-only / data-gathering — never prompt)

| Tool | What it does | Enforced by |
|------|--------------|-------------|
| `read_file_from_container` | Read a single file from inside a running container (`kubectl exec -- cat`). | Path allow/deny policy with in-container symlink resolution — secret/token mounts and the `/proc`, `/sys`, `/dev` pseudo-filesystems are always denied. |
| `run_preapproved_kubectl_exec_command` | Run a read-only diagnostic binary (`ps`/`top`/`df`/`ls`/`netstat`/`ss`) inside a container. The caller passes `pod`, `namespace`, optional `container`, and `command` as a list; the server builds `kubectl exec ... -- <command>` itself. | Binary allowlist — only `command[0]` is checked, exactly. |
| `run_preapproved_diagnostic_image` | Launch a short-lived, hardened pod (no SA token, no privilege escalation, memory-capped, not host-networked) from a pre-approved troubleshooting image, capture output, auto-delete. | Image allowlist (repo match → pinned tag) **and a target policy** — see [Diagnostic-pod target policy](#diagnostic-pod-target-policy). |
| `run_gpu_node_diagnostics` | Run one or more **named** GPU checks (single pod for all of them) (`overview`, `details`, `throttling`, `utilization_samples`, `ecc`, `page_retirement`, `row_remapper`, `compute_processes`, `dcgm_*`) on a specific node, via a short-lived pod pinned to that node. The NVIDIA container runtime injects the host driver's `nvidia-smi` (`NVIDIA_VISIBLE_DEVICES=all`) — no `nvidia.com/gpu` is allocated, so it works on fully-utilized nodes. No host mounts, no `hostPID`. | Check-name catalog — every command string is server-owned; the caller picks only the node, the check names, and a bounds-checked `dcgm_diag_level`. See [GPU node diagnostics](#gpu-node-diagnostics). |
| `run_gpu_node_host_diagnostics` | Run one or more **named** kernel/driver/PCIe GPU checks (single pod for all of them) (`kernel_gpu_errors`, `kernel_log_journal`, `driver_info`, `pci`, `pci_link`, `fabric_manager`, `gpu_device_holders`, `process_info`) via a privileged, `hostPID` pod with the host root mounted **read-only** at `/host`. Read-only host access — auto-approvable because the caller picks only a check name plus strictly validated `pid`/`pci_bus_id` scalars; every command is server-owned. | Check-name catalog + scalar validators; `GPU_DIAG_ALLOW_HOST_ACCESS=false` removes the tool entirely. See [GPU node diagnostics](#gpu-node-diagnostics). |
| `get_remediation_mcp_config` | Return the live effective policy for debugging. | — |

`run_preapproved_kubectl_exec_command` deliberately excludes `cat` (use
`read_file_from_container`) and `env` (leaks secrets). Because the pod/namespace
and the in-container command are separate parameters and the server owns the
`kubectl exec ... --` boundary, a caller cannot smuggle a second command or a
fake separator into the invocation.

### Approval-gated fallback (always prompts a human)

| Tool | What it does | Gated by |
|------|--------------|----------|
| `run_kubectl_command` | Catch-all for everything not pre-approved: all mutations, arbitrary exec, non-allowlisted images via `kubectl run`, etc. | HolmesGPT `approval_required_tools` **plus** the server guards below. |

Server guards on `run_kubectl_command` (defense in depth, independent of approval):

- **Hard verb allowlist** (`KUBECTL_ALLOWED_COMMANDS`).
- **Flag blocklist** (`KUBECTL_DANGEROUS_FLAGS`) + `--overrides`.
- **Shell-metacharacter rejection** (`; | & $ \` \ ' " ` and newlines); `shell=False`.
- **Timeout** (`KUBECTL_TIMEOUT`).
- **`KUBECTL_ALLOW_ARBITRARY_COMMANDS`**: when `false`, this tool is disabled —
  a fully locked-down mode where only the auto-approved tools function.

### Tool → approval summary

| Tool | Mutating | Approval | Enforced where |
|------|----------|----------|----------------|
| `read_file_from_container` | No | Auto | server path policy |
| `run_preapproved_kubectl_exec_command` | No | Auto | server binary allowlist |
| `run_preapproved_diagnostic_image` | No (data-gathering pod) | Auto | server image allowlist + target policy + egress NetworkPolicy |
| `run_gpu_node_diagnostics` | No (data-gathering pod, no host access) | Auto | server check catalog (server-owned commands) |
| `run_gpu_node_host_diagnostics` | No (read-only host access: privileged + hostPID + host FS read-only) | Auto | server check catalog + scalar validators + `GPU_DIAG_ALLOW_HOST_ACCESS` |
| `get_remediation_mcp_config` | No | Auto | — |
| `run_kubectl_command` | Yes | **Human approval** | HolmesGPT `approval_required_tools` + server guards |

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `KUBECTL_ALLOWED_COMMANDS` | `edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec` | Hard verb allowlist for `run_kubectl_command` |
| `KUBECTL_DANGEROUS_FLAGS` | `--kubeconfig,--context,--cluster,--user,--token,--as,--as-group,--as-uid` | Blocked flags |
| `KUBECTL_PREAPPROVED_EXEC_BINARIES` | `ps,top,df,ls,netstat,ss` | `run_preapproved_kubectl_exec_command` binary allowlist (bare names, no patterns) |
| `KUBECTL_DIAGNOSTIC_IMAGES` | `nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1` | `run_preapproved_diagnostic_image` allowlist |
| `KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED` | `true` | master switch for the diagnostic target policy; `false` disables **all** target checks (see warning below) |
| `KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS` | `false` | allow diagnostic probes to target hosts outside the cluster |
| `KUBECTL_DIAGNOSTIC_INTERNAL_DNS_SUFFIXES` | `.svc,.svc.cluster.local,.cluster.local` | DNS suffixes counted as cluster-internal |
| `KUBECTL_FILE_READ_ALLOWED_PATHS` | `/` | `read_file_from_container` allow roots |
| `KUBECTL_FILE_READ_DENIED_PATHS` | `/var/run/secrets/,/run/secrets/,/var/run/secrets/kubernetes.io/serviceaccount/` | secret-mount denylist |
| `KUBECTL_ALLOW_ARBITRARY_COMMANDS` | `true` | enable the approval-gated fallback |
| `KUBECTL_TIMEOUT` | `60` | per-command timeout (s) |
| `LOG_LEVEL` | `INFO` | logging |
| `MCP_AUTH_TOKEN` | *(unset)* | bearer token required on every HTTP-transport request; unset = unauthenticated (a startup warning is logged) |
| `GPU_DIAG_ENABLED` | `true` | master switch for both GPU node diagnostics tools |
| `GPU_DIAG_IMAGE` | `nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04` | image for the non-host GPU checks (needs glibc + sh; `nvidia-smi` is injected by the NVIDIA container runtime) |
| `DCGM_ENABLED` | `false` | **opt-in**: enable the `dcgm_*` checks — `dcgmi` runs in the node's EXISTING DCGM DaemonSet pod (e.g. the GPU Operator's `nvidia-dcgm`); no image is pulled, and there is no fallback when the node has no DCGM pod |
| `GPU_DIAG_DCGM_POD_SELECTOR` | `app=nvidia-dcgm` | label selector locating the node's DCGM pod |
| `GPU_DIAG_DCGM_MAX_DIAG_LEVEL` | `1` | highest `dcgmi diag -r <level>` allowed (levels 2–3 run long; 3 stress-tests the GPU) |
| `GPU_DIAG_RUNTIME_CLASS` | `nvidia` | RuntimeClass for the non-host GPU pod; empty = omit (for clusters whose default runtime already injects the driver) |
| `GPU_DIAG_ALLOW_HOST_ACCESS` | `true` | enable `run_gpu_node_host_diagnostics`; `false` removes it for operators who don't want auto-approved host-level pods |
| `GPU_DIAG_HOST_IMAGE` | `busybox:1.37.0` | image for the host-level checks (host binaries run via `chroot /host`) |
| `GPU_DIAG_NAMESPACE` | `default` | namespace the GPU diagnostic pods run in (the Helm chart sets the release namespace) |
| `GPU_DIAG_TIMEOUT` | `300` | per-check timeout (s) — covers image pull and `dcgmi diag` runtime |

The diagnostic image allowlist matches on the **repository**; the server runs the
pinned tag from the allowlist, so callers can just name the repo
(`run_preapproved_diagnostic_image(image="nicolaka/netshoot", ...)`).

## GPU node diagnostics

Both GPU tools launch a short-lived pod **pinned to the target node** (via
`spec.nodeName`, so a cordoned node can still be diagnosed; a blanket
toleration keeps GPU-node taints from evicting it), labeled
`robusta.dev/diagnostic-pod: "true"` like the other diagnostic pods, with no
ServiceAccount token, and auto-deleted afterwards. The caller selects checks
**by name** (several per call — they all run in the same pod, one
`===== <check> =====` section per check in the output) — every command string
is a server-owned constant, and the only caller-supplied values that reach a
command are a numeric `pid`, a `[0-9a-fA-F:.]`-validated PCI bus id, and a
bounds-checked `dcgmi diag` level.

- **`run_gpu_node_diagnostics`** (auto-approved) covers the nvidia-smi surface:
  driver/temperature/power overview, throttle reasons, ECC counts, retired
  pages / row remapping, GPU-memory-holding processes, ~30s utilization
  sampling. The pod gets the node driver's tooling through the NVIDIA container
  runtime (`NVIDIA_VISIBLE_DEVICES=all`, `NVIDIA_DRIVER_CAPABILITIES=utility`)
  without requesting `nvidia.com/gpu`, so it schedules even when every GPU is
  allocated — exactly the situation on the node you are debugging.
  The optional `dcgm_*` checks (`DCGM_ENABLED=true`, off by default) are the
  exception to the debug-pod pattern: they `kubectl exec` `dcgmi` in the
  node's **existing** DCGM DaemonSet pod (located via
  `GPU_DIAG_DCGM_POD_SELECTOR`, GPU Operator's `app=nvidia-dcgm` by default),
  whose host engine is already live — no image pull, no new pod, and a clear
  error when the node has no DCGM pod (there is deliberately no fallback
  image).
- **`run_gpu_node_host_diagnostics`** (auto-approved) covers the kernel side:
  dmesg XID/NVRM errors, kernel journal, loaded-vs-on-disk-vs-running driver
  version (the "Driver/library version mismatch" triple check), PCIe presence
  and link state, fabric-manager status, and which host processes hold
  `/dev/nvidia*` open. Its pod is privileged with `hostPID` and the host root
  mounted **read-only** at `/host` — the same shape as `kubectl debug node/`.
  It stays auto-approvable because the check catalog bounds what a caller (or
  prompt-injected content steering the caller) can do: trigger one of the
  fixed read-only checks, never compose a command. The accepted residual
  exposure is what those checks print — notably `process_info`/host process
  state, where secrets passed as process CLI args can appear.
  `GPU_DIAG_ALLOW_HOST_ACCESS=false` removes the capability entirely for
  operators who don't accept that trade-off.

## Diagnostic-pod target policy

`run_preapproved_diagnostic_image` is auto-approved and the allowlisted images
are network-probing tools (`curl`, `dig`, `wget`, `tcpdump`). The image allowlist
constrains *what runs*; it says nothing about *where the probe points*. Shell-char
rejection does not help either — `:` `/` `.` `?` `=` are all legal characters, so
a URL passes it untouched. Without a target policy, prompt-injected agent output
could aim an auto-approved probe at the cloud metadata service and read instance
credentials back, or POST cluster data to an external collector (ROB-910).

Two independent layers now constrain the target.

**1. Server-side argument checks** (`validate_diagnostic_command`), before
anything executes:

- **Always refused, not operator-configurable:** cloud metadata and
  link-local/loopback destinations — `169.254.0.0/16` (AWS/Azure/OpenStack IMDS,
  ECS task metadata), `127.0.0.0/8`, `0.0.0.0/8`, `100.100.100.200` (Alibaba),
  `192.0.0.192` (Oracle), `::1`, `fe80::/10`, `fd00:ec2::254`, and the metadata
  hostnames (`metadata.google.internal`, `metadata.goog`, `metadata`,
  `instance-data`). Recognised in **every IPv4 spelling** `inet_aton` accepts, so
  `http://2852039166/`, `http://0xA9FEA9FE/`, `http://0251.0376.0251.0376/` and
  `http://[::ffff:169.254.169.254]/` are all caught. URL userinfo is stripped
  first, so `http://harmless.example.com@169.254.169.254/` is judged on the real
  host.
- **Refused unless the operator opts in:** any target outside the cluster
  (`KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS=false` by default). This is what
  stops outbound exfiltration. Every token is inspected, not just the URL, so a
  proxy flag (`-x http://collector`, `--proxy=socks5://collector`) and `dig`'s
  `@server` syntax are covered — those change where the connection really goes.
- **Refused always:** explicit redirect-following (`curl -L`, `--location`,
  `--location-trusted`, and bundled forms like `-fsSL`), because a 302 lets the
  responding server pick the final target.

Cluster-internal means a bare service name, a name under a configured suffix
(`.svc`, `.cluster.local`), or a private (RFC1918) address. Namespace-qualified
shortcuts like `kubernetes.default` are **not** recognised — by shape they are
indistinguishable from `evil.com` — so use the FQDN
(`kubernetes.default.svc.cluster.local`). Refusals name the rule and the fix.

**2. An egress NetworkPolicy** — `diagnostic-pod-networkpolicy.yaml`. The server
labels every diagnostic pod `robusta.dev/diagnostic-pod: "true"` and pins
`hostNetwork: false` (a host-networked pod is exempt from NetworkPolicy). The
policy allows cluster DNS and RFC1918 egress only, so metadata/link-local is
denied by the CNI regardless of what was requested.

**Both layers are needed.** Argument checks cannot see a DNS name that only
resolves to a metadata address *inside* the pod (e.g. wildcard DNS like
`169.254.169.254.nip.io`), nor `wget`'s follow-redirects-by-default behaviour,
which has no flag to key on. Those cases run, and the NetworkPolicy is what
contains them. Conversely the NetworkPolicy is namespaced and inert on CNIs that
do not enforce it, and it cannot give the agent a legible reason to correct
itself. Layer 1 produces an actionable refusal; layer 2 produces containment that
does not depend on parsing.

When a probe genuinely needs a restricted target, route it through
`run_kubectl_command`, which requires human approval.

### Escape hatch: disabling the target policy

`KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED=false` turns the whole layer-1 policy
off, for environments it misjudges — a cluster domain the suffix list can't
express, or an appliance that genuinely lives on a public address.

> **This disables every target check, including the metadata ranges that are
> otherwise not operator-configurable, and restores the pre-fix behaviour: an
> auto-approved probe can then be aimed at the cloud metadata service and its
> response returned to the agent.** The server logs a warning at startup and on
> every call while it is off.

Try these first, in order:

1. `KUBECTL_DIAGNOSTIC_INTERNAL_DNS_SUFFIXES` — if the problem is a custom cluster domain.
2. `KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS=true` — if the problem is legitimate external probing. Metadata/link-local stays denied.
3. `run_kubectl_command` — for a one-off that a human approves.

Only reach for the master switch if none of those fit. Disabling layer 1 makes
the egress NetworkPolicy your only remaining control, so apply it first if you
go this route.

The other guards are unaffected by this switch: the image allowlist,
shell-metacharacter rejection, and the flag-injection check still apply.

## Quick Start

```bash
# 1. Build the Docker image
docker build -t kubernetes-remediation-mcp:1.2.0 .

# 2. Deploy the scoped RBAC (ServiceAccount + ClusterRole + binding, no cluster-admin)
kubectl apply -f rbac.yaml

# 3. Deploy the MCP server (and optionally lock ingress to HolmesGPT)
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f networkpolicy.yaml

# 4. Contain diagnostic pods' egress (metadata/link-local, external hosts).
#    NetworkPolicy is namespaced: apply this to EVERY namespace the agent may
#    run diagnostics in, not just the server's own namespace.
kubectl apply -f diagnostic-pod-networkpolicy.yaml -n <namespace>

# 5. Verify it's running
kubectl get pods -l app=kubernetes-remediation-mcp
```

## RBAC

`rbac.yaml` ships a **scoped, least-privilege `ClusterRole`** — not
`cluster-admin`. It is cluster-scoped because node operations require it, and
`secrets` is intentionally absent (defense in depth on top of the file-read
denylist). For stricter or namespaced setups, replace it with your own
`Role`/`ClusterRole`.

## HTTP transport authentication

With `--transport http` the server exposes its full tool surface — including
cluster mutations running under the pod's elevated ServiceAccount — to anyone
who can reach the port, and the human-approval gate for `run_kubectl_command`
lives in the HolmesGPT client, so it only binds callers that go through
HolmesGPT. Set `MCP_AUTH_TOKEN` to require `Authorization: Bearer <token>` on
every request (compared in constant time; anything else gets `401`).

The Holmes Helm chart (`mcpAddons.kubernetesRemediation`) generates a
per-release token Secret and wires both sides automatically. For manual
deployments, create a Secret and mount it on this pod as `MCP_AUTH_TOKEN`
(see the commented block in `deployment.yaml`), then send the same value from
Holmes via the toolset's `extra_headers`:

```yaml
mcp_servers:
  kubernetes_remediation:
    config:
      url: "http://kubernetes-remediation-mcp.default.svc.cluster.local:8000/mcp"
      mode: streamable-http
      extra_headers:
        Authorization: "Bearer {{ env.K8S_REMEDIATION_MCP_TOKEN }}"
```

When `MCP_AUTH_TOKEN` is unset the server behaves exactly as before (no
authentication) so existing deployments keep working — but then a
NetworkPolicy is the only thing between the port and the cluster.

## NetworkPolicy

`networkpolicy.yaml` is ingress-only and locks inbound traffic to HolmesGPT
(`app: holmes`). It restricts only ingress, so it can never break the MCP
server → apiserver path. It is inert where the CNI doesn't enforce
NetworkPolicy. Verify the HolmesGPT pod label matches your deployment before
relying on enforcement.

`diagnostic-pod-networkpolicy.yaml` is egress-only and applies to the short-lived
pods created by `run_preapproved_diagnostic_image` (selected by the
`robusta.dev/diagnostic-pod: "true"` label the server stamps on them). It allows
cluster DNS and RFC1918 egress, which denies cloud metadata, link-local, loopback
and the public internet. See [Diagnostic-pod target
policy](#diagnostic-pod-target-policy) for why this is required alongside the
server-side checks and not instead of them.

Two deployment notes: NetworkPolicy is **namespaced**, so apply this manifest to
every namespace diagnostics may run in (the tool takes the namespace from the
caller); and it is inert on a CNI that does not enforce NetworkPolicy. Confirm
enforcement before relying on it:

```bash
kubectl run np-test --rm -i --restart=Never -n <namespace> \
  --image=curlimages/curl:8.11.1 \
  --overrides='{"metadata":{"labels":{"robusta.dev/diagnostic-pod":"true"}}}' \
  -- curl -s -m 5 http://169.254.169.254/    # must time out, not answer
```

## Holmes integration

```yaml
mcp_servers:
  kubernetes_remediation:
    description: "Kubernetes remediation & deep diagnostics — execute kubectl and run diagnostic pods"
    config:
      url: "http://kubernetes-remediation-mcp.default.svc.cluster.local:8000/mcp"
      mode: streamable-http
    approval_required_tools:
      - "run_kubectl_command"
```

Only the mutating fallback (`run_kubectl_command`) is listed under
`approval_required_tools` — the read-only tools, the GPU diagnostics
included, run immediately.

## Testing

```bash
# Unit tests for the policy/validator logic (no cluster needed)
pip install -r requirements.txt pytest
pytest test_kubernetes_remediation.py

# Run the server locally (HTTP transport)
python kubernetes_remediation.py --transport http --host 0.0.0.0 --port 8000
```

## File Structure

```
kubernetes-remediation/
├── kubernetes_remediation.py        # MCP server
├── test_kubernetes_remediation.py   # Unit tests (policy/validators)
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Container image
├── deployment.yaml                  # Deployment + ConfigMap env
├── service.yaml                     # Service
├── networkpolicy.yaml               # Ingress-only NetworkPolicy (MCP server)
├── diagnostic-pod-networkpolicy.yaml # Egress NetworkPolicy for diagnostic pods
├── rbac.yaml                        # Scoped ServiceAccount/ClusterRole/binding
└── README.md                        # This file
```
