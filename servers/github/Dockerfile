# Official GitHub MCP server in native HTTP mode.
# Distroless base — supergateway is no longer needed (the binary now speaks
# streamable HTTP directly via the `http` subcommand, see github/github-mcp-server v1.4.0).
FROM ghcr.io/github/github-mcp-server:v1.4.0

EXPOSE 8000
CMD ["http", "--port", "8000"]
