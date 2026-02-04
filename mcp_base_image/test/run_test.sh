#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="supergateway-base-test:latest"

echo "=== Building test image ==="
docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"

echo "=== Starting test container ==="
CONTAINER_ID=$(docker run -d -p 8765:8000 "$IMAGE_NAME")

cleanup() {
    echo "=== Cleaning up ==="
    # Kill background curl if running
    kill $SSE_PID 2>/dev/null || true
    docker stop "$CONTAINER_ID" >/dev/null 2>&1 || true
    docker rm "$CONTAINER_ID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Wait for server to start
echo "=== Waiting for server to start ==="
sleep 3

# Check container is still running
if ! docker ps -q --filter "id=$CONTAINER_ID" | grep -q .; then
    echo "=== TEST FAILED: Container exited ==="
    docker logs "$CONTAINER_ID"
    exit 1
fi

echo "=== Connecting to SSE endpoint ==="
# Start SSE connection in background, save output to file
SSE_OUTPUT_FILE=$(mktemp)
curl -s -N http://localhost:8765/sse > "$SSE_OUTPUT_FILE" 2>/dev/null &
SSE_PID=$!

# Wait for sessionId to appear
sleep 2

echo "SSE Output:"
cat "$SSE_OUTPUT_FILE"

# Extract sessionId from the SSE output (format: data: /message?sessionId=xxx)
SESSION_ID=$(grep -o 'sessionId=[^[:space:]]*' "$SSE_OUTPUT_FILE" | head -1 | cut -d'=' -f2)

if [ -z "$SESSION_ID" ]; then
    echo "=== TEST FAILED: Could not get sessionId ==="
    echo "=== Container logs ==="
    docker logs "$CONTAINER_ID"
    exit 1
fi

echo "=== Got sessionId: $SESSION_ID ==="

echo "=== Testing MCP initialize via /message endpoint ==="
INIT_RESPONSE=$(curl -s -X POST "http://localhost:8765/message?sessionId=$SESSION_ID" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}')

echo "Response: $INIT_RESPONSE"

# Wait a moment for the response to come through SSE
sleep 1
echo "=== SSE received data ==="
cat "$SSE_OUTPUT_FILE"

if echo "$INIT_RESPONSE" | grep -q "Accepted"; then
    echo "=== TEST PASSED: Server accepted the message ==="
    rm -f "$SSE_OUTPUT_FILE"
    exit 0
else
    echo "=== TEST FAILED: Unexpected response ==="
    echo "=== Container logs ==="
    docker logs "$CONTAINER_ID"
    rm -f "$SSE_OUTPUT_FILE"
    exit 1
fi
