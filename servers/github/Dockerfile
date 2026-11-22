# Official GitHub MCP server in native HTTP mode.
# Distroless base — supergateway is no longer needed (the binary now speaks
# streamable HTTP directly via the `http` subcommand, see github/github-mcp-server v1.4.0).
# Rebuilt from source: upstream v2.1.0/v2.2.0 use Go 1.27.3 and x/net 0.61.0
# (CVE-2026-97054, CVE-2026-78661, CVE-2026-97045) on base-debian12 with openssl 3.0.20
# (CVE-2026-75803 and others). Revert to the upstream image once it ships fixed builds.
FROM --platform=$BUILDPLATFORM node:26.13.1-alpine AS ui-build
RUN apk add --no-cache git && \
    git clone --depth 1 --branch v2.1.0 https://github.com/github/github-mcp-server.git /src
WORKDIR /src/ui
RUN npm ci && mkdir -p ../pkg/github/ui_dist && npm run build

FROM --platform=$BUILDPLATFORM golang:1.27.4-alpine AS builder
ARG TARGETOS=linux
ARG TARGETARCH
COPY --from=ui-build /src /src
WORKDIR /src
RUN go get golang.org/x/net@v0.62.0 && go mod tidy && \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -ldflags="-s -w -X main.version=v2.1.0" \
    -o /bin/github-mcp-server ./cmd/github-mcp-server

FROM gcr.io/distroless/static-debian13@sha256:1a64f46364620465ef9e9a8f5413757dfdf22695e050bbfd7303eeaf17a59383
COPY --from=builder /bin/github-mcp-server /server/github-mcp-server
WORKDIR /server
ENTRYPOINT ["/server/github-mcp-server"]

EXPOSE 8000
CMD ["http", "--port", "8000"]
