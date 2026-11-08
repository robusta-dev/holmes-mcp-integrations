# Azure API MCP Server
# Builds from source since Microsoft doesn't provide pre-built images

# Pin an explicit patched Go: the floating 1.26-alpine tag baked an old stdlib into the
# binary at build time. 1.26.10 clears the <1.26.9 stdlib CVEs (CVE-2026-56857,
# CVE-2026-78663 and others). Bump this when a newer Go security release lands.
FROM golang:1.26.10-alpine AS builder

# Install git for cloning the repository
RUN apk add --no-cache git

# Pinned: v0.0.8+ is stdio-only, the chart needs streamable-http
RUN git clone --branch v0.0.7 --depth 1 https://github.com/Azure/azure-api-mcp.git /azure-api-mcp

# Build the binary
WORKDIR /azure-api-mcp

# Set GOTOOLCHAIN to auto to handle version requirements
ENV GOTOOLCHAIN=auto
# GOGC=off prevents GC crashes when building under QEMU emulation
ENV GOGC=off

# Force buger/jsonparser>=1.1.2 to fix CVE-2026-32285 (transitive dep)
RUN go get github.com/buger/jsonparser@v1.1.2 && go mod tidy

RUN go build -o bin/azure-api-mcp ./cmd/server

# Final stage - minimal image
FROM alpine:3.24

# Upgrade OS packages to pick up openssl, python3 and zlib CVE fixes
RUN apk upgrade --no-cache

# Install ca-certificates for HTTPS connections and Azure CLI for authentication
RUN apk add --no-cache ca-certificates python3 py3-pip && \
    pip3 install --break-system-packages azure-cli && \
    pip3 install --no-cache-dir --break-system-packages --upgrade \
    jaraco-context wheel cryptography setuptools pip && \
    ln -sf /usr/bin/python3 /usr/bin/python

# Copy the built binary from builder stage
COPY --from=builder /azure-api-mcp/bin/azure-api-mcp /usr/local/bin/

# Expose the default port
EXPOSE 8000

# Set the entrypoint
ENTRYPOINT ["azure-api-mcp"]

# Default command with streamable-http transport and read-only mode
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000", "--readonly"]