FROM python:3.13-alpine

# PyGithub handles GitHub App credentials (JWT signing, installation
# discovery, token minting); requests is used directly by the proxy.
RUN pip3 install --no-cache-dir PyGithub==2.9.1 requests && \
    pip3 install --no-cache-dir --upgrade setuptools pip wheel

# Copy GitHub MCP server binary from the official image.
# Pinned version — when bumping, re-check OWNER_ARG_KEYS in github_utils.py
# against the new release's tool argument names.
COPY --from=ghcr.io/github/github-mcp-server:v1.4.0 /server/github-mcp-server /usr/local/bin/

COPY github_utils.py proxy.py wrapper.py /app/

WORKDIR /app

EXPOSE 8000
ENTRYPOINT ["python3", "/app/wrapper.py"]
