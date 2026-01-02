# Google Cloud Storage MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM supercorp/supergateway:latest

# Switch to root to install dependencies
USER root

# Update npm to latest version for security
RUN npm install -g npm@latest

# Install minimal dependencies for storage-mcp
RUN apk add --no-cache \
    python3 \
    bash \
    curl

# Pre-install the storage MCP package to cache it
RUN npx -y @google-cloud/storage-mcp --version || true

# Switch back to node user for security
USER node

# Expose the SSE API port (different from other MCPs)
EXPOSE 8002

# Run Supergateway with storage-mcp as the stdio process
CMD ["--port", "8002", "--stdio", "npx -y @google-cloud/storage-mcp"]