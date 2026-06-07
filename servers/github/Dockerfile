# Build github-mcp-server from source against Go 1.25.11 to fix CVE-2026-42504
# (upstream ghcr.io/github/github-mcp-server is still on Go 1.25.10).
ARG GITHUB_MCP_SERVER_VERSION=v1.1.2

FROM node:26-alpine AS ui-build
ARG GITHUB_MCP_SERVER_VERSION
WORKDIR /src
RUN apk add --no-cache git && \
    git clone --depth 1 --branch ${GITHUB_MCP_SERVER_VERSION} https://github.com/github/github-mcp-server.git .
RUN cd ui && npm ci && npm run build

FROM golang:1.25.11-alpine AS build
ARG GITHUB_MCP_SERVER_VERSION
WORKDIR /build
RUN apk add --no-cache git && \
    git clone --depth 1 --branch ${GITHUB_MCP_SERVER_VERSION} https://github.com/github/github-mcp-server.git .
COPY --from=ui-build /src/pkg/github/ui_dist/ ./pkg/github/ui_dist/
ENV CGO_ENABLED=0
RUN go build \
    -ldflags="-s -w -X main.version=${GITHUB_MCP_SERVER_VERSION} -X main.commit=$(git rev-parse HEAD) -X main.date=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    -o /bin/github-mcp-server ./cmd/github-mcp-server

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest
COPY --from=build /bin/github-mcp-server /usr/local/bin/

EXPOSE 8000
CMD ["--port", "8000", "--stdio", "github-mcp-server stdio"]
