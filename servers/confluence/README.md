# Confluence MCP Server

An MCP server that provides Confluence integration for searching and retrieving documentation. Uses the [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) server to expose Confluence (and optionally Jira) tools.

## Overview

This MCP server enables Holmes to search and interact with Confluence documentation. It can be used to:

- Search for pages using CQL (Confluence Query Language)
- Retrieve page content and metadata
- Read comments on pages
- Create and update pages (if enabled)

## Architecture

```
Holmes -> Remote MCP (SSE) -> Confluence MCP Server -> Confluence API
                                      |
                      Running in Kubernetes as Deployment
                      (Credentials via Kubernetes Secrets)
```

## Quick Start

```bash
# 1. Create the secret with your Confluence credentials
# Edit secret.yaml with your actual values first
kubectl apply -f secret.yaml

# 2. Deploy the MCP server
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# 3. Verify it's running
kubectl get pods -l app=confluence-mcp
kubectl logs -l app=confluence-mcp
```

## Prerequisites

1. **Confluence Cloud Account** with API access
2. **API Token** - Generate at https://id.atlassian.com/manage-profile/security/api-tokens
3. **Kubernetes Cluster** with kubectl configured

For Server/Data Center deployments, use a Personal Access Token instead.

## Tools

### Confluence Tools

| Tool | Description |
|------|-------------|
| `confluence_search` | Search for pages using CQL |
| `confluence_get_page` | Get page details by ID |
| `confluence_get_page_content` | Get the full content of a page |
| `confluence_get_comments` | Get comments on a page |
| `confluence_create_page` | Create a new page |
| `confluence_update_page` | Update an existing page |
| `confluence_delete_page` | Delete a page |

### Jira Tools (Optional)

If Jira credentials are configured, these tools become available:

| Tool | Description |
|------|-------------|
| `jira_search` | Search with JQL |
| `jira_get_issue` | Get issue details |
| `jira_create_issue` | Create issues |
| `jira_update_issue` | Update issues |
| `jira_transition_issue` | Change status |
| `jira_add_comment` | Add comments |

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `CONFLUENCE_URL` | Confluence instance URL (e.g., `https://company.atlassian.net/wiki`) | Yes |
| `CONFLUENCE_USERNAME` | Email address for authentication | Yes |
| `CONFLUENCE_API_TOKEN` | API token from Atlassian account | Yes |
| `JIRA_URL` | Jira instance URL | No |
| `JIRA_USERNAME` | Email for Jira authentication | No |
| `JIRA_API_TOKEN` | API token for Jira | No |
| `ENABLED_TOOLS` | Comma-separated list of tools to enable | No |
| `READ_ONLY` | Set to `true` to disable write operations | No |

### Configuring Available Tools

Use the `ENABLED_TOOLS` environment variable to control which tools are exposed:

**Read-only Confluence (default):**
```yaml
- name: ENABLED_TOOLS
  value: "confluence_search,confluence_get_page,confluence_get_page_content,confluence_get_comments"
```

**Full Confluence access:**
```yaml
- name: ENABLED_TOOLS
  value: "confluence_search,confluence_get_page,confluence_get_page_content,confluence_get_comments,confluence_create_page,confluence_update_page,confluence_delete_page"
```

**Confluence + Jira read-only:**
```yaml
- name: ENABLED_TOOLS
  value: "confluence_search,confluence_get_page,confluence_get_page_content,jira_search,jira_get_issue"
```

**All tools (requires both Confluence and Jira credentials):**
Remove or comment out the `ENABLED_TOOLS` environment variable to enable all available tools.

## Deployment

### 1. Create the Secret

Edit `secret.yaml` with your actual credentials:

```yaml
stringData:
  confluence-url: "https://your-company.atlassian.net/wiki"
  confluence-username: "your-email@company.com"
  confluence-api-token: "your-actual-api-token"
```

Apply the secret:
```bash
kubectl apply -f secret.yaml
```

