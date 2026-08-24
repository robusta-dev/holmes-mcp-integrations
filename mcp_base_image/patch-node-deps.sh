#!/bin/sh
# Replace vulnerable copies of npm packages inside an already-installed tree.
#
# Child images install their MCP server with `npm install -g` or prewarm it into
# the npx cache, so the vulnerable packages are transitive deps we do not own and
# cannot bump via a normal `npm install`. This walks the tree and swaps every
# copy of each named package for a fixed release, including nested copies that
# npm could not dedupe.
#
# usage: patch-node-deps.sh <root-dir> <pkg@range> [pkg@range ...]
#
# Exits non-zero if a named package is not found under <root-dir>. That is
# deliberate: it makes the build fail loudly when an upstream bump restructures
# the tree, instead of silently shipping an unpatched image.
set -eu

ROOT="$1"
shift
[ -d "$ROOT" ] || { echo "patch-node-deps: no such directory: $ROOT" >&2; exit 1; }

TMP=$(mktemp -d)
cd "$TMP"

for spec in "$@"; do
    # Strip the trailing @<range>, keeping any leading @scope/ intact.
    name=$(echo "$spec" | sed 's/@[^@]*$//')

    npm pack "$spec" >/dev/null
    tgz=$(ls ./*.tgz)

    found=0
    for dir in $(find "$ROOT" -type d -path "*/node_modules/$name"); do
        # Wipe first: extracting over the old release leaves its stale files
        # behind, which can shadow the new one across a major bump.
        rm -rf "$dir"/* "$dir"/.[!.]* 2>/dev/null || true
        tar xzf "$tgz" --strip-components=1 -C "$dir"
        found=$((found + 1))
    done
    rm -f "$tgz"

    if [ "$found" -eq 0 ]; then
        echo "patch-node-deps: ERROR: $name not found under $ROOT" >&2
        exit 1
    fi
    echo "patch-node-deps: $spec -> replaced $found copy/copies under $ROOT"
done

cd /
rm -rf "$TMP"
