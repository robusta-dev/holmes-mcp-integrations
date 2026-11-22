# github-mcp-server rebuilt from source with Go 1.27.4 and x/net 0.62.0
# (CVE-2026-97054, CVE-2026-78661, CVE-2026-97045); same build as servers/github
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

FROM python:3.13-alpine

RUN apk upgrade --no-cache && \
    pip3 install --no-cache-dir PyGithub==2.9.1 starlette==1.6.0 uvicorn==0.52.1 httpx==0.28.1 certifi==2026.10.19 && \
    pip3 install --no-cache-dir --upgrade setuptools pip wheel && \
    pip3 uninstall -y pip && rm -rf /usr/local/lib/python3.13/ensurepip

COPY --from=builder /bin/github-mcp-server /usr/local/bin/

COPY github_app_auth.py wrapper.py /app/

EXPOSE 8000
ENTRYPOINT ["python3", "/app/wrapper.py"]
