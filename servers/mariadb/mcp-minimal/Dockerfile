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

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

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