# Use Python base image since MariaDB MCP is a Python application
FROM python:3.11-slim

# Install system dependencies for MariaDB client libraries and git
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install UV (fast Python package installer)
RUN pip install uv

# Create app directory
WORKDIR /app

# Clone the MariaDB MCP repository
RUN git clone https://github.com/MariaDB/mcp.git .

# Create lockfile and install Python dependencies using UV
RUN uv lock && uv sync

# Create a non-root user
RUN useradd -m -s /bin/bash mcp

# Change ownership of the app directory to the mcp user
RUN chown -R mcp:mcp /app

USER mcp

# Expose port for HTTP
EXPOSE 8000

# Run the server in HTTP mode using uv run
# The server will use environment variables for database connection
CMD ["uv", "run", "server.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8000", "--path", "/mcp/messages"]