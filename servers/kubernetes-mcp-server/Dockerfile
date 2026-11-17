# Patches CVEs in robusta-dev/kubernetes-mcp-server v0.0.61-oauth:
#   - rebuilds the binary with a patched Go and bumped Go modules
#   - runs on ubi-micro, which drops the UBI minimal packages with unfixed CVEs (krb5, sqlite, json-glib)
FROM golang:1.26.10-alpine AS builder

ARG TARGETOS=linux
ARG TARGETARCH

RUN apk add --no-cache git make

RUN git clone --branch v0.0.61-oauth https://github.com/robusta-dev/kubernetes-mcp-server.git /src

WORKDIR /src

RUN go get github.com/containerd/containerd@v1.7.33 \
      github.com/go-jose/go-jose/v4@v4.1.6 \
      github.com/klauspost/compress@v1.18.7 \
      golang.org/x/crypto@v0.58.0 \
      golang.org/x/net@v0.62.0 \
      golang.org/x/text@v0.43.0 \
      google.golang.org/grpc@v1.83.2 \
      oras.land/oras-go/v2@v2.6.2 \
    && go mod tidy

RUN make build-multiarch TARGETOS=${TARGETOS} TARGETARCH=${TARGETARCH}

# The binary is static (CGO_ENABLED=0); the chart's init container only needs cp
FROM registry.access.redhat.com/ubi9/ubi-micro:9.9

WORKDIR /app
COPY --from=builder /src/kubernetes-mcp-server /app/kubernetes-mcp-server
USER 65532:65532
ENTRYPOINT ["/app/kubernetes-mcp-server"]
CMD ["--port", "8080"]
EXPOSE 8080
