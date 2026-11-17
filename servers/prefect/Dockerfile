# Prefect MCP Server with Supergateway
# Wraps the Prefect MCP stdio server and exposes it as SSE HTTP API
# Pre-installs prefect-mcp at build time (no runtime network needed)

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:0.1.7

# Switch to root to install Python and prefect-mcp
USER root

RUN apk upgrade --no-cache && apk add --no-cache python3 py3-pip

# Install prefect-mcp at build time so no outbound network is needed at runtime
RUN pip3 install --no-cache-dir --break-system-packages prefect-mcp==0.0.1b13 && \
    pip3 install --no-cache-dir --break-system-packages --upgrade \
    jaraco-context wheel cryptography setuptools pip \
    "authlib>=1.9.0" "certifi>=2026.10.18" "dnspython>=2.9.0" "msgpack>=1.2.3" "urllib3>=2.8.0"

USER node
EXPOSE 8000

# Supergateway wraps the stdio-based prefect-mcp-server as SSE
# Auth credentials are passed via environment variables
CMD ["--port", "8000", "--stdio", "prefect-mcp-server"]
