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

# Update npm, then patch npm's bundled deps.
# npm is held at ^11 rather than @latest: npm 12 requires Node
# ^22.22.2 || ^24.15.0 || >=26.0.0, and this image pins Node 20.20.2 above, so
# `npm@latest` now fails the build outright with EBADENGINE. 11.x is the newest
# line that still supports Node 20 (engines: ^20.17.0 || >=22.9.0). The npm
# version does not affect the CVE fixes below — those are applied explicitly.
# TODO: move this image to a supported Node LTS (Node 20 went EOL 2026-04-30),
# then drop the ^11 pin.
# Patched here:
# - minimatch, tar: general updates
# - picomatch (CVE-2026-33671)
# - brace-expansion (CVE-2026-33750)
RUN npm install -g npm@^11 && \
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
# - fast-uri 3.1.3 → 3.1.5 (CVE-2026-16221, CVE-2026-18446)
# - ip-address 10.2.0 → 10.3.1 (CVE-2026-69192, CVE-2026-54272, CVE-2026-69198)
# - hono 4.12.27 → 4.12.34 (CVE-2026-69207, CVE-2026-71848, CVE-2026-71850)
# - @hono/node-server 1.19.14 → 1.19.15 (GHSA-frvp-7c67-39w9)
# hono/@hono/node-server are held on their current major on purpose: the
# supergateway 3.4.3 patch scripts below are version-pinned against that tree,
# and 1.19.15 / 4.12.34 already carry the fixes.
RUN cd /usr/local/lib/node_modules/supergateway && \
    npm install @modelcontextprotocol/sdk@latest @isaacs/brace-expansion@latest qs@latest \
    minimatch@latest tar@latest cross-spawn@latest esbuild@latest \
    fast-uri@^3.1.5 ip-address@^10.3.1 hono@^4.12.34 @hono/node-server@^1.19.15 && \
    sed -i 's/"prev-modelcontextprotocol-sdk": "npm:@modelcontextprotocol\/sdk@[^"]*"/"prev-modelcontextprotocol-sdk": "npm:@modelcontextprotocol\/sdk@latest"/' package.json && \
    npm install && \
    npm audit fix --force || true

# Patch npm's bundled deps that `npm install -g npm@latest` still ships stale:
# - ip-address 10.2.0 → 10.3.1 (CVE-2026-69192, CVE-2026-54272, CVE-2026-69198)
# - undici 6.27.0 → 6.28.0 (CVE-2026-15157, CVE-2026-16728, CVE-2026-16729)
# Both stay on their current major so npm itself keeps working.
RUN cd /tmp && \
    npm pack ip-address@^10.3.1 && npm pack undici@^6.28.0 && \
    for pkg in ip-address undici; do \
        for dir in $(find /usr/local/lib/node_modules -type d -name "$pkg" 2>/dev/null); do \
            tar xzf /tmp/"$pkg"-*.tgz --strip-components=1 -C "$dir" 2>/dev/null || true; \
        done; \
    done && \
    rm -rf /tmp/*.tgz

# Also update cross-spawn in npm's global node_modules (CVE-2024-21538)
RUN cd /tmp && npm pack cross-spawn@latest && \
    for dir in $(find /usr/local/lib/node_modules -name cross-spawn -type d 2>/dev/null); do \
        tar xzf /tmp/cross-spawn-*.tgz --strip-components=1 -C "$dir" 2>/dev/null || true; \
    done && \
    rm -rf /tmp/*.tgz

# Ship a helper child images use to swap vulnerable transitive deps out of an
# already-installed MCP server tree (npm -g install or npx prewarm cache).
COPY patch-node-deps.sh /usr/local/bin/patch-node-deps.sh
RUN chmod +x /usr/local/bin/patch-node-deps.sh

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
