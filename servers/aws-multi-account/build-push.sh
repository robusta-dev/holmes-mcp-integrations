#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="us-central1-docker.pkg.dev/genuine-flight-317411/devel"

image=$(grep '^image:' "$SCRIPT_DIR/auto-build-config.yaml" | sed 's/image: *//')
version=$(grep '^version:' "$SCRIPT_DIR/auto-build-config.yaml" | sed 's/version: *//' | tr -d '"')

docker buildx build --pull --no-cache --build-arg BUILDKIT_INLINE_CACHE=1 --platform linux/arm64,linux/amd64 --tag "$REGISTRY/$image:$version" --push .
