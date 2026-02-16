# Prefect MCP Server with Supergateway
# Wraps the Prefect MCP stdio server and exposes it as SSE HTTP API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

# Switch to root to install Python and Prefect MCP
USER root

RUN apk add --no-cache python3 py3-pip

# Install Prefect MCP server
RUN pip3 install --no-cache-dir --break-system-packages prefect-mcp

USER node
EXPOSE 8000

# Supergateway wraps the stdio-based prefect-mcp-server as SSE on port 8000
# Prefect credentials are passed via environment variables (PREFECT_API_URL, PREFECT_API_KEY)
CMD ["--port", "8000", "--stdio", "prefect-mcp-server"]
