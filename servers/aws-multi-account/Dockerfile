# AWS multi-account API MCP Server — native MCP Streamable HTTP (no supergateway).
#
# wrapper.py sets up per-account AWS profiles (via IRSA) and refreshes their
# credentials in the background, then launches the AWS API MCP server. The
# server speaks MCP Streamable HTTP natively (transport configured via env
# below), so we no longer bridge stdio through supergateway. This removes the
# Node/supergateway CVE surface and the supergateway SSE single-session crash
# ("Already connected to a transport"). Holmes connects to
# http://<service>:8000/mcp.
FROM python:3.13-alpine

# Patch OS-level CVEs available in Alpine (openssl, etc.). Any python/sqlite
# findings are upstream-pending in CPython/Alpine and clear once fixes ship.
RUN apk upgrade --no-cache

# Install the AWS API MCP server (+ boto3/pyyaml for wrapper.py), then upgrade
# transitive deps flagged by scanners: idna (GHSA-65pc-fj4g-8rjx) and starlette
# (GHSA-86qp-5c8j-p5mr); refresh pip/setuptools/wheel/cryptography too.
RUN pip install --no-cache-dir \
    awslabs.aws-api-mcp-server \
    boto3 \
    pyyaml && \
    pip install --no-cache-dir --upgrade \
    pip setuptools wheel cryptography idna starlette

COPY aws_auth.py /app/
COPY wrapper.py /app/

# wrapper.py launches AWS_MCP_SERVER_CMD; the server reads its transport config
# from the AWS_API_MCP_* env vars and serves Streamable HTTP on :8000/mcp.
# ALLOWED_HOSTS/ORIGINS=* preserves the permissive in-cluster posture of the
# previous supergateway (CORS-disabled) setup; AUTH_TYPE=no-auth is required for
# the HTTP transport and matches that posture (access is gated by the cluster
# network and IAM, as before).
ENV AWS_MCP_SERVER_CMD="python3 -m awslabs.aws_api_mcp_server.server" \
    AWS_API_MCP_TRANSPORT=streamable-http \
    AWS_API_MCP_HOST=0.0.0.0 \
    AWS_API_MCP_PORT=8000 \
    AWS_API_MCP_ALLOWED_HOSTS=* \
    AWS_API_MCP_ALLOWED_ORIGINS=* \
    AUTH_TYPE=no-auth

# Runs as root: wrapper.py writes the assumed-role credentials to /root/.aws.
EXPOSE 8000

CMD ["python3", "/app/wrapper.py"]
