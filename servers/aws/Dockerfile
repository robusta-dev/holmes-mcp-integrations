# AWS API MCP Server with Supergateway
# Uses Supergateway base image and adds Python

FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

# Switch to root to install Python
USER root

RUN apk add --no-cache python3 py3-pip

# Install AWS API MCP server
RUN pip3 install --no-cache-dir --break-system-packages awslabs.aws-api-mcp-server

USER node
EXPOSE 8000

CMD ["--port", "8000", "--stdio", "python3 -m awslabs.aws_api_mcp_server.server"]
