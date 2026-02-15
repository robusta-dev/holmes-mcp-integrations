# Grafana MCP Server

An MCP server that provides Grafana integration for observability, dashboards, metrics querying, alerting, and incident management. Uses the official [mcp-grafana](https://github.com/grafana/mcp-grafana) server which natively supports SSE transport.

## Overview

This MCP server enables Holmes to interact with Grafana for full-stack observability. It supports:

- **Grafana Cloud** - Connect to your Grafana Cloud instance
- **Self-hosted Grafana** - Local or on-premise Grafana deployments
- **Mimir** - Query Mimir metrics through Grafana's Prometheus datasource interface

Capabilities include:

- Search, retrieve, and manage dashboards
- Execute PromQL queries (Prometheus / Mimir)
- Execute LogQL queries (Loki)
- Manage alert rules and contact points
- Create and track incidents (Grafana Incident)
- Access OnCall schedules and alert groups
- Manage annotations
- List and inspect datasources

## Architecture

```
Holmes -> Remote MCP (SSE) -> Grafana MCP Server -> Grafana API
                                    |
                    Running in Kubernetes as Deployment
                    (Credentials via Kubernetes Secrets)
```

The official Grafana MCP server natively supports SSE transport, so no Supergateway wrapper is needed.

## Quick Start

### Prerequisites

- Grafana 9.0 or later
- A service account token with appropriate permissions

### Creating a Service Account Token

1. Go to Grafana -> Administration -> Service accounts
2. Click "Add service account"
3. Assign the **Editor** role (or configure granular RBAC permissions below)
4. Click "Add service account token" and copy the generated token

### Deploy

```bash
# 1. Edit secret.yaml with your Grafana URL and credentials
vi secret.yaml

# 2. Create the secret
kubectl apply -f secret.yaml

# 3. Deploy the MCP server
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# 4. Verify it's running
kubectl get pods -l app=grafana-mcp
kubectl logs -l app=grafana-mcp
```

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GRAFANA_URL` | Grafana instance URL | Yes |
| `GRAFANA_SERVICE_ACCOUNT_TOKEN` | Service account token | Yes (or use basic auth) |
| `GRAFANA_USERNAME` | Username for basic auth | No (alternative to token) |
| `GRAFANA_PASSWORD` | Password for basic auth | No (alternative to token) |
| `GRAFANA_ORG_ID` | Numeric organization ID for multi-org setups | No |
| `GRAFANA_EXTRA_HEADERS` | JSON object of additional HTTP headers | No |

### Deployment Scenarios

#### Grafana Cloud

```yaml
stringData:
  grafana-url: "https://your-org.grafana.net"
  grafana-service-account-token: "glsa_xxxxxxxxxxxx"
```

#### Self-Hosted Grafana (in-cluster)

```yaml
stringData:
  grafana-url: "http://grafana.monitoring.svc.cluster.local:3000"
  grafana-service-account-token: "glsa_xxxxxxxxxxxx"
```

#### Self-Hosted Grafana (external)

```yaml
stringData:
  grafana-url: "https://grafana.your-company.com"
  grafana-service-account-token: "glsa_xxxxxxxxxxxx"
```

#### Mimir (via Grafana)

Mimir metrics are queried through Grafana's Prometheus datasource. Point `GRAFANA_URL` at the Grafana instance that has Mimir configured as a Prometheus datasource. The MCP server's Prometheus tools (`execute_prometheus_query`, `list_prometheus_metric_names`, etc.) will work against any Prometheus-compatible datasource, including Mimir.

```yaml
stringData:
  grafana-url: "https://your-grafana-with-mimir.example.com"
  grafana-service-account-token: "glsa_xxxxxxxxxxxx"
```

### RBAC Permissions

The service account needs permissions based on which tools you use. For convenience, assign the **Editor** role. For minimal permissions:

| Tool Category | Required Permissions |
|---------------|---------------------|
| Dashboards | `dashboards:read`, `dashboards:write` (scope: `dashboards:*`, `folders:*`) |
| Datasources | `datasources:read` (scope: `datasources:*`) |
| Prometheus / Mimir | `datasources:query` (scope: `datasources:*`) |
| Loki | `datasources:query` (scope: `datasources:*`) |
| Alerting | `alert.rules:read`, `alert.rules:write` |
| Incidents | Viewer or Editor role on Grafana Incident |
| OnCall | Editor role on Grafana OnCall |
| Annotations | `annotations:read`, `annotations:write` |

## Holmes Integration

Add the MCP server to your Holmes helm values:

```yaml
  mcp_servers:
    grafana:
      description: "Grafana observability platform - dashboards, Prometheus, Loki, Mimir, alerting, and incidents"
      config:
        url: "http://grafana-mcp.default.svc.cluster.local:8000/sse"
        mode: sse
        headers:
          Content-Type: "application/json"
```

Update the namespace in the URL if deploying to a different namespace.

## Tools

Key tools provided by the Grafana MCP server:

| Tool | Description |
|------|-------------|
| `search_dashboards` | Search dashboards by query string |
| `get_dashboard_by_uid` | Retrieve a dashboard by UID |
| `list_datasources` | List all configured datasources |
| `execute_prometheus_query` | Execute a PromQL query (works with Mimir) |
| `list_prometheus_metric_names` | List available Prometheus/Mimir metric names |
| `execute_loki_query` | Execute a LogQL query |
| `list_alert_rules` | List configured alert rules |
| `get_alert_rule_by_uid` | Get details of a specific alert rule |
| `search_incidents` | Search Grafana Incident |
| `create_incident` | Create a new incident |
| `list_oncall_schedules` | List OnCall schedules |
| `get_annotations` | Retrieve annotations |

For the full list of 100+ tools, see the [mcp-grafana documentation](https://github.com/grafana/mcp-grafana).

## Security Considerations

1. **Credentials** - Store tokens in Kubernetes Secrets, never in plain text
2. **Minimal Permissions** - Use granular RBAC instead of broad Editor role in production
3. **Read-Only Mode** - Pass `--disable-write` as an additional argument to prevent mutations
4. **Network Policies** - Restrict access to the MCP server pod
5. **Token Rotation** - Rotate service account tokens periodically
6. **TLS** - Use TLS for connections to external Grafana instances

## Troubleshooting

### MCP Server Not Starting

```bash
kubectl describe pod -l app=grafana-mcp
kubectl logs -l app=grafana-mcp
kubectl get secret grafana-mcp-credentials
```

### Authentication Errors

1. Verify the service account token is valid in Grafana UI
2. Check the service account has required RBAC permissions
3. Ensure `GRAFANA_URL` is correct and reachable from the cluster
4. For Grafana Cloud, ensure the token matches the correct stack

### Connection Issues

```bash
# Test from within the cluster
kubectl run curl --image=curlimages/curl --rm -it --restart=Never -- \
  curl -v http://grafana-mcp:8000/healthz

# Check service endpoints
kubectl get endpoints grafana-mcp
```

### Mimir Queries Not Working

1. Verify Mimir is configured as a Prometheus datasource in Grafana
2. Check the service account has `datasources:query` permission
3. Use `list_datasources` to confirm Mimir datasource is visible

## File Structure

```
grafana/
├── Dockerfile                    # Container image using official grafana/mcp-grafana
├── auto-build-config.yaml        # Build version config
├── build-push.sh                 # Docker build and push script
├── deployment.yaml               # Kubernetes Deployment
├── service.yaml                  # Kubernetes Service
├── secret.yaml                   # Credentials template
├── holmes-config/
│   └── grafana-toolset.yaml      # Holmes MCP configuration
└── README.md                     # This file
```

## References

- [mcp-grafana GitHub](https://github.com/grafana/mcp-grafana)
- [Grafana Service Accounts](https://grafana.com/docs/grafana/latest/administration/service-accounts/)
- [Grafana RBAC](https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/access-control/)
- [Mimir Documentation](https://grafana.com/docs/mimir/latest/)
