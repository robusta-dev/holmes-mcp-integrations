# Minimal MariaDB MCP Server
# Lightweight version without ML/embedding dependencies

FROM python:3.11-slim

# Install only essential system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies.
# Upgrade setuptools/pip too: their vendored copies of jaraco-context and
# wheel are flagged as CVE-2026-23949 / CVE-2026-24049.
# Drop ensurepip's bundled pip wheel (otherwise scanners flag CVE-2025-8869 on the unused bootstrap wheel).
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade pip setuptools && \
    rm -f /usr/local/lib/python3.*/ensurepip/_bundled/pip-*.whl

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