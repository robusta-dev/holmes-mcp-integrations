# Azure API MCP Server
# Builds from source since Microsoft doesn't provide pre-built images

FROM golang:1.26.3-alpine AS builder

# Install git for cloning the repository
RUN apk add --no-cache git

# Clone the Azure API MCP repository
RUN git clone https://github.com/Azure/azure-api-mcp.git /azure-api-mcp

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
FROM alpine:latest

# Fix CVE-2026-22184: upgrade zlib only
RUN apk add --no-cache --upgrade zlib

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