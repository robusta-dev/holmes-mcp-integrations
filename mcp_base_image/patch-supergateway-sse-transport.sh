#!/bin/sh
# patch-supergateway-sse-transport.sh
#
# Patches supergateway's stdioToSse gateway to use a per-SSE-connection
# Server instance instead of one shared across all clients.
#
# Symptom (without this patch):
#
#   [supergateway] New SSE connection from ::ffff:<ip>
#   .../sdk/dist/esm/shared/protocol.js:217
#       throw new Error('Already connected to a transport. Call close()
#       before connecting to a new transport, or use a separate Protocol
#       instance per connection.');
#       at Server.connect (.../sdk/.../shared/protocol.js:217:19)
#       at .../supergateway/dist/gateways/stdioToSse.js:58:22
#
# The unhandled rejection escapes Express, Node exits 1, kubelet
# crashloops the pod. First connection succeeds; the crash is on the 2nd
# SSE GET — fresh pods look healthy until any client reconnects.
#
# Root cause:
#
#   stdioToSse() declares `const server = new Server(...)` at the OUTER
#   scope of the gateway function, then the `app.get(ssePath, ...)`
#   handler calls `server.connect(newSseTransport)` for every incoming
#   SSE client against that same Server. MCP SDK >= 1.26 enforces the
#   documented "one Protocol per transport" contract (the fix for
#   CVE-2026-25536, "cross-client response leak via shared Server reuse")
#   and refuses the second connect().
#
# Why only "modern" builds crash:
#
#   supergateway 3.4.3 ships the same buggy gateway code regardless of
#   whatever SDK gets installed alongside it. Older bases (SDK < 1.26)
#   silently reused the Server across clients — vulnerable to the CVE
#   but not crashy. Newer bases (this repo's base image, which runs
#   `npm install @modelcontextprotocol/sdk@latest` to close the CVE)
#   pull SDK >= 1.26, which throws instead.
#
#   So patching the CVE made the gateway crash on reconnect. This script
#   makes both safe by giving each SSE client its own Server.
#
# Why streamableHttp is unaffected:
#
#   stdioToStatelessStreamableHttp.js uses a different code path that
#   creates a Server per request. It hits its own child-process-leak bug
#   (patched separately by patch-supergateway-child-leak.sh) but never
#   reuses a Server across transports, so the SDK guard never fires.
#
# Fix:
#
#   Insert a `const server = new Server(...)` line inside the
#   `app.get(ssePath, ...)` handler, just before the SSEServerTransport
#   is created. JavaScript block scoping means the inner const shadows
#   the outer one for the handler's closure; each SSE client gets its
#   own Server + transport pair, no cross-client state.
#
# Version-pinned: build fails if supergateway is upgraded past the
# tested version, forcing a human to re-verify the patch.

set -eu

EXPECTED_VERSION="3.4.3"
SG_DIR="/usr/local/lib/node_modules/supergateway"
TARGET="$SG_DIR/dist/gateways/stdioToSse.js"
MARKER="PATCH: per-connection Server (SSE transport-lifecycle fix)"

# ── 1. Version gate ──────────────────────────────────────────────────
ACTUAL_VERSION=$(node -e "process.stdout.write(require('$SG_DIR/package.json').version)")
if [ "$ACTUAL_VERSION" != "$EXPECTED_VERSION" ]; then
  echo "FATAL: supergateway version mismatch" >&2
  echo "  expected: $EXPECTED_VERSION" >&2
  echo "  actual:   $ACTUAL_VERSION" >&2
  echo "  The SSE transport patch must be re-verified for this version." >&2
  exit 1
fi
echo "supergateway version $ACTUAL_VERSION — matches expected $EXPECTED_VERSION"

if [ ! -f "$TARGET" ]; then
  echo "FATAL: target file not found: $TARGET" >&2
  exit 1
fi

# ── 2. Apply (idempotent, via node to handle template-literal anchor) ─
node - "$TARGET" "$MARKER" <<'NODE_EOF'
const fs = require('fs');
const file = process.argv[2];
const marker = process.argv[3];
let src = fs.readFileSync(file, 'utf8');

if (src.includes(marker)) {
  console.log('Patch already applied — skipping');
  process.exit(0);
}

const ANCHOR = 'const sseTransport = new SSEServerTransport(`${baseUrl}${messagePath}`, res);';
if (!src.includes(ANCHOR)) {
  console.error('FATAL: anchor not found in ' + file);
  console.error('  expected: ' + ANCHOR);
  process.exit(1);
}

const INSERT =
  '/* ' + marker + ' */\n' +
  "        const server = new Server({ name: 'supergateway', version: getVersion() }, { capabilities: {} });\n" +
  '        ';

src = src.replace(ANCHOR, INSERT + ANCHOR);
fs.writeFileSync(file, src);
console.log('Patch applied: per-connection Server inserted at SSE handler');
NODE_EOF

# ── 3. Verify ────────────────────────────────────────────────────────
if ! grep -qF "$MARKER" "$TARGET"; then
  echo "FATAL: patch verification failed" >&2
  exit 1
fi
