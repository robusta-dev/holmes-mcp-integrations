# AWS API MCP Server — native MCP Streamable HTTP (no supergateway).
#
# awslabs.aws-api-mcp-server speaks MCP Streamable HTTP natively, so we run it
# directly instead of bridging stdio through supergateway. This removes the
# Node/supergateway CVE surface and the supergateway SSE single-session crash
# ("Already connected to a transport"), and serves multiple concurrent Holmes
# sessions from one process. Holmes connects to http://<service>:8000/mcp.
FROM python:3.13-alpine

# Patch OS-level CVEs available in Alpine (openssl, etc.). Any python/sqlite
# findings are upstream-pending in CPython/Alpine and clear once fixes ship.
RUN apk upgrade --no-cache

# Install the AWS API MCP server, then upgrade transitive deps flagged by
# scanners: idna (GHSA-65pc-fj4g-8rjx) and starlette (GHSA-86qp-5c8j-p5mr);
# refresh pip/setuptools/wheel/cryptography too.
RUN pip install --no-cache-dir awslabs.aws-api-mcp-server && \
    pip install --no-cache-dir --upgrade \
    pip setuptools wheel cryptography idna starlette

# Native MCP Streamable HTTP transport on :8000/mcp.
# ALLOWED_HOSTS/ORIGINS=* preserves the permissive in-cluster posture of the
# previous supergateway (CORS-disabled) setup, so Holmes can reach the server
# via its in-cluster Service name. AUTH_TYPE=no-auth is required for the HTTP
# transport and matches that posture (access is gated by the cluster network
# and IAM, as before).
ENV AWS_API_MCP_TRANSPORT=streamable-http \
    AWS_API_MCP_HOST=0.0.0.0 \
    AWS_API_MCP_PORT=8000 \
    AWS_API_MCP_ALLOWED_HOSTS=* \
    AWS_API_MCP_ALLOWED_ORIGINS=* \
    AUTH_TYPE=no-auth

# Run as a non-root user (least privilege; the server's workdir lives under /tmp).
RUN adduser -D -u 1000 mcp
USER mcp

EXPOSE 8000

CMD ["python3", "-m", "awslabs.aws_api_mcp_server.server"]
