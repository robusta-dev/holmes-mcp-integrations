# gcloud MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:0.1.6

# Switch to root to install dependencies
USER root

# Install gcloud SDK dependencies.
# crc32c + crc32c-dev build the C extension for the google-crc32c wheel below
# (there is no musllinux wheel on PyPI, so it compiles from the sdist).
RUN apk add --no-cache \
    python3 \
    py3-pip \
    bash \
    curl \
    gcc \
    python3-dev \
    musl-dev \
    linux-headers \
    crc32c \
    crc32c-dev

# Pull the newest Alpine patches for everything the layers above dragged in.
# c-ares 1.34.6-r0 → 1.34.8-r0 (CVE-2026-33630), arriving as a libcurl dep, and
# python3 3.12.13-r0 → 3.12.14-r0 (CVE-2026-11940, CVE-2026-15308, CVE-2026-3644,
# CVE-2026-4786, CVE-2026-6100, CVE-2026-7210).
RUN apk upgrade --no-cache

# Install Google Cloud SDK without any configuration
ENV PATH="/google-cloud-sdk/bin:${PATH}"
RUN curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz && \
    tar xzf google-cloud-cli-linux-x86_64.tar.gz && \
    rm google-cloud-cli-linux-x86_64.tar.gz && \
    /google-cloud-sdk/install.sh --quiet --usage-reporting=false --path-update=false && \
    rm -rf /root/.config/gcloud && \
    rm -rf /root/.gsutil && \
    rm -rf /root/.docker

# Upgrade Python packages to fix CVEs (cryptography, jaraco-context, wheel, pyopenssl)
# pyopenssl>=26.0.0 fixes CVE-2026-27459
# cryptography>=50.0.0 fixes CVE-2026-69247
#
# The rm list strips vulnerable copies out of the SDK's bundled python. That python is
# a glibc build and cannot execute under musl at all — its ELF interpreter is missing,
# so gcloud falls through to /usr/bin/python3.12 (confirmed via `gcloud info`) — which
# makes every package under it unreachable dead code. Dropping setuptools there
# wholesale, rather than only its vendored wheel dist-info, also clears
# CVE-2026-59890 (setuptools 80.10.2 → 83.0.0) at the source.
RUN pip3 install --no-cache-dir --break-system-packages --upgrade \
    jaraco-context wheel "cryptography>=50.0.0" setuptools pip pyopenssl && \
    rm -rf /google-cloud-sdk/platform/bundledpythonunix/lib/python3.*/site-packages/cryptography* \
           /google-cloud-sdk/platform/bundledpythonunix/lib/python3.*/site-packages/setuptools \
           /google-cloud-sdk/platform/bundledpythonunix/lib/python3.*/site-packages/setuptools-*.dist-info \
           /google-cloud-sdk/platform/bundledpythonunix/lib/python3.*/site-packages/wheel-*.dist-info \
           /google-cloud-sdk/platform/bundledpythonunix/lib/python3.*/site-packages/OpenSSL \
           /google-cloud-sdk/platform/bundledpythonunix/lib/python3.*/site-packages/pyOpenSSL-*.dist-info

# Drop the SDK's bundled gcloud-crc32c Go binary.
#
# It is the only Go binary in this image and it carries 9 HIGH Go stdlib CVEs
# (CVE-2026-33818, -39821, -39822, -46600, -56853, -56858, -56859, -56860,
# -56862). Google builds it inside google3 from a single crc32c.go with
# `-trimpath` and no module path, so there is nothing public to rebuild from,
# and the newest rapid-channel SDK (581.0.0) still ships it built with go1.26.5
# — which clears only CVE-2026-39822. The other eight need go >= 1.26.6.
#
# gcloud only uses this binary as a fast CRC32C accelerator. Its own fallback
# order (googlecloudsdk/command_lib/storage/fast_crc32c_util.py) prefers the
# C-backed google-crc32c Python module whenever that is importable, so install
# that instead and the accelerator is a like-for-like swap. Two env vars make
# the swap take effect:
#   - CLOUDSDK_PYTHON_SITEPACKAGES=1 — gcloud otherwise runs python with -S and
#     would not see the wheel we just installed.
#   - CLOUDSDK_STORAGE_USE_GCLOUD_CRC32C=false — stops gcloud probing for the
#     binary we removed.
# Verified: `gcloud storage hash` returns the same crc32c as before the swap.
# Revert (drop this block, keep the binary) once Google ships gcloud-crc32c
# built with go >= 1.26.6.
ENV CLOUDSDK_PYTHON_SITEPACKAGES=1
ENV CLOUDSDK_STORAGE_USE_GCLOUD_CRC32C=false
RUN pip3 install --no-cache-dir --break-system-packages google-crc32c && \
    python3 -c "import google_crc32c, sys; sys.exit(0 if google_crc32c.implementation in ('c', 'cffi') else 1)" && \
    rm -f /google-cloud-sdk/bin/gcloud-crc32c

# Drop pip once the installs above are done. pip is build-only here — gcloud, gsutil
# and the MCP server never invoke it at runtime — and its vendored-dependency SBOM
# (pip/_vendor/bom.cdx.json) is the sole source of two HIGH findings that no upstream
# pip release clears yet: msgpack 1.1.2 (GHSA-6v7p-g79w-8964, fixed in 1.2.1) and
# setuptools 70.3.0 (CVE-2025-47273, fixed in 78.1.1; only the BOM entry is stale
# here — the real setuptools in site-packages is current). Removing the whole
# build-only package manager is the honest fix. `apk del` alone is not enough: the
# `pip install --upgrade pip` above replaced the apk-owned files with unmanaged ones.
RUN apk del py3-pip && \
    rm -rf /usr/lib/python3.12/site-packages/pip \
           /usr/lib/python3.12/site-packages/pip-*.dist-info \
           /usr/bin/pip /usr/bin/pip3 /usr/bin/pip3.12

# Smoke-test that the gcloud MCP package resolves at build time.
#
# This used to be described as "pre-install to cache it", but it never cached
# anything usable: npx writes to $HOME/.npm, HOME is /root here, /root is mode
# 0700, and the container runs as uid 1000 (node). The runtime user could never
# read the cache, so it only shipped a stale, unreachable copy of the dependency
# tree — which is where the brace-expansion/fast-uri/ip-address findings under
# /root/.npm/_npx came from. Delete it; runtime behaviour is unchanged because
# `npx -y` already re-resolved the package on every container start.
RUN npx -y @google-cloud/gcloud-mcp --version || true
RUN rm -rf /root/.npm

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch back to node user for security
USER node

# Expose the SSE API port
EXPOSE 8000

# Use entrypoint to enable ADC
ENTRYPOINT ["/entrypoint.sh"]

# Run Supergateway with gcloud-mcp as the stdio process
CMD ["node", "/usr/local/bin/supergateway", "--port", "8000", "--stdio", "npx -y @google-cloud/gcloud-mcp"]
