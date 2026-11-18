# Kubernetes Remediation MCP Server
# Runs kubectl commands securely via MCP protocol

FROM python:3.11-slim

# Install kubectl (supports both amd64 and arm64)
RUN apt-get update && apt-get upgrade -y && apt-get install -y \
    curl \
    ca-certificates \
    && ARCH=$(dpkg --print-architecture) \
    && curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl \
    && apt-get remove -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Trixie has no fix yet for these CVEs (util-linux, acl, ncurses, sqlite3, systemd, perl);
# take only these packages from forky, which has them, plus the glibc they need
RUN FORKY_PKGS="libc6 libc-bin libc-gconv-modules-extra bsdutils libblkid1 liblastlog2-2 libmount1 libsmartcols1 libuuid1 login mount util-linux libacl1 libncursesw6 libtinfo6 ncurses-base ncurses-bin libsqlite3-0 libsystemd0 libudev1 perl-base" && \
    echo 'deb http://deb.debian.org/debian forky main' > /etc/apt/sources.list.d/forky.list && \
    printf "Package: $FORKY_PKGS\nPin: release n=forky\nPin-Priority: 990\n\nPackage: *\nPin: release n=forky\nPin-Priority: -1\n" > /etc/apt/preferences.d/99-forky && \
    apt-get update -y && apt-get install -y --no-install-recommends $FORKY_PKGS && \
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
    pip uninstall -y pip

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
