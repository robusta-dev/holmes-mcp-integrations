# Dockerfile for custom multi-account AWS MCP server
FROM node:18-alpine

WORKDIR /app

# Install AWS SDK
RUN npm install -g \
    @aws-sdk/client-sts \
    @aws-sdk/client-ec2 \
    @aws-sdk/client-s3 \
    @aws-sdk/client-cloudwatch \
    @aws-sdk/client-cloudwatch-logs \
    @modelcontextprotocol/sdk \
    js-yaml

# Copy the auth module and server code
COPY lib/ ./lib/
COPY server.js ./

EXPOSE 8000

CMD ["node", "server.js"]
