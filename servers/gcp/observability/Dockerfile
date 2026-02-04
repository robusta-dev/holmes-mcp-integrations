# Google Cloud Observability MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/devel/supergateway_base:latest

# Switch to root to install dependencies
USER root

# Install minimal dependencies for observability-mcp
RUN apk add --no-cache \
    python3 \
    bash \
    curl

# Pre-install the observability MCP package to cache it
RUN npx -y @google-cloud/observability-mcp --version || true

# Switch back to node user for security
USER node

# Expose the SSE API port (different from gcloud-mcp)
EXPOSE 8001

# Run Supergateway with observability-mcp as the stdio process
CMD ["--port", "8001", "--stdio", "npx -y @google-cloud/observability-mcp"]