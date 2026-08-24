# Sentry MCP Server with Supergateway
# Wraps the Sentry MCP stdio server and exposes it as SSE HTTP API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:0.1.6

# Switch to root to install dependencies
USER root

# Install Sentry MCP server globally
RUN npm install -g @sentry/mcp-server

# Patch vulnerable transitive deps in the @sentry/mcp-server tree. These come
# from @modelcontextprotocol/sdk and ajv, which the package pins, so they cannot
# be bumped with a plain npm install:
# - fast-uri 3.1.3 → 3.1.5 (CVE-2026-16221, CVE-2026-18446)
# - ip-address 10.2.0 → 10.3.1 (CVE-2026-69192, CVE-2026-54272, CVE-2026-69198)
# - hono 4.12.27 → 4.12.34 (CVE-2026-69207, CVE-2026-71848, CVE-2026-71850)
# - @hono/node-server 1.19.14 → 1.19.15 (GHSA-frvp-7c67-39w9)
RUN /usr/local/bin/patch-node-deps.sh /usr/local/lib/node_modules/@sentry/mcp-server \
    fast-uri@^3.1.5 ip-address@^10.3.1 hono@^4.12.34 @hono/node-server@^1.19.15

USER node
EXPOSE 8000

# The access token is passed via environment variable
# Supergateway will execute the sentry-mcp command with the token
CMD ["--port", "8000", "--stdio", "sh -c 'sentry-mcp --access-token=$SENTRY_AUTH_TOKEN'"]
