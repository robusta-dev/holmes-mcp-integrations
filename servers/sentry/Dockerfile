# Sentry MCP Server with Supergateway
# Wraps the Sentry MCP stdio server and exposes it as SSE HTTP API

FROM supercorp/supergateway:latest

# Switch to root to install dependencies
USER root

# Update npm to latest version for security
RUN npm install -g npm@latest

# Install Sentry MCP server globally
RUN npm install -g @sentry/mcp-server

USER node
EXPOSE 8000

# The access token is passed via environment variable
# Supergateway will execute the sentry-mcp command with the token
CMD ["--port", "8000", "--stdio", "sh -c 'sentry-mcp --access-token=$SENTRY_AUTH_TOKEN'"]
