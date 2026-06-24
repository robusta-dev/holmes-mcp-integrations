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
| `run_preapproved_kubectl_command` | Run a kubectl command from the read-only diagnostics allowlist (`exec ... -- ps/top/df/ls/netstat/ss`). | Command allowlist (prefix/glob). |
| `run_preapproved_diagnostic_image` | Launch a short-lived, hardened pod (no SA token, no privilege escalation, memory-capped) from a pre-approved troubleshooting image, capture output, auto-delete. | Image allowlist (repo match → pinned tag). |
| `get_remediation_mcp_config` | Return the live effective policy for debugging. | — |

`run_preapproved_kubectl_command` deliberately excludes `cat` (use
`read_file_from_container`) and `env` (leaks secrets).

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
| `run_preapproved_kubectl_command` | No | Auto | server command allowlist |
| `run_preapproved_diagnostic_image` | No (data-gathering pod) | Auto | server image allowlist |
| `get_remediation_mcp_config` | No | Auto | — |
| `run_kubectl_command` | Yes | **Human approval** | HolmesGPT `approval_required_tools` + server guards |

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `KUBECTL_ALLOWED_COMMANDS` | `edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec` | Hard verb allowlist for `run_kubectl_command` |
| `KUBECTL_DANGEROUS_FLAGS` | `--kubeconfig,--context,--cluster,--user,--token,--as,--as-group,--as-uid` | Blocked flags |
| `KUBECTL_PREAPPROVED_COMMANDS` | `exec * -- ps*,exec * -- top*,exec * -- df*,exec * -- ls*,exec * -- netstat*,exec * -- ss*` | `run_preapproved_kubectl_command` allowlist |
| `KUBECTL_DIAGNOSTIC_IMAGES` | `nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1` | `run_preapproved_diagnostic_image` allowlist |
| `KUBECTL_FILE_READ_ALLOWED_PATHS` | `/` | `read_file_from_container` allow roots |
| `KUBECTL_FILE_READ_DENIED_PATHS` | `/var/run/secrets/,/run/secrets/,/var/run/secrets/kubernetes.io/serviceaccount/` | secret-mount denylist |
| `KUBECTL_ALLOW_ARBITRARY_COMMANDS` | `true` | enable the approval-gated fallback |
| `KUBECTL_TIMEOUT` | `60` | per-command timeout (s) |
| `LOG_LEVEL` | `INFO` | logging |

The diagnostic image allowlist matches on the **repository**; the server runs the
pinned tag from the allowlist, so callers can just name the repo
(`run_preapproved_diagnostic_image(image="nicolaka/netshoot", ...)`).

## Quick Start

```bash
# 1. Build the Docker image
docker build -t kubernetes-remediation-mcp:1.1.0 .

# 2. Deploy the scoped RBAC (ServiceAccount + ClusterRole + binding, no cluster-admin)
kubectl apply -f rbac.yaml

# 3. Deploy the MCP server (and optionally lock ingress to HolmesGPT)
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f networkpolicy.yaml

# 4. Verify it's running
kubectl get pods -l app=kubernetes-remediation-mcp
```

## RBAC

`rbac.yaml` ships a **scoped, least-privilege `ClusterRole`** — not
`cluster-admin`. It is cluster-scoped because node operations require it, and
`secrets` is intentionally absent (defense in depth on top of the file-read
denylist). For stricter or namespaced setups, replace it with your own
`Role`/`ClusterRole`.

## NetworkPolicy

`networkpolicy.yaml` is ingress-only and locks inbound traffic to HolmesGPT
(`app: holmes`). It restricts only ingress, so it can never break the MCP
server → apiserver path. It is inert where the CNI doesn't enforce
NetworkPolicy. Verify the HolmesGPT pod label matches your deployment before
relying on enforcement.

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
`approval_required_tools` — the four read-only tools run immediately.

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
├── networkpolicy.yaml               # Ingress-only NetworkPolicy
├── rbac.yaml                        # Scoped ServiceAccount/ClusterRole/binding
└── README.md                        # This file
```
