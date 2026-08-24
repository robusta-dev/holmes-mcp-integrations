# gcloud MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:0.1.6

# Switch to root to install dependencies
USER root

# Install gcloud SDK dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    bash \
    curl \
    gcc \
    python3-dev \
    musl-dev \
    linux-headers

# Refresh anything the base layer left stale, notably c-ares (CVE-2026-33630)
# and curl/libcurl (CVE-2026-5545). Runs after apk add so the packages installed
# just above are covered too.
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
RUN pip3 install --no-cache-dir --break-system-packages --upgrade \
    jaraco-context wheel cryptography setuptools pip pyopenssl

# Drop the SDK's bundled CPython entirely, rather than deleting flagged packages
# out of it one by one. It is a glibc build and cannot execute on this musl base
# at all -- `gcloud info` reports /usr/bin/python3.12 as the interpreter actually
# in use -- so nothing here runs it, yet its site-packages keep getting flagged
# (setuptools 80.10.2 / CVE-2026-59890, and before that cryptography, wheel and
# pyOpenSSL). Removing the tree closes the whole class and saves ~90 MB.
#
# gcloud-crc32c is a Google-prebuilt Go binary we cannot rebuild. It is linked
# against a Go stdlib with 8 unfixed HIGH CVEs (CVE-2026-33818, CVE-2026-39821,
# CVE-2026-46600, CVE-2026-56853, CVE-2026-56858, CVE-2026-56859,
# CVE-2026-56860, CVE-2026-56862). It is only an optional CRC32C accelerator:
# googlecloudsdk/command_lib/storage/fast_crc32c_util.py falls back to a slower
# checksum path when the binary is absent.
RUN rm -rf /google-cloud-sdk/platform/bundledpythonunix \
           /google-cloud-sdk/bin/gcloud-crc32c

# Install the MCP server at build time instead of resolving it with `npx -y` on
# every container start. `npx -y` re-resolves the package against the registry
# when the container boots, which reinstalls the dependency tree and silently
# undoes the CVE patches applied below -- so the tree that ships in the image has
# to be the tree that runs. It also means startup no longer depends on
# registry.npmjs.org being reachable. Matches how servers/sentry does it.
RUN npm install -g @google-cloud/gcloud-mcp

# Patch vulnerable transitive deps in the installed MCP server tree. These come
# from @modelcontextprotocol/sdk and ajv, which the MCP package pins:
# - fast-uri 3.1.3 → 3.1.5 (CVE-2026-16221, CVE-2026-18446)
# - ip-address 10.2.0 → 10.3.1 (CVE-2026-69192, CVE-2026-54272, CVE-2026-69198)
# - hono 4.12.27 → 4.12.34 (CVE-2026-69207, CVE-2026-71848, CVE-2026-71850)
# - @hono/node-server 1.19.14 → 1.19.15 (GHSA-frvp-7c67-39w9)
RUN /usr/local/bin/patch-node-deps.sh /usr/local/lib/node_modules/@google-cloud/gcloud-mcp \
    fast-uri@^3.1.5 ip-address@^10.3.1 hono@^4.12.34 @hono/node-server@^1.19.15

# pip is only needed for the build-time upgrades above; nothing in this image
# uses it at runtime. Removing it also removes pip's vendored dependency
# manifest (pip/_vendor/vendor.txt), which pins msgpack 1.1.2
# (GHSA-6v7p-g79w-8964) and setuptools 70.3.0 (CVE-2025-47273, CVE-2026-59890).
# Upstream pip has not refreshed either, and they cannot be upgraded in place.
RUN apk del py3-pip && \
    rm -rf /usr/lib/python3.*/site-packages/pip \
           /usr/lib/python3.*/site-packages/pip-*.dist-info

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
CMD ["node", "/usr/local/bin/supergateway", "--port", "8000", "--stdio", "gcloud-mcp"]
