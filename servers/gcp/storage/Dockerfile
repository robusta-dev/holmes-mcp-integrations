# Google Cloud Storage MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

# Switch to root to install dependencies
USER root

# Install minimal dependencies for storage-mcp.
# Drop ensurepip's bundled pip wheel (otherwise scanners flag CVE-2025-8869 on the unused bootstrap wheel).
RUN apk add --no-cache \
    python3 \
    bash \
    curl && \
    rm -f /usr/lib/python3.*/ensurepip/_bundled/pip-*.whl

# Pre-install the storage MCP package to cache it
RUN npx -y @google-cloud/storage-mcp --version || true

# Switch back to node user for security
USER node

# Expose the SSE API port (different from other MCPs)
EXPOSE 8002

# Run Supergateway with storage-mcp as the stdio process
CMD ["--port", "8002", "--stdio", "npx -y @google-cloud/storage-mcp"]