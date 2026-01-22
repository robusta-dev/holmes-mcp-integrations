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
# 1. Create the secret with your Sentry credentials
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

#### User Auth Token (for development/testing)

1. Go to Sentry → Settings → Account → API → Auth Tokens
2. Click "Create New Token"
3. Grant the following scopes:
   - `org:read`
   - `project:read`
   - `event:read`
   - `issue:read` (optional, for issue management)
   - `issue:write` (optional, for resolving/assigning issues)
4. Copy the generated token


## Holmes Integration

Add the MCP server to your Holmes helm values:

```yaml
  mcp_servers:
    sentry:
      description: "Sentry error tracking and issue management"
      config:
        url: "http://sentry-mcp.default.svc.cluster.local:8000/sse"
        mode: sse
        headers:
          Content-Type: "application/json"
      llm_instructions: "Use Sentry tools to investigate application errors, get issue details, and analyze root causes with Holmes. When investigating sentry alert, try understanding the cause. The to find the relevant github repo, using the github mcp integration, or any other way. Try to find when the problematic code was created, by who."
```

**Note:** Update the namespace in the URL if deploying to a different namespace (e.g., `sentry-mcp.sentry.svc.cluster.local`).

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
