# Test image for supergateway base
# Verifies the base image works correctly with a simple MCP server

FROM us-central1-docker.pkg.dev/genuine-flight-317411/devel/supergateway_base:latest

COPY test_server.js /app/test_server.js

EXPOSE 8000

CMD ["--port", "8000", "--stdio", "node /app/test_server.js"]
