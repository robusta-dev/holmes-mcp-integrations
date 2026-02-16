# Prefect MCP Server

An MCP server that provides Prefect workflow orchestration integration for Holmes. Uses the official [prefect-mcp](https://github.com/PrefectHQ/prefect-mcp-server) server wrapped with Supergateway to expose it as an HTTP/SSE endpoint.

## Overview

This MCP server enables Holmes to interact with Prefect for workflow observability. It can be used to:

- List and inspect flow runs, task runs, and their logs
- Check deployment configurations and status
- Monitor work pool health and worker status
- Query the Prefect event stream
- List configured automations
- Look up Prefect documentation

## Architecture

```
Holmes -> Remote MCP (SSE) -> Supergateway -> Prefect MCP (STDIO) -> Prefect API
                                    |
                    Running in Kubernetes as Deployment
                    (Credentials via Kubernetes Secrets)
```

The official Prefect MCP uses STDIO transport, so we wrap it with [Supergateway](https://github.com/supercorp-ai/supergateway) to expose it as an SSE HTTP endpoint that Holmes can connect to.

## Quick Start

### 1. Create the secret with your Prefect credentials

For **Prefect Cloud**:
```bash
kubectl create secret generic prefect-mcp-credentials \
  --from-literal=prefect-api-url="https://api.prefect.cloud/api/accounts/<ACCOUNT_ID>/workspaces/<WORKSPACE_ID>" \
  --from-literal=prefect-api-key="<YOUR_API_KEY>"
```

For **self-hosted Prefect** (no auth):
```bash
kubectl create secret generic prefect-mcp-credentials \
  --from-literal=prefect-api-url="http://prefect-server:4200/api" \
  --from-literal=prefect-api-key=""
```

Or edit `secret.yaml` and apply it:
```bash
kubectl apply -f secret.yaml
```

### 2. Deploy the MCP server

```bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# Verify it's running
kubectl get pods -l app=prefect-mcp
kubectl logs -l app=prefect-mcp
```

### 3. Configure Holmes

Add the snippet from `holmes-config/prefect-toolset.yaml` to your Holmes helm values.

## Testing in Kubernetes

The `test/` directory contains everything needed to test this integration end-to-end in a K8s cluster.

### Step 1: Deploy a test Prefect server

```bash
# Create namespace and deploy Prefect server
kubectl create namespace prefect
kubectl apply -f test/prefect-server.yaml -n prefect

# Wait for it to be ready
kubectl wait --for=condition=ready pod -l app=prefect-server -n prefect --timeout=120s

# (Optional) Access the Prefect UI
kubectl port-forward -n prefect svc/prefect-server 4200:4200
# Open http://localhost:4200
```

### Step 2: Seed sample data

```bash
# Run the seed job (creates sample flows, including a failing one)
kubectl apply -f test/seed-job.yaml -n prefect

# Watch it run
kubectl logs -f job/prefect-seed-data -n prefect
```

This creates several flow runs:
- `etl-pipeline` — successful ETL with extract/transform/load tasks
- `health-check` — simple passing flow
- `data-sync` — multi-source sync flow
- `failing-pipeline` — a flow that fails with a database connection error

### Step 3: Deploy the MCP server pointing at the test Prefect

```bash
# Create the secret pointing at the test server (no auth needed for local Prefect)
kubectl create secret generic prefect-mcp-credentials \
  --from-literal=prefect-api-url="http://prefect-server.prefect.svc.cluster.local:4200/api" \
  -n prefect

# Deploy the MCP server
kubectl apply -f deployment.yaml -n prefect
kubectl apply -f service.yaml -n prefect

# Wait for it
kubectl wait --for=condition=ready pod -l app=prefect-mcp -n prefect --timeout=120s
```

### Step 4: Verify the MCP server works

```bash
# Port-forward to the MCP server
kubectl port-forward -n prefect svc/prefect-mcp 8000:8000 &

# Check the SSE endpoint responds
curl -s http://localhost:8000/sse

# Or test from inside the cluster
kubectl run curl --image=curlimages/curl --rm -it --restart=Never -n prefect -- \
  curl -s http://prefect-mcp:8000/sse
```

### Cleanup

```bash
kubectl delete namespace prefect
```

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `PREFECT_API_URL` | Prefect API endpoint URL | Yes |
| `PREFECT_API_KEY` | API key for Prefect Cloud | For Cloud |
| `PREFECT_API_AUTH_STRING` | Basic auth for self-hosted (`user:pass`) | For self-hosted with auth |

### Authentication Options

| Setup | Variables Needed |
|-------|-----------------|
| Prefect Cloud | `PREFECT_API_URL` + `PREFECT_API_KEY` |
| Self-hosted (no auth) | `PREFECT_API_URL` only |
| Self-hosted (basic auth) | `PREFECT_API_URL` + `PREFECT_API_AUTH_STRING` |

## Holmes Integration

Add the MCP server to your Holmes helm values:

```yaml
mcp_servers:
  prefect:
    description: "Prefect workflow orchestration and observability"
    config:
      url: "http://prefect-mcp.default.svc.cluster.local:8000/sse"
      mode: sse
      headers:
        Content-Type: "application/json"
    llm_instructions: "Use the Prefect tools to investigate workflow failures, check flow run status, and analyze orchestration issues. When a flow run fails, retrieve its logs and task run details to find the root cause. Check work pool status to identify infrastructure problems."
```

**Note:** Update the namespace in the URL if deploying to a different namespace (e.g., `prefect-mcp.prefect.svc.cluster.local`).

## Troubleshooting

### MCP Server Not Starting

```bash
# Check pod status
kubectl describe pod -l app=prefect-mcp

# Check logs for startup errors
kubectl logs -l app=prefect-mcp

# Verify secret exists
kubectl get secret prefect-mcp-credentials
```

### Connection to Prefect API Fails

```bash
# Test connectivity from the MCP pod to Prefect
kubectl exec -it deploy/prefect-mcp -- wget -qO- http://prefect-server:4200/api/health

# Check the API URL in the secret
kubectl get secret prefect-mcp-credentials -o jsonpath='{.data.prefect-api-url}' | base64 -d
```

### Authentication Errors

1. For Prefect Cloud: verify API key at https://app.prefect.cloud/my/api-keys
2. For self-hosted with auth: check `PREFECT_API_AUTH_STRING` format is `username:password`
3. For self-hosted without auth: ensure `PREFECT_API_KEY` is empty or not set

## File Structure

```
prefect/
├── Dockerfile                    # Container image with Supergateway + Prefect MCP
├── deployment.yaml               # Kubernetes Deployment
├── service.yaml                  # Kubernetes Service
├── secret.yaml                   # Credentials template
├── auto-build-config.yaml        # Build configuration
├── build-push.sh                 # Build and push script
├── holmes-config/
│   └── prefect-toolset.yaml      # Holmes MCP configuration
├── test/
│   ├── prefect-server.yaml       # Test Prefect server (Deployment + Service)
│   ├── seed-job.yaml             # K8s Job to populate sample data
│   └── seed-data.py              # Seed script (also usable locally)
└── README.md                     # This file
```

## References

- [Prefect MCP Server GitHub](https://github.com/PrefectHQ/prefect-mcp-server)
- [prefect-mcp on PyPI](https://pypi.org/project/prefect-mcp/)
- [Prefect MCP How-to Guide](https://docs.prefect.io/v3/how-to-guides/ai/use-prefect-mcp-server)
- [Prefect Documentation](https://docs.prefect.io/)
- [Supergateway](https://github.com/supercorp-ai/supergateway)
