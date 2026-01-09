# Kubernetes Remediation MCP Server

An MCP server that allows running kubectl commands safely for Kubernetes remediation tasks. It runs as a pod inside a Kubernetes cluster, relying on RBAC for namespace and resource restrictions.

## Overview

This MCP server provides Holmes with the ability to execute kubectl commands for investigating and remediating Kubernetes issues. The server implements multiple layers of security:

1. **Subcommand allowlist** - Only explicitly allowed kubectl subcommands can be executed
2. **Dangerous flags blocklist** - Flags that could bypass security are blocked
3. **Shell metacharacter rejection** - Defense in depth against injection attacks
4. **Image allowlist** - For the `run_image` tool, only pre-approved images can be used
5. **RBAC** - Kubernetes native access control for namespace/resource restrictions
6. **Timeout** - Prevents hanging commands from consuming resources

## Architecture

```
Holmes -> Remote MCP (HTTP) -> Kubernetes Remediation MCP Server -> kubectl -> Kubernetes API
                                          |
                          Running in Kubernetes with ServiceAccount
                          (RBAC controls what kubectl can access)
```

## Quick Start

```bash
# 1. Build the Docker image
docker build -t kubernetes-remediation-mcp:latest .

# 2. Deploy RBAC resources
kubectl apply -f rbac.yaml

# 3. Deploy the MCP server
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# 4. Verify it's running
kubectl get pods -l app=kubernetes-remediation-mcp
```

## Tools

### 1. `kubectl`

Execute a kubectl command with validated arguments.

**Parameters:**
- `args: list[str]` - Command arguments, e.g. `["get", "pods", "-n", "default"]`

**Example calls:**
```json
{"args": ["get", "pods", "-n", "production"]}
{"args": ["describe", "pod", "my-pod", "-n", "default"]}
{"args": ["logs", "my-pod", "-c", "sidecar", "--tail", "100"]}
{"args": ["delete", "pod", "stuck-pod", "-n", "staging"]}
```

**Returns:**
```json
{"success": true, "stdout": "...", "stderr": "", "return_code": 0}
```

### 2. `run_image`

Run a pod with a pre-approved image. This tool is disabled by default and requires configuring `KUBECTL_ALLOWED_IMAGES`.

**Parameters:**
- `name: str` - Pod name (required)
- `image: str` - Image to run, must be in allowed list (required)
- `namespace: str` - Optional namespace
- `command: list[str]` - Optional command to run in container
- `rm: bool` - Delete pod after exit (default: true)

**Example calls:**
```json
{"name": "debug", "image": "alpine", "command": ["sh", "-c", "cat /etc/resolv.conf"]}
{"name": "curl-test", "image": "curlimages/curl", "command": ["curl", "-s", "http://my-service"]}
```

### 3. `get_config`

Get the current configuration of the MCP server for debugging purposes.

**Returns:**
```json
{
  "allowed_commands": ["get", "describe", "logs"],
  "dangerous_flags": ["--kubeconfig", "--context", "..."],
  "timeout_seconds": 60,
  "allowed_images": ["alpine", "busybox"],
  "run_image_enabled": true
}
```

## Configuration

Configure the server using environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `KUBECTL_ALLOWED_COMMANDS` | Comma-separated list of allowed subcommands | `get,describe,logs` |
| `KUBECTL_DANGEROUS_FLAGS` | Comma-separated list of blocked flags | `--kubeconfig,--context,--cluster,--user,--token,--as,--as-group,--as-uid` |
| `KUBECTL_TIMEOUT` | Command timeout in seconds | `60` |
| `KUBECTL_ALLOWED_IMAGES` | Comma-separated list of allowed images for `run_image` | (empty = tool disabled) |
| `LOG_LEVEL` | Logging level | `INFO` |

### Example Configurations

**Read-only (default):**
```yaml
- name: KUBECTL_ALLOWED_COMMANDS
  value: "get,describe,logs"
```

**Remediation enabled:**
```yaml
- name: KUBECTL_ALLOWED_COMMANDS
  value: "get,describe,logs,delete,patch,scale,rollout"
```

**With debug images:**
```yaml
- name: KUBECTL_ALLOWED_COMMANDS
  value: "get,describe,logs,delete,run"
- name: KUBECTL_ALLOWED_IMAGES
  value: "curlimages/curl,busybox,alpine,nicolaka/netshoot"
```

## Security

### Why These Security Measures

| Measure | Protects Against |
|---------|------------------|
| `shell=False` | Shell injection (`;`, `\|`, `$()`, etc.) |
| Subcommand allowlist | Unauthorized operations |
| Dangerous flags block | Credential/context hijacking |
| Shell char rejection | Defense in depth |
| Image allowlist | Running malicious containers |
| No `--overrides` flag | Privilege escalation via pod spec |
| Timeout | Hanging commands consuming resources |
| RBAC (cluster-side) | Namespace/resource access control |

