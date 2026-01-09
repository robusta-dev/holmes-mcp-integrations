# Kubernetes Remediation MCP Server
# Runs kubectl commands securely via MCP protocol

FROM python:3.11-slim

# Install kubectl
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    && curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl \
    && apt-get remove -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Verify kubectl installation
RUN kubectl version --client

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY kubernetes_remediation.py .

# Create non-root user
RUN useradd -m -s /bin/bash mcp && \
    chown -R mcp:mcp /app

USER mcp

# Expose port for HTTP
EXPOSE 8000

# Run the MCP server in HTTP mode
CMD ["python", "kubernetes_remediation.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
