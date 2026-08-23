# Google Cloud Observability MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:0.1.6

# Switch to root to install dependencies
USER root

# Install minimal dependencies for observability-mcp
RUN apk add --no-cache \
    python3 \
    bash \
    curl

# Pull the newest Alpine patches for everything the layer above dragged in.
# c-ares 1.34.6-r0 → 1.34.8-r0 (CVE-2026-33630), arriving as a libcurl dep, and
# python3 3.12.13-r0 → 3.12.14-r0 (CVE-2026-11940, CVE-2026-15308, CVE-2026-3644,
# CVE-2026-4786, CVE-2026-6100, CVE-2026-7210).
RUN apk upgrade --no-cache

# Smoke-test that the observability MCP package resolves at build time.
#
# This used to be described as "pre-install to cache it", but it never cached
# anything usable: npx writes to $HOME/.npm, HOME is /root here, /root is mode
# 0700, and the container runs as uid 1000 (node). The runtime user could never
# read the cache, so it only shipped a stale, unreachable copy of the dependency
# tree — which is where the brace-expansion/fast-uri/ip-address findings under
# /root/.npm/_npx came from. Delete it; runtime behaviour is unchanged because
# `npx -y` already re-resolved the package on every container start.
RUN npx -y @google-cloud/observability-mcp --version || true
RUN rm -rf /root/.npm

# Switch back to node user for security
USER node

# Expose the SSE API port (different from gcloud-mcp)
EXPOSE 8001

# Run Supergateway with observability-mcp as the stdio process
CMD ["--port", "8001", "--stdio", "npx -y @google-cloud/observability-mcp"]
