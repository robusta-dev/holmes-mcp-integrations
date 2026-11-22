# Kubernetes Remediation MCP Server
# Runs kubectl commands securely via MCP protocol

# kubectl rebuilt from source: the release binary uses Go 1.26.10 with x/net 0.57.0,
# x/text 0.40.0 and otel/trace 1.44.0 (CVE-2026-78661, CVE-2026-78669, CVE-2026-56851,
# CVE-2026-97054, CVE-2026-128085 and others)
FROM --platform=$BUILDPLATFORM golang:1.26.11-alpine AS kubectl-builder
ARG TARGETOS=linux
ARG TARGETARCH
ARG KUBECTL_VERSION=v1.37.2
RUN apk add --no-cache git && \
    git clone --depth 1 --branch ${KUBECTL_VERSION} https://github.com/kubernetes/kubernetes.git /src
WORKDIR /src
RUN GOWORK=off GOFLAGS=-mod=mod go get golang.org/x/net@v0.62.0 golang.org/x/text@v0.43.0 go.opentelemetry.io/otel/trace@v1.47.0 && \
    GOWORK=off GOFLAGS=-mod=mod CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath \
    -ldflags "-s -w -X k8s.io/client-go/pkg/version.gitVersion=${KUBECTL_VERSION} -X k8s.io/component-base/version.gitVersion=${KUBECTL_VERSION}" \
    -o /kubectl ./cmd/kubectl

FROM python:3.11-slim

RUN apt-get update && apt-get upgrade -y && apt-get install -y ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=kubectl-builder /kubectl /usr/local/bin/kubectl

# Trixie has no fix yet for these CVEs (util-linux, acl, attr, ncurses, sqlite3, systemd, perl, gcc);
# take only these packages from forky, which has them, plus the glibc they need.
# gcc-14-base is docs only and unused once libgcc-s1/libstdc++6 come from gcc-16.
RUN FORKY_PKGS="libc6 libc-bin libc-gconv-modules-extra bsdutils libblkid1 liblastlog2-2 libmount1 libsmartcols1 libuuid1 login mount util-linux libacl1 libncursesw6 libtinfo6 ncurses-base ncurses-bin libsqlite3-0 libsystemd0 libudev1 perl-base libattr1 libgcc-s1 libstdc++6 gcc-16-base" && \
    echo 'deb http://deb.debian.org/debian forky main' > /etc/apt/sources.list.d/forky.list && \
    printf "Package: $FORKY_PKGS\nPin: release n=forky\nPin-Priority: 990\n\nPackage: *\nPin: release n=forky\nPin-Priority: -1\n" > /etc/apt/preferences.d/99-forky && \
    apt-get update -y && apt-get install -y --no-install-recommends $FORKY_PKGS && \
    apt-get purge -y gcc-14-base && \
    rm -f /etc/apt/sources.list.d/forky.list /etc/apt/preferences.d/99-forky && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Verify kubectl installation
RUN kubectl version --client

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Upgrade pip first, then install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y pip && \
    rm -rf /usr/local/lib/python3.11/ensurepip

# Copy server code
COPY kubernetes_remediation.py .

# Create non-root user with explicit UID (required for runAsNonRoot)
RUN useradd -m -s /bin/bash -u 1000 mcp && \
    chown -R mcp:mcp /app

USER 1000

# Expose port for HTTP
EXPOSE 8000

# Run the MCP server in HTTP mode
CMD ["python", "kubernetes_remediation.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
