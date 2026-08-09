FROM python:3.13-alpine

RUN pip3 install --no-cache-dir PyJWT cryptography requests && \
    pip3 install --no-cache-dir --upgrade setuptools pip wheel

# Copy GitHub MCP server binary from the official image.
# Pinned version — when bumping, re-check OWNER_ARG_KEYS in owner_extraction.py
# against the new release's tool argument names.
COPY --from=ghcr.io/github/github-mcp-server:v1.4.0 /server/github-mcp-server /usr/local/bin/

COPY github_app_auth.py owner_extraction.py proxy.py wrapper.py /app/

WORKDIR /app

EXPOSE 8000
ENTRYPOINT ["python3", "/app/wrapper.py"]
