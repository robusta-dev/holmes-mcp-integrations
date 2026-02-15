# Grafana MCP Server

An MCP server that provides Grafana integration for querying dashboards, metrics, logs, and alerts.

Uses the official [mcp-grafana](https://github.com/grafana/mcp-grafana) server from Grafana Labs.

## Overview

This MCP server enables Holmes to interact with Grafana. It can be used to:

- Search and retrieve dashboards
- Query Prometheus metrics via PromQL
- Query Loki logs via LogQL
- List and inspect alert rules
- Access OnCall schedules and alert groups
- Create and query annotations

## Architecture

```
Holmes -> Remote MCP (streamable-http) -> Grafana MCP Server -> Grafana API
                                                |
                                Running in Kubernetes as Deployment
                                (Credentials via Kubernetes Secrets)
```

## Quick Start

### 1. Create a Grafana Service Account

1. In Grafana, go to **Administration -> Users and Access -> Service Accounts**
2. Click **Add service account**
3. Set the role to **Viewer** (sufficient for read-only access)
4. Click **Create**
5. Go into the created service account and click **Add service account token**
6. Click **Generate token** with no expiration (or the longest duration available)
7. Copy the token (starts with `glsa_...`)

### 2. Create the Kubernetes Secret

Using your service account token and Grafana URL, create the secret:

```bash
kubectl create secret generic grafana-mcp-secret \
  --from-literal=GRAFANA_SERVICE_ACCOUNT_TOKEN='<your-service-account-token>' \
  --from-literal=GRAFANA_URL='<your-grafana-url>'
```

### 3. Deploy

```bash
kubectl apply -f deployment.yaml
```

### 4. Verify

```bash
# Check pod status
kubectl get pods -l app=grafana-mcp

# Check logs
kubectl logs -l app=grafana-mcp

```

## Holmes Integration

Add the MCP server to your Holmes configuration.
You can add custom instructions under the `llm_instructions` section to instruct Holmes when and how to use Grafana.

```yaml
mcp_servers:
  grafana:
    description: "Grafana dashboards, metrics, logs, and alerting"
    config:
      url: "http://grafana-mcp.default.svc.cluster.local:8000/mcp"
      mode: streamable-http
      headers:
        Content-Type: "application/json"
    llm_instructions: |
      Use the Grafana MCP to query dashboards, metrics (Prometheus), and logs (Loki).
      When investigating issues, search for relevant dashboards and query Prometheus metrics or Loki logs for the affected services.
```

## Tools

### Dashboard Tools

| Tool | Description |
|------|-------------|
| `search_dashboards` | Search for dashboards by query string |
| `get_dashboard_by_uid` | Retrieve complete dashboard details |
| `get_dashboard_summary` | Get compact dashboard summary |
| `get_dashboard_panel_queries` | Retrieve panel queries from a dashboard |
| `update_dashboard` | Create or update a dashboard (write mode) |

### Datasource Tools

| Tool | Description |
|------|-------------|
| `list_datasources` | List all configured datasources |
| `get_datasource_by_uid` | Get datasource details by UID |
| `get_datasource_by_name` | Get datasource details by name |

### Prometheus Tools

| Tool | Description |
|------|-------------|
| `query_prometheus` | Query Prometheus using PromQL |
| `list_prometheus_metric_names` | List metric names with regex filtering |
| `list_prometheus_label_names` | List label names |
| `list_prometheus_label_values` | Get values for a specific label |

### Loki Tools

| Tool | Description |
|------|-------------|
| `query_loki_logs` | Execute LogQL query for logs or metrics |
| `list_loki_label_names` | List all available label names |
| `list_loki_label_values` | Get unique values for a label |
| `query_loki_stats` | Get statistics about log streams |
| `query_loki_patterns` | Retrieve detected log patterns |

### Alerting Tools

| Tool | Description |
|------|-------------|
| `list_alert_rules` | List alert rules with label filtering |
| `get_alert_rule_by_uid` | Get full alert rule configuration |
| `list_contact_points` | List notification contact points |

### Incident Tools

| Tool | Description |
|------|-------------|
| `list_incidents` | List Grafana incidents |
| `get_incident` | Get incident details |
| `create_incident` | Create a new incident (write mode) |

## Security Considerations

1. **Service Account** - Use a service account with **Viewer** role for read-only access
2. **Credentials** - Store tokens in Kubernetes Secrets, never in plain text
3. **Read-only Mode** - Use `--disable-write` flag to prevent write operations
4. **Network Policies** - Consider restricting access to the MCP server pod
5. **Token Scope** - The service account role controls what the MCP server can access

## Troubleshooting

### MCP Server Not Starting

```bash
# Check pod status
kubectl describe pod -l app=grafana-mcp

# Check logs
kubectl logs -l app=grafana-mcp

# Verify secret exists
kubectl get secret grafana-mcp-secret
```

### Authentication Errors

1. Verify the service account token is valid in Grafana UI
2. Ensure the service account has at least **Viewer** role
3. Check the `GRAFANA_URL` is reachable from within the cluster

## References

- [mcp-grafana GitHub](https://github.com/grafana/mcp-grafana)
- [Grafana Service Accounts](https://grafana.com/docs/grafana/latest/administration/service-accounts/)
