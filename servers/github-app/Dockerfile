FROM python:3.13-alpine

RUN apk upgrade --no-cache && \
    pip3 install --no-cache-dir PyGithub==2.9.1 starlette==1.6.0 uvicorn==0.52.1 httpx==0.28.1 certifi==2026.10.19 && \
    pip3 install --no-cache-dir --upgrade setuptools pip wheel

# Copy GitHub MCP server binary from official image
COPY --from=ghcr.io/github/github-mcp-server:v2.1.0 /server/github-mcp-server /usr/local/bin/

COPY github_app_auth.py wrapper.py /app/

EXPOSE 8000
ENTRYPOINT ["python3", "/app/wrapper.py"]
