# Prefect MCP Server

An MCP server that provides Prefect integration for workflow orchestration and monitoring. Uses the [prefect-mcp](https://pypi.org/project/prefect-mcp/) server wrapped with Supergateway to expose it as an HTTP/SSE endpoint.

## Overview

This MCP server enables Holmes to interact with Prefect for workflow troubleshooting and monitoring. It can be used to:

- List and inspect flow runs, deployments, and work pools
- Retrieve logs from flow and task runs
- Check worker health and queue backlogs
- Investigate failed or crashed runs
- Trigger deployments

## Architecture

```
Holmes -> Remote MCP (SSE) -> Supergateway -> prefect-mcp-server (STDIO) -> Prefect API
                                    |
                    Running in Kubernetes as Deployment
                    (Credentials via Kubernetes Secrets)
```

The prefect-mcp server is pre-installed at Docker build time (no runtime network required). Supergateway wraps the stdio transport as an SSE HTTP endpoint.

## Quick Start

### 1. Create the secret with your Prefect credentials

**For Prefect Cloud:**

1. Go to [Prefect Cloud](https://app.prefect.cloud)
2. Navigate to your workspace settings to get the API URL
3. Create an API key at Settings -> API Keys

**For self-hosted Prefect:**

1. Use your Prefect server URL (e.g., `http://prefect-server:4200/api`)
2. API key is optional unless you've configured authentication

```bash
# Edit secret.yaml with your actual credentials first
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

Add the MCP server to your Holmes helm values:

```yaml
mcp_servers:
  prefect:
    description: "Prefect workflow orchestration and monitoring"
    config:
      url: "http://prefect-mcp.default.svc.cluster.local:8000/sse"
      mode: sse
      headers:
        Content-Type: "application/json"
    llm_instructions: |
      Use Prefect tools to investigate workflow failures, check flow run status, and troubleshoot orchestration issues.
      When investigating a failed flow run:
        1. First get the flow run details to understand what failed
        2. Retrieve the logs for the failed flow/task run
        3. Check if the deployment is healthy and workers are running
        4. Look at recent runs of the same flow to identify patterns
```

**Note:** Update the namespace in the URL if deploying to a different namespace (e.g., `prefect-mcp.monitoring.svc.cluster.local`).

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `PREFECT_API_URL` | Prefect API endpoint URL | Yes |
| `PREFECT_API_KEY` | Prefect Cloud API key | Cloud: Yes, Self-hosted: Optional |
| `PREFECT_API_AUTH_STRING` | Basic auth string (username:password) | Optional (self-hosted only) |

### Prefect Cloud vs Self-Hosted

| Setup | `PREFECT_API_URL` | Authentication |
|-------|-------------------|----------------|
| Cloud | `https://api.prefect.cloud/api/accounts/<id>/workspaces/<id>` | `PREFECT_API_KEY` (required) |
| Self-hosted | `http://prefect-server:4200/api` | `PREFECT_API_KEY` or `PREFECT_API_AUTH_STRING` (optional) |

## Security Considerations

1. **Credentials** - Store API keys in Kubernetes Secrets, never in plain text
2. **API Key Scope** - Use minimal permissions required for your use case
3. **Network Policies** - Consider restricting access to the MCP server pod
4. **Key Rotation** - Rotate API keys periodically and update the Kubernetes Secret
5. **Air-gapped** - The Docker image has all dependencies pre-installed; no outbound network access is needed at runtime

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

### Authentication Errors

1. Verify the API key is valid in Prefect Cloud UI
2. Check that `PREFECT_API_URL` points to the correct workspace
3. For self-hosted, ensure the Prefect server is reachable from the cluster

### Connection Issues

```bash
# Test from within the cluster
kubectl run curl --image=curlimages/curl --rm -it --restart=Never -- \
  curl -v http://prefect-mcp:8000/health

# Check service endpoints
kubectl get endpoints prefect-mcp

# Check if pod is ready
kubectl get pods -l app=prefect-mcp -o wide
```

## File Structure

```
prefect/
├── Dockerfile                    # Container image with Supergateway + prefect-mcp
├── auto-build-config.yaml        # Build configuration
├── build-push.sh                 # Build and push script
├── deployment.yaml               # Kubernetes Deployment
├── service.yaml                  # Kubernetes Service
├── secret.yaml                   # Credentials template
├── holmes-config/
│   └── prefect-toolset.yaml      # Holmes MCP configuration
└── README.md                     # This file
```

## References

- [prefect-mcp on PyPI](https://pypi.org/project/prefect-mcp/)
- [Prefect Documentation](https://docs.prefect.io/)
- [Prefect Cloud](https://app.prefect.cloud)
- [Supergateway](https://github.com/supercorp-ai/supergateway)
