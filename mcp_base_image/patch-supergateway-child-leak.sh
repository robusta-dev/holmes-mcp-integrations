#!/bin/sh
# patch-supergateway-child-leak.sh
#
# Patches supergateway's stateless streamableHttp gateway to kill child
# processes after each request completes. Without this patch, every POST
# to /mcp spawns a new stdio child that is never reaped, leaking memory
# until the container OOMs.
#
# This script is version-pinned: it will FAIL if supergateway is upgraded
# past the tested version, forcing a human to re-verify the patch.
set -eu

EXPECTED_VERSION="3.4.3"
SG_DIR="/usr/local/lib/node_modules/supergateway"
TARGET="$SG_DIR/dist/gateways/stdioToStatelessStreamableHttp.js"

# ── 1. Version gate ──────────────────────────────────────────────────
ACTUAL_VERSION=$(node -e "process.stdout.write(require('$SG_DIR/package.json').version)")
if [ "$ACTUAL_VERSION" != "$EXPECTED_VERSION" ]; then
  echo "FATAL: supergateway version mismatch" >&2
  echo "  expected: $EXPECTED_VERSION" >&2
  echo "  actual:   $ACTUAL_VERSION" >&2
  echo "  The child-leak patch must be re-verified for this version." >&2
  exit 1
fi
echo "supergateway version $ACTUAL_VERSION — matches expected $EXPECTED_VERSION"

# ── 2. Verify the target file exists and contains the anchor ─────────
if [ ! -f "$TARGET" ]; then
  echo "FATAL: target file not found: $TARGET" >&2
  exit 1
fi

ANCHOR="await transport.handleRequest(req, res, req.body)"

if ! grep -qF "$ANCHOR" "$TARGET"; then
  echo "FATAL: anchor string not found in $TARGET" >&2
  echo "  expected: $ANCHOR" >&2
  exit 1
fi

if grep -qF "PATCH: kill child on response close" "$TARGET"; then
  echo "Patch already applied — skipping"
  exit 0
fi

# ── 3. Apply the patch (1 line added) ───────────────────────────────
sed -i "s|${ANCHOR}|/* PATCH: kill child on response close */ res.on('close', () => { if (!child.killed) child.kill(); });\n      ${ANCHOR}|" "$TARGET"

# ── 4. Verify ────────────────────────────────────────────────────────
if ! grep -qF "PATCH: kill child on response close" "$TARGET"; then
  echo "FATAL: patch verification failed" >&2
  exit 1
fi

echo "Patch applied: added res.on('close', () => child.kill()) before handleRequest"
