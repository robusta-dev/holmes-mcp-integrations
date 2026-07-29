# Official GitHub MCP server in native HTTP mode.
# Distroless base — supergateway is no longer needed (the binary now speaks
# streamable HTTP directly via the `http` subcommand, see github/github-mcp-server v1.4.0).
FROM ghcr.io/github/github-mcp-server:v1.4.0

# Upstream image is distroless with no USER directive (defaults to root).
# The binary is static and writes nothing, so drop to distroless' nonroot user.
USER 65532:65532

EXPOSE 8000
CMD ["http", "--port", "8000"]
