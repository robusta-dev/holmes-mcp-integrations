# Google Cloud Storage MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:0.1.6

# Switch to root to install dependencies
USER root

# Install minimal dependencies for storage-mcp
RUN apk add --no-cache \
    python3 \
    bash \
    curl

# Refresh anything the base layer left stale, notably c-ares (CVE-2026-33630)
# and curl/libcurl (CVE-2026-5545). Runs after apk add so the packages installed
# just above are covered too.
RUN apk upgrade --no-cache

# Install the MCP server at build time instead of resolving it with `npx -y` on
# every container start. `npx -y` re-resolves the package against the registry
# when the container boots, which reinstalls the dependency tree and silently
# undoes the CVE patches applied below -- so the tree that ships in the image has
# to be the tree that runs. It also means startup no longer depends on
# registry.npmjs.org being reachable. Matches how servers/sentry does it.
RUN npm install -g @google-cloud/storage-mcp

# Patch vulnerable transitive deps in the installed MCP server tree. These come
# from @modelcontextprotocol/sdk, ajv and @google-cloud/storage, which the MCP
# package pins:
# - fast-uri 3.1.3 → 3.1.5 (CVE-2026-16221, CVE-2026-18446)
# - ip-address 10.2.0 → 10.3.1 (CVE-2026-69192, CVE-2026-54272, CVE-2026-69198)
# - hono 4.12.27 → 4.12.34 (CVE-2026-69207, CVE-2026-71848, CVE-2026-71850)
# - @hono/node-server 1.19.14 → 1.19.15 (GHSA-frvp-7c67-39w9)
# - fast-xml-parser 5.9.3 → 5.10.1 (CVE-2026-73569)
# - uuid 9.0.1 → 11.1.1 (CVE-2026-41907) — no fix exists on the 9.x line, and
#   the only copies are gaxios/teeny-request internals, which use the named
#   v4 export that 11.x still provides.
RUN /usr/local/bin/patch-node-deps.sh /usr/local/lib/node_modules/@google-cloud/storage-mcp \
    fast-uri@^3.1.5 ip-address@^10.3.1 hono@^4.12.34 @hono/node-server@^1.19.15 \
    fast-xml-parser@^5.10.1 uuid@^11.1.1

# Switch back to node user for security
USER node

# Expose the SSE API port (different from other MCPs)
EXPOSE 8002

# Run Supergateway with storage-mcp as the stdio process
CMD ["--port", "8002", "--stdio", "storage-mcp"]