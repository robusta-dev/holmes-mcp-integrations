# Minimal MariaDB MCP Server
# Lightweight version without ML/embedding dependencies

FROM python:3.11-slim

# Install only essential system dependencies.
# Also explicitly upgrade libc6 / libc-bin so the image picks up the patched
# glibc 2.41-12+deb13u3 (CVE-2026-4046 — iconv IBM1390/IBM1399 crash).
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && apt-get install -y --only-upgrade libc6 libc-bin \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
# Upgrade setuptools/pip too: their vendored copies of jaraco-context and
# wheel are flagged as CVE-2026-23949 / CVE-2026-24049.
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade pip setuptools

# Copy server code
COPY server.py .

# Create non-root user
RUN useradd -m -s /bin/bash mcp && \
    chown -R mcp:mcp /app

USER mcp

# Expose port for HTTP
EXPOSE 8000

# Run the MCP server in HTTP mode
CMD ["python", "server.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]