# MCP Base Image
# Base image for MCP servers using Supergateway

FROM supercorp/supergateway:latest

USER root

# Upgrade all Alpine packages to fix OS-level CVEs:
# - openssl 3.5.4 → 3.5.6 (CVE-2025-15467, CVE-2025-69419, CVE-2025-69420,
#   CVE-2025-69421, CVE-2026-2673, CVE-2026-31790, CVE-2026-28388,
#   CVE-2026-28389, CVE-2026-28390)
# - zlib 1.3.1 → 1.3.2 (CVE-2026-22184)
# - tar 7.5.4 → latest (CVE-2026-24842)
RUN apk upgrade --no-cache

# Update npm to latest version for CVE handling, then patch npm's bundled deps:
# - minimatch, tar: general updates
# - picomatch (CVE-2026-33671)
# - brace-expansion (CVE-2026-33750)
RUN npm install -g npm@latest && \
    cd /tmp && \
    npm pack minimatch@latest && npm pack tar@latest && npm pack picomatch@latest && npm pack brace-expansion@latest && \
    cd /usr/local/lib/node_modules/npm/node_modules/minimatch && \
    tar xzf /tmp/minimatch-*.tgz --strip-components=1 && \
    cd /usr/local/lib/node_modules/npm/node_modules/tar && \
    tar xzf /tmp/tar-*.tgz --strip-components=1 && \
    cd /usr/local/lib/node_modules/npm/node_modules/tinyglobby/node_modules/picomatch && \
    tar xzf /tmp/picomatch-*.tgz --strip-components=1 && \
    cd /usr/local/lib/node_modules/npm/node_modules/brace-expansion && \
    tar xzf /tmp/brace-expansion-*.tgz --strip-components=1 && \
    rm -rf /tmp/*.tgz

# Update vulnerable dependencies in supergateway:
# - @modelcontextprotocol/sdk (CVE-2026-25536)
# - @isaacs/brace-expansion (CVE-2026-25547)
# - qs (CVE-2025-15284)
# - cross-spawn (CVE-2024-21538)
RUN cd /usr/local/lib/node_modules/supergateway && \
    npm install @modelcontextprotocol/sdk@latest @isaacs/brace-expansion@latest qs@latest \
    minimatch@latest tar@latest cross-spawn@latest && \
    sed -i 's/"prev-modelcontextprotocol-sdk": "npm:@modelcontextprotocol\/sdk@[^"]*"/"prev-modelcontextprotocol-sdk": "npm:@modelcontextprotocol\/sdk@latest"/' package.json && \
    npm install && \
    npm audit fix --force || true

# Also update cross-spawn in npm's global node_modules (CVE-2024-21538)
RUN cd /tmp && npm pack cross-spawn@latest && \
    for dir in $(find /usr/local/lib/node_modules -name cross-spawn -type d 2>/dev/null); do \
        tar xzf /tmp/cross-spawn-*.tgz --strip-components=1 -C "$dir" 2>/dev/null || true; \
    done && \
    rm -rf /tmp/*.tgz

# Fix child process leak in stateless streamableHttp mode.
# Every POST spawns a stdio child that is never killed after the response
# completes, leaking memory until OOM. This patch adds res.on('close')
# to kill the child. Version-pinned — build fails if supergateway changes.
COPY patch-supergateway-child-leak.sh /tmp/
RUN chmod +x /tmp/patch-supergateway-child-leak.sh && \
    /tmp/patch-supergateway-child-leak.sh && \
    rm /tmp/patch-supergateway-child-leak.sh
