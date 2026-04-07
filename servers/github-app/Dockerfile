FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

USER root

RUN apk add --no-cache python3 py3-pip
RUN pip3 install --no-cache-dir --break-system-packages PyJWT cryptography requests && \
    pip3 install --no-cache-dir --break-system-packages --upgrade \
    jaraco-context wheel cryptography setuptools pip

# Copy GitHub MCP server binary from official image
COPY --from=ghcr.io/github/github-mcp-server:latest /server/github-mcp-server /usr/local/bin/

COPY github_app_auth.py /app/
COPY wrapper.py /app/

EXPOSE 8000
CMD ["--port", "8000", "--stdio", "python3 /app/wrapper.py"]
