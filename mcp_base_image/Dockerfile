# MCP Base Image
# Base image for MCP servers using Supergateway

FROM supercorp/supergateway:latest

USER root

# Update npm to latest version for CVE handling
RUN npm install -g npm@latest

# Update vulnerable dependencies in supergateway:
# - @modelcontextprotocol/sdk@1.25.2 (CVE-2026-0621, CVE-2025-66414)
# - qs@6.14.1 (CVE-2025-15284)
RUN cd /usr/local/lib/node_modules/supergateway && \
    npm install @modelcontextprotocol/sdk@1.25.2 qs@6.14.1 && \
    rm -rf node_modules/prev-modelcontextprotocol-sdk \
           node_modules/body-parser/node_modules/qs \
           node_modules/express/node_modules/qs
