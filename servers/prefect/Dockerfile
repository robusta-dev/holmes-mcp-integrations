# Prefect MCP Server with Supergateway
# Wraps the Prefect MCP stdio server and exposes it as SSE HTTP API
# Pre-installs prefect-mcp at build time (no runtime network needed)

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

# Switch to root to install Python and prefect-mcp
USER root

RUN apk add --no-cache python3 py3-pip

# Install prefect-mcp at build time so no outbound network is needed at runtime
RUN pip3 install --no-cache-dir --break-system-packages prefect-mcp

USER node
EXPOSE 8000

# Supergateway wraps the stdio-based prefect-mcp-server as SSE
# Auth credentials are passed via environment variables
CMD ["--port", "8000", "--stdio", "prefect-mcp-server"]
