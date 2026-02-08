# Supergateway Base Image Test

Manual test to verify the supergateway base image works correctly.

## Usage

Run the test against the latest published base image:

```bash
./run_test.sh
```

## What it tests

1. Builds a test image using the base image
2. Starts a container with a simple MCP server
3. Establishes an SSE connection
4. Sends an MCP initialize request
5. Verifies the server responds correctly

## CI/CD

The GitHub Action in `.github/workflows/test-supergateway-base.yaml` runs this test automatically on PRs that modify `mcp_base_image/`. The CI builds the base image locally with tag `latest-test` before running the test.