### 2. Customize the Deployment (Optional)

Edit `deployment.yaml` to:
- Change the namespace
- Adjust resource limits
- Configure enabled tools
- Add Jira credentials

### 3. Deploy

```bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

### 4. Verify

```bash
# Check pod status
kubectl get pods -l app=confluence-mcp

# Check logs
kubectl logs -l app=confluence-mcp

# Test connectivity
kubectl run curl --image=curlimages/curl --rm -it --restart=Never -- \
  curl -s http://confluence-mcp:8000/health
```

## Holmes Integration

Add the MCP server to your Holmes configuration:

```yaml
mcp_servers:
  confluence:
    description: "Confluence documentation search and retrieval"
    config:
      url: "http://confluence-mcp.default.svc.cluster.local:8000/sse"
      mode: sse
      headers:
        Content-Type: "application/json"
    llm_instructions: |
      Use the Confluence MCP to search and retrieve documentation.

      Available tools:
      - confluence_search: Search for pages using CQL
      - confluence_get_page: Get page details by ID
      - confluence_get_page_content: Get full page content

      CQL examples:
      - text ~ "deployment guide"
      - space = "ENGINEERING" AND text ~ "architecture"
```

See `holmes-config/confluence-toolset.yaml` for a complete example.

## CQL Query Reference

Confluence Query Language (CQL) examples:

| Query | Description |
|-------|-------------|
| `text ~ "kubernetes"` | Search for pages containing "kubernetes" |
| `title ~ "runbook*"` | Pages with title starting with "runbook" |
| `space = "DOCS"` | Pages in the DOCS space |
| `type = "page"` | Only pages (not blog posts) |
| `creator = "user@example.com"` | Pages created by specific user |
| `created >= "2024-01-01"` | Pages created after date |
| `space = "ENG" AND text ~ "api"` | Combined filters |

## Security Considerations

1. **Credentials** - Store API tokens in Kubernetes Secrets, never in plain text
2. **Read-only Mode** - Use `ENABLED_TOOLS` to restrict to read-only operations
3. **Network Policies** - Consider restricting access to the MCP server pod
4. **API Token Scope** - Use tokens with minimal necessary permissions
5. **Audit** - Enable logging to track API calls

## Troubleshooting

### MCP Server Not Starting

```bash
# Check pod status
kubectl describe pod -l app=confluence-mcp

# Check logs
kubectl logs -l app=confluence-mcp

# Verify secret exists
kubectl get secret confluence-mcp-credentials
```

### Authentication Errors

1. Verify the API token is valid at https://id.atlassian.com/manage-profile/security/api-tokens
2. Ensure the username is the email associated with the Atlassian account
3. Check the URL format (Cloud: `https://company.atlassian.net/wiki`)

### Connection Issues

```bash
# Test from within the cluster
kubectl run curl --image=curlimages/curl --rm -it --restart=Never -- \
  curl -v http://confluence-mcp:8000/health

# Check service endpoints
kubectl get endpoints confluence-mcp
```

### Tool Not Available

1. Check `ENABLED_TOOLS` configuration
2. Verify the tool name is correct
3. For Jira tools, ensure Jira credentials are configured

## File Structure

```
confluence/
├── deployment.yaml              # Kubernetes Deployment
├── service.yaml                 # Kubernetes Service
├── secret.yaml                  # Credentials template
├── holmes-config/
│   └── confluence-toolset.yaml  # Holmes MCP configuration
└── README.md                    # This file
```

## Compatibility

- **Confluence Cloud**: Fully supported
- **Confluence Server/Data Center**: v6.0+ supported (use Personal Access Token)
- **Jira Cloud**: Fully supported (optional)
- **Jira Server/Data Center**: v8.14+ supported (optional)

## References

- [mcp-atlassian GitHub](https://github.com/sooperset/mcp-atlassian)
- [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
- [CQL Documentation](https://developer.atlassian.com/cloud/confluence/advanced-searching-using-cql/)
