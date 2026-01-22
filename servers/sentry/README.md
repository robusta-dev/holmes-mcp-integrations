# Sentry MCP Server

An MCP server that provides Sentry integration for error tracking and monitoring. Uses the official [Sentry MCP](https://github.com/getsentry/sentry-mcp) server wrapped with Supergateway to expose it as an HTTP/SSE endpoint.

## Overview

This MCP server enables Holmes to interact with Sentry for error tracking and issue management. It can be used to:

- List projects and organizations
- Search and retrieve issues/errors
- Get detailed event information with stack traces
- Resolve and assign issues
- Analyze error patterns and trends

## Architecture

```
Holmes -> Remote MCP (SSE) -> Supergateway -> Sentry MCP (STDIO) -> Sentry API
                                    |
                    Running in Kubernetes as Deployment
                    (Auth Token via Kubernetes Secrets)
```

The official Sentry MCP uses STDIO transport, so we wrap it with [Supergateway](https://github.com/supercorp-ai/supergateway) to expose it as an SSE HTTP endpoint that Holmes can connect to.

## Quick Start

```bash
# 1. Build the Docker image
docker build -t your-registry/sentry-mcp:latest .
docker push your-registry/sentry-mcp:latest

# 2. Update deployment.yaml with your image
# Edit deployment.yaml and set the image field

# 3. Create the secret with your Sentry credentials
# Edit secret.yaml with your actual token first
kubectl apply -f secret.yaml

# 4. Deploy the MCP server
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# 5. Verify it's running
kubectl get pods -l app=sentry-mcp
kubectl logs -l app=sentry-mcp
```

## Prerequisites

1. **Sentry Account** (SaaS or self-hosted)
2. **Auth Token** - See [Creating an Auth Token](#creating-an-auth-token)
3. **Kubernetes Cluster** with kubectl configured

### Creating an Auth Token

You have two options for authentication:

#### Option 1: User Auth Token (for development/testing)

1. Go to Sentry → Settings → Account → API → Auth Tokens
2. Click "Create New Token"
3. Grant the following scopes:
   - `org:read`
   - `project:read`
   - `event:read`
   - `issue:read` (optional, for issue management)
   - `issue:write` (optional, for resolving/assigning issues)
4. Copy the generated token

#### Option 2: Internal Integration (recommended for production)

1. Go to Sentry → Settings → Developer Settings → Internal Integrations
2. Click "Create New Internal Integration"
3. Configure permissions:
   - **Organization**: Read
   - **Project**: Read
   - **Issue & Event**: Read (or Admin if you need write access)
4. Copy the token from the integration

## Tools

The Sentry MCP provides these tools:

| Tool | Description |
|------|-------------|
| `list_organizations` | List all organizations you have access to |
| `list_projects` | List projects in an organization |
| `list_issues` | List issues in a project |
| `get_issue` | Get detailed information about an issue |
| `get_issue_events` | Get events associated with an issue |
| `get_event` | Get detailed event information with stack trace |
| `resolve_issue` | Mark an issue as resolved |
| `unresolve_issue` | Reopen a resolved issue |
| `assign_issue` | Assign an issue to a user |
| `search_errors` | Search for errors using Sentry's search syntax |

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SENTRY_AUTH_TOKEN` | Sentry authentication token | Yes |
| `SENTRY_HOST` | Hostname for self-hosted Sentry (e.g., `sentry.example.com`) | No |

### Self-Hosted Sentry

For self-hosted Sentry installations:

1. Uncomment the `sentry-host` field in `secret.yaml` and set your hostname
2. Uncomment the `SENTRY_HOST` environment variable in `deployment.yaml`
3. Update the Dockerfile CMD to include the host flag (see below)

Update the Dockerfile CMD for self-hosted:
```dockerfile
CMD ["--port", "8000", "--stdio", "sh -c 'sentry-mcp --access-token=$SENTRY_AUTH_TOKEN --host=$SENTRY_HOST'"]
```

## Deployment

### 1. Build the Docker Image

```bash
docker build -t your-registry/sentry-mcp:latest .
docker push your-registry/sentry-mcp:latest
```

### 2. Create the Secret

Edit `secret.yaml` with your actual token:

```yaml
stringData:
  sentry-auth-token: "sntryu_your-actual-token-here"
```

Apply the secret:
```bash
kubectl apply -f secret.yaml
```

### 3. Update and Deploy

Edit `deployment.yaml` to set your image:
```yaml
image: your-registry/sentry-mcp:latest
```

Deploy:
```bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

### 4. Verify

```bash
# Check pod status
kubectl get pods -l app=sentry-mcp

# Check logs
kubectl logs -l app=sentry-mcp

# Test connectivity (should see SSE connection established)
kubectl run curl --image=curlimages/curl --rm -it --restart=Never -- \
  curl -s http://sentry-mcp:8000/health
```

## Holmes Integration

Add the MCP server to your Holmes configuration:

```yaml
mcp_servers:
  sentry:
    description: "Sentry error tracking and monitoring"
    config:
      url: "http://sentry-mcp.default.svc.cluster.local:8000/sse"
      mode: sse
      headers:
        Content-Type: "application/json"
```

See `holmes-config/sentry-toolset.yaml` for a complete example.

**Note:** Update the namespace in the URL if deploying to a different namespace (e.g., `sentry-mcp.sentry.svc.cluster.local`).

## Security Considerations

1. **Credentials** - Store auth tokens in Kubernetes Secrets, never in plain text
2. **Token Scope** - Use minimal permissions required for your use case
3. **Internal Integration** - Prefer Internal Integrations over User Auth Tokens for production
4. **Network Policies** - Consider restricting access to the MCP server pod
5. **Token Rotation** - Rotate tokens periodically and update the Kubernetes Secret
6. **Audit** - Enable Sentry audit logging to track API usage

### Recommended Token Scopes

| Use Case | Required Scopes |
|----------|-----------------|
| Read-only monitoring | `org:read`, `project:read`, `event:read` |
| Issue management | Above + `issue:read`, `issue:write` |
| Full access | Above + `team:read`, `member:read` |

## Troubleshooting

### MCP Server Not Starting

```bash
# Check pod status
kubectl describe pod -l app=sentry-mcp

# Check logs for startup errors
kubectl logs -l app=sentry-mcp

# Verify secret exists
kubectl get secret sentry-mcp-credentials
```

### Authentication Errors

1. Verify the token is valid in Sentry UI
2. Check token has required scopes
3. For Internal Integrations, ensure it's installed on the organization
4. Check if token has expired

### Connection Issues

```bash
# Test from within the cluster
kubectl run curl --image=curlimages/curl --rm -it --restart=Never -- \
  curl -v http://sentry-mcp:8000/health

# Check service endpoints
kubectl get endpoints sentry-mcp

# Check if pod is ready
kubectl get pods -l app=sentry-mcp -o wide
```

### Self-Hosted Sentry Issues

1. Verify `SENTRY_HOST` is set correctly (hostname only, no protocol)
2. Ensure network connectivity between cluster and Sentry instance
3. Check if self-hosted Sentry API is accessible

### "Tool not found" Errors

1. The Sentry MCP exposes all tools by default
2. Check logs for MCP initialization errors
3. Verify the MCP server started successfully

## File Structure

```
sentry/
├── Dockerfile                    # Container image with Supergateway + Sentry MCP
├── deployment.yaml               # Kubernetes Deployment
├── service.yaml                  # Kubernetes Service
├── secret.yaml                   # Credentials template
├── holmes-config/
│   └── sentry-toolset.yaml       # Holmes MCP configuration
└── README.md                     # This file
```

## References

- [Sentry MCP GitHub](https://github.com/getsentry/sentry-mcp)
- [Sentry MCP Documentation](https://docs.sentry.io/product/sentry-mcp/)
- [Sentry Auth Tokens](https://docs.sentry.io/account/auth-tokens/)
- [Sentry API Documentation](https://docs.sentry.io/api/)
- [Supergateway](https://github.com/supercorp-ai/supergateway)
