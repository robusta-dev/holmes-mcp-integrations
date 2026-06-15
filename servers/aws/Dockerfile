# AWS API MCP Server with Supergateway
# Uses Supergateway base image and adds Python

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

# Switch to root to install Python
USER root

# apk upgrade pulls the latest patched Alpine packages into this layer too
# (e.g. openssl libssl3/libcrypto3 3.5.7-r0: CVE-2026-34182 et al.)
RUN apk add --no-cache python3 py3-pip && apk upgrade --no-cache

# Install AWS API MCP server.
# idna and starlette upgraded to fix GHSA-65pc-fj4g-8rjx / GHSA-86qp-5c8j-p5mr.
RUN pip3 install --no-cache-dir --break-system-packages awslabs.aws-api-mcp-server && \
    pip3 install --no-cache-dir --break-system-packages --upgrade \
    jaraco-context wheel cryptography setuptools pip idna starlette

USER node
EXPOSE 8000

CMD ["--port", "8000", "--stdio", "python3 -m awslabs.aws_api_mcp_server.server"]