### Blocked Flags

The following flags are always blocked:
- `--kubeconfig` - Could point to different cluster config
- `--context` - Could switch to different cluster/user
- `--cluster` - Could target different cluster
- `--user` - Could impersonate different user
- `--token` - Could use different credentials
- `--as` / `--as-group` / `--as-uid` - Impersonation
- `--overrides` - Could escalate privileges via pod spec

### Shell Metacharacters

Even though `shell=False` is used, these characters are rejected as defense in depth:
```
; | & $ ` \ ' " \n \r
```

## RBAC Configuration

The MCP server relies on Kubernetes RBAC for access control. The `rbac.yaml` file provides templates.

### Read-Only Access (Default)

The default configuration provides read-only access to common resources:

```yaml
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "services", "configmaps", "events"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "daemonsets", "replicasets", "statefulsets"]
    verbs: ["get", "list", "watch"]
```

### Enabling Remediation

To enable write operations, uncomment the remediation rules in `rbac.yaml`:

```yaml
# Pod remediation
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["delete", "patch"]

# Deployment remediation
- apiGroups: ["apps"]
  resources: ["deployments", "deployments/scale"]
  verbs: ["patch", "update"]
```

And update the allowed commands:
```yaml
- name: KUBECTL_ALLOWED_COMMANDS
  value: "get,describe,logs,delete,patch,scale"
```

### Namespace Scoping

For restricted access to specific namespaces, use `Role` and `RoleBinding` instead of `ClusterRole` and `ClusterRoleBinding`. See the commented examples in `rbac.yaml`.

## Deployment

### Building the Image

```bash
# Build locally
docker build -t kubernetes-remediation-mcp:latest .

# Build and push to registry
docker build -t your-registry/kubernetes-remediation-mcp:1.0.0 .
docker push your-registry/kubernetes-remediation-mcp:1.0.0
```

### Deploying to Kubernetes

1. Update the image in `deployment.yaml` to your registry

2. Apply the RBAC resources:
```bash
kubectl apply -f rbac.yaml
```

3. Deploy the server:
```bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

4. Verify:
```bash
kubectl get pods -l app=kubernetes-remediation-mcp
kubectl logs -l app=kubernetes-remediation-mcp
```

## Holmes Integration

Configure Holmes to use the MCP server:

```yaml
mcp_servers:
  kubernetes-remediation:
    description: "Kubernetes remediation tools for cluster operations"
    config:
      url: "http://kubernetes-remediation-mcp.default.svc.cluster.local:8000/mcp"
      mode: streamable-http
```

## Testing Locally

### Without Kubernetes (Limited)

```bash
# Install dependencies
pip install -r requirements.txt

# Configure for read-only
export KUBECTL_ALLOWED_COMMANDS="get,describe,logs"
export KUBECTL_TIMEOUT="30"

# Run the server
python kubernetes_remediation.py --transport http --host 0.0.0.0 --port 8000
```

### Test with curl

```bash
# List tools
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'

# Get config
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_config", "arguments": {}}, "id": 2}'
```

## Troubleshooting

### MCP Server Not Responding

```bash
# Check pod status
kubectl get pods -l app=kubernetes-remediation-mcp
kubectl describe pod -l app=kubernetes-remediation-mcp

# Check logs
kubectl logs -l app=kubernetes-remediation-mcp
```

### Permission Denied Errors

```bash
# Verify ServiceAccount is attached
kubectl get pod <pod-name> -o yaml | grep serviceAccount

# Test RBAC manually
kubectl auth can-i get pods --as=system:serviceaccount:default:kubernetes-remediation-mcp-sa

# Check ClusterRoleBinding
kubectl describe clusterrolebinding kubernetes-remediation-mcp-binding
```

### Command Not Allowed

Check the `KUBECTL_ALLOWED_COMMANDS` environment variable and add the required command.

## File Structure

```
kubernetes-remediation/
├── kubernetes_remediation.py   # Main MCP server
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Container image
├── deployment.yaml             # Kubernetes Deployment
├── service.yaml                # Kubernetes Service
├── rbac.yaml                   # RBAC configuration
└── README.md                   # This file
```

## Security Recommendations

1. **Start with read-only** - Begin with `get,describe,logs` and add write commands only as needed
2. **Use namespace-scoped RBAC** - Limit access to specific namespaces when possible
3. **Audit logging** - Enable Kubernetes audit logging to track all API calls
4. **Network policies** - Restrict network access to the MCP server
5. **Image scanning** - If using `run_image`, only allow scanned and approved images
6. **Regular review** - Periodically review RBAC permissions and allowed commands
