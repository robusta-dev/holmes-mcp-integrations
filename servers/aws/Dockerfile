# AWS API MCP Server with Supergateway
# Uses Supergateway base image and adds Python

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

# Switch to root to install Python
USER root

RUN apk add --no-cache python3 py3-pip

# Install AWS API MCP server.
# Drop ensurepip's bundled pip wheel (otherwise scanners flag CVE-2025-8869 on the unused bootstrap wheel).
RUN pip3 install --no-cache-dir --break-system-packages awslabs.aws-api-mcp-server && \
    pip3 install --no-cache-dir --break-system-packages --upgrade \
    jaraco-context wheel cryptography setuptools pip && \
    rm -f /usr/lib/python3.*/ensurepip/_bundled/pip-*.whl

USER node
EXPOSE 8000

CMD ["--port", "8000", "--stdio", "python3 -m awslabs.aws_api_mcp_server.server"]
