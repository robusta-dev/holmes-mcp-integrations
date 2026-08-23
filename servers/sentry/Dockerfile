# Sentry MCP Server with Supergateway
# Wraps the Sentry MCP stdio server and exposes it as SSE HTTP API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:0.1.6

# Switch to root to install dependencies
USER root

# Install Sentry MCP server globally. Pinned so the image is reproducible and so
# the dependency tree we patch below is the tree that actually ships.
RUN npm install -g @sentry/mcp-server@0.37.0

# Patch vulnerable transitive deps inside @sentry/mcp-server's own node_modules:
# - fast-uri  (CVE-2026-16221 → >=3.1.4, CVE-2026-18446 → >=3.1.5), pulled in by ajv.
#   Held to the 3.x line on purpose: ajv declares fast-uri "^3.0.1", so installing
#   4.x here would leave ajv resolving its own vulnerable nested copy again.
# - ip-address (CVE-2026-69192 → >=10.3.1)
#
# Overlaid from `npm pack` tarballs rather than `npm install`d in place: the
# published @sentry/mcp-server depends on @sentry/mcp-core, a workspace package
# that is bundled into the tarball but never published, so any `npm install` run
# inside this directory re-resolves the tree and dies on E404.
RUN cd /tmp && \
    npm pack "fast-uri@^3.1.5" && npm pack "ip-address@^10.3.1" && \
    for pkg in fast-uri ip-address; do \
        for dir in $(find /usr/local/lib/node_modules/@sentry/mcp-server -type d -name "$pkg"); do \
            rm -rf "$dir" && mkdir -p "$dir" && \
            tar xzf /tmp/$pkg-*.tgz --strip-components=1 -C "$dir"; \
        done; \
    done && \
    rm -rf /tmp/*.tgz && \
    npm cache clean --force

USER node
EXPOSE 8000

# The access token is passed via environment variable
# Supergateway will execute the sentry-mcp command with the token
CMD ["--port", "8000", "--stdio", "sh -c 'sentry-mcp --access-token=$SENTRY_AUTH_TOKEN'"]
