# MCP Base Image
# Base image for MCP servers using Supergateway

FROM supercorp/supergateway:latest

USER root

# Upgrade all Alpine packages to fix OS-level CVEs:
# - openssl 3.5.6 → 3.5.7-r0 (CVE-2026-34180, CVE-2026-34181, CVE-2026-34182,
#   CVE-2026-34183, CVE-2026-42764, CVE-2026-45445, CVE-2026-45447,
#   CVE-2026-7383, CVE-2026-9076)
# - zlib 1.3.1 → 1.3.2 (CVE-2026-22184)
# - tar 7.5.4 → latest (CVE-2026-24842)
RUN apk upgrade --no-cache

# Replace the bundled Node.js binary (20.19.5) with 20.20.2 to fix
# CVE-2025-55130, CVE-2025-55131, CVE-2025-59465, CVE-2025-59466 and
# CVE-2026-21637 (fixed in 20.20.0) plus CVE-2026-21710 (fixed in 20.20.2).
# Upstream supergateway still ships 20.19.5, so swap the musl binary in place.
RUN NODE_VERSION=20.20.2 && \
    case "$(apk --print-arch)" in \
        x86_64)  NODE_ARCH=x64 ;; \
        aarch64) NODE_ARCH=arm64 ;; \
        *) echo "unsupported arch: $(apk --print-arch)" && exit 1 ;; \
    esac && \
    wget -qO /tmp/node.tar.gz "https://unofficial-builds.nodejs.org/download/release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}-musl.tar.gz" && \
    tar -xzf /tmp/node.tar.gz -C /usr/local/bin --strip-components=2 "node-v${NODE_VERSION}-linux-${NODE_ARCH}-musl/bin/node" && \
    rm /tmp/node.tar.gz && \
    node --version

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
# - esbuild 0.27.7 → 0.28.1 (GHSA-gv7w-rqvm-qjhr)
RUN cd /usr/local/lib/node_modules/supergateway && \
    npm install @modelcontextprotocol/sdk@latest @isaacs/brace-expansion@latest qs@latest \
    minimatch@latest tar@latest cross-spawn@latest esbuild@latest && \
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

# Fix stdio→SSE shared-Server crash on the 2nd SSE client.
# supergateway 3.4.3's stdioToSse adapter declares a single `Server` at
# the gateway-function scope and calls `server.connect()` on it for every
# incoming SSE GET. @modelcontextprotocol/sdk >= 1.26 (the fix for
# CVE-2026-25536, "cross-client response leak via shared Server reuse")
# refuses the second connect() with "Already connected to a transport" —
# the throw escapes Express, Node exits 1, kubelet crashloops the pod.
# This patch shadows the outer Server with a per-connection one inside
# the SSE handler, closing the CVE properly (per-client isolation) and
# eliminating the crash. Version-pinned — build fails if supergateway
# is bumped past the tested 3.4.3.
COPY patch-supergateway-sse-transport.sh /tmp/
RUN chmod +x /tmp/patch-supergateway-sse-transport.sh && \
    /tmp/patch-supergateway-sse-transport.sh && \
    rm /tmp/patch-supergateway-sse-transport.sh
