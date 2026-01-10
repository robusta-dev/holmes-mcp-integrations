FROM supercorp/supergateway:latest

# Copy GitHub MCP server binary from official image
COPY --from=ghcr.io/github/github-mcp-server:latest /server/github-mcp-server /usr/local/bin/

EXPOSE 8000
CMD ["--port", "8000", "--stdio", "github-mcp-server stdio"]