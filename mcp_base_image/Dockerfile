# MCP Base Image
# Base image for MCP servers using Supergateway

FROM supercorp/supergateway:latest

USER root

# Upgrade all Alpine packages to fix OS-level CVEs:
# - openssl 3.5.4 → latest (CVE-2025-15467, CVE-2025-69419, CVE-2025-69420, CVE-2025-69421)
# - tar 7.5.4 → latest (CVE-2026-24842)
RUN apk upgrade --no-cache

# Update npm to latest version for CVE handling, then patch npm's bundled deps
RUN npm install -g npm@latest && \
    cd /tmp && \
    npm pack minimatch@latest && npm pack tar@latest && \
    cd /usr/local/lib/node_modules/npm/node_modules/minimatch && \
    tar xzf /tmp/minimatch-*.tgz --strip-components=1 && \
    cd /usr/local/lib/node_modules/npm/node_modules/tar && \
    tar xzf /tmp/tar-*.tgz --strip-components=1 && \
    rm -rf /tmp/*.tgz

# Update vulnerable dependencies in supergateway:
# - @modelcontextprotocol/sdk (CVE-2026-25536)
# - @isaacs/brace-expansion (CVE-2026-25547)
# - qs (CVE-2025-15284)
RUN cd /usr/local/lib/node_modules/supergateway && \
    npm install @modelcontextprotocol/sdk@latest @isaacs/brace-expansion@latest qs@latest \
    minimatch@latest tar@latest && \
    sed -i 's/"prev-modelcontextprotocol-sdk": "npm:@modelcontextprotocol\/sdk@[^"]*"/"prev-modelcontextprotocol-sdk": "npm:@modelcontextprotocol\/sdk@latest"/' package.json && \
    npm install && \
    npm audit fix --force || true
