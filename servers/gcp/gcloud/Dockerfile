# gcloud MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

# Switch to root to install dependencies
USER root

# Install gcloud SDK dependencies (py3-pip not needed - gcloud SDK installed via curl)
RUN apk add --no-cache \
    python3 \
    bash \
    curl \
    gcc \
    python3-dev \
    musl-dev \
    linux-headers

# Install Google Cloud SDK without any configuration
ENV PATH="/google-cloud-sdk/bin:${PATH}"
RUN curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-550.0.0-linux-x86_64.tar.gz && \
    tar xzf google-cloud-cli-550.0.0-linux-x86_64.tar.gz && \
    rm google-cloud-cli-550.0.0-linux-x86_64.tar.gz && \
    /google-cloud-sdk/install.sh --quiet --usage-reporting=false --path-update=false && \
    rm -rf /root/.config/gcloud && \
    rm -rf /root/.gsutil && \
    rm -rf /root/.docker

# Pre-install the gcloud MCP package to cache it
RUN npx -y @google-cloud/gcloud-mcp --version || true

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch back to node user for security
USER node

# Expose the SSE API port
EXPOSE 8000

# Use entrypoint to enable ADC
ENTRYPOINT ["/entrypoint.sh"]

# Run Supergateway with gcloud-mcp as the stdio process
CMD ["node", "/usr/local/bin/supergateway", "--port", "8000", "--stdio", "npx -y @google-cloud/gcloud-mcp"]
