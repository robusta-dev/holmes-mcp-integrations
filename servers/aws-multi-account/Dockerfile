FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/supergateway_base:latest

USER root

RUN apk add --no-cache python3 py3-pip

RUN pip3 install --no-cache-dir --break-system-packages \
    awslabs.aws-api-mcp-server \
    boto3 \
    pyyaml

COPY aws_auth.py /app/
COPY wrapper.py /app/

ENV AWS_MCP_SERVER_CMD="python3 -m awslabs.aws_api_mcp_server.server"

USER root
EXPOSE 8000

CMD ["--port", "8000", "--stdio", "python3 /app/wrapper.py"]
