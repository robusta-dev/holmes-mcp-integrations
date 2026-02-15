# Grafana MCP Server
# Uses the official grafana/mcp-grafana image which natively supports SSE transport.
# Provides access to dashboards, Prometheus/Loki/Mimir queries, alerting,
# incidents, OnCall, and more.
#
# Supports Grafana Cloud, self-hosted Grafana, and Mimir deployments.

FROM grafana/mcp-grafana:latest

EXPOSE 8000

# Run in SSE mode on port 8000.
# All configuration is passed via environment variables:
#   GRAFANA_URL                      - Grafana instance URL (required)
#   GRAFANA_SERVICE_ACCOUNT_TOKEN    - Service account token (recommended)
#   GRAFANA_USERNAME / GRAFANA_PASSWORD - Basic auth (alternative)
#   GRAFANA_ORG_ID                   - Organization ID for multi-org setups
ENTRYPOINT ["mcp-grafana"]
CMD ["--transport", "sse", "--address", "0.0.0.0:8000"]
