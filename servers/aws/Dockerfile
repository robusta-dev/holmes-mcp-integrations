FROM supercorp/supergateway:latest

USER root
RUN npm install -g npm@latest
RUN apk add --no-cache python3 py3-pip

# Install original AWS MCP server + our auth
RUN pip3 install --no-cache-dir --break-system-packages \
    awslabs.aws-api-mcp-server \
    boto3 \
    pyyaml

# Copy our auth wrapper
COPY aws_auth.py /app/
COPY wrapper.py /app/

USER node
EXPOSE 8000

# Use --stdio (not --sse) for command execution
CMD ["--port", "8000", "--stdio", "python3 /app/wrapper.py"]
