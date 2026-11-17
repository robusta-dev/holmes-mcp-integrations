# GitLab MCP Server with Supergateway
# Wraps the @zereight/mcp-gitlab stdio server and exposes it as SSE HTTP API
# Pre-installs the package at build time instead of running npx at startup

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:0.1.7

USER root

# Pinned to the version the Holmes chart runs (mcpAddons.gitlabMcp.mcpGitlabVersion)
RUN npm install -g @zereight/mcp-gitlab@2.1.46

USER node
EXPOSE 8000

# Config (GITLAB_API_URL, GITLAB_PERSONAL_ACCESS_TOKEN, GITLAB_READ_ONLY_MODE, ...) comes from env
CMD ["--port", "8000", "--stdio", "mcp-gitlab"]
