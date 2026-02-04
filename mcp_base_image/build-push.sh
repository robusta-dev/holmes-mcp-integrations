#!/bin/bash
docker buildx build --pull --no-cache --build-arg BUILDKIT_INLINE_CACHE=1 --platform linux/amd64,linux/arm64 \
    --tag us-central1-docker.pkg.dev/genuine-flight-317411/devel/supergateway_base:0.1.0 \
    --tag us-central1-docker.pkg.dev/genuine-flight-317411/devel/supergateway_base:latest \
    --push .