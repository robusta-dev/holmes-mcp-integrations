# holmes-mcp-integrations

MCP integrations for HolmesGPT

## Repository Structure

```
holmes-mcp-integrations/
├── build-all-mcp-servers.sh      # Build script for all MCP servers
├── mcp_base_image/               # Base Docker image for MCP servers
└── servers/                      # MCP server implementations
    ├── aws/                      # AWS API MCP Server
    ├── aws-multi-account/        # AWS Multi-Account MCP Server
    ├── azure/                    # Azure CLI MCP Server
    ├── confluence/               # Confluence MCP Server (external image)
    ├── gcp/
    │   ├── gcloud/               # GCP gcloud CLI MCP Server
    │   ├── observability/        # GCP Observability MCP Server
    │   └── storage/              # GCP Storage MCP Server
    ├── github/                   # GitHub MCP Server
    ├── kubernetes-remediation/   # Kubernetes Remediation MCP Server
    ├── mariadb/
    │   └── mcp-minimal/          # MariaDB MCP Server (minimal)
    └── sentry/                   # Sentry MCP Server
```

## Building MCP Servers

### Configuration

Each MCP server directory contains an `auto-build-config.yaml` file that defines the image name and version:

```yaml
image: azure-cli-mcp
version: "1.0.2"
```

### Build All Servers

Use the `build-all-mcp-servers.sh` script to build and push all MCP server images:

```bash
# Build all servers (skips images that already exist)
./build-all-mcp-servers.sh

# Dry run - list servers and check which images exist (no build)
./build-all-mcp-servers.sh --dry-run

# Force rebuild all servers (even if they exist)
./build-all-mcp-servers.sh --force

# Show help
./build-all-mcp-servers.sh --help
```

The script will:
- Auto-discover all servers with `auto-build-config.yaml`
- Check if each image already exists in the registry
- Skip existing images (unless `--force` is used)
- Build multi-platform images (linux/amd64, linux/arm64)
- Push to the configured registry

### Build Single Server

Each server directory also has its own `build-push.sh` script:

```bash
cd servers/azure
./build-push.sh
```

### Bumping Versions

To release a new version of an MCP server:

1. Update the `version` in the server's `auto-build-config.yaml`:
   ```yaml
   image: azure-cli-mcp
   version: "1.0.3"  # bumped from 1.0.2
   ```

2. Run the build:
   ```bash
   # Build just that server
   cd servers/azure && ./build-push.sh

   # Or build all (will only build the changed one)
   ./build-all-mcp-servers.sh
   ```

## Registry

All images are pushed to:
```
us-central1-docker.pkg.dev/genuine-flight-317411/devel/
```

## Adding a New MCP Server

1. Create a new directory under `servers/`:
   ```bash
   mkdir servers/my-new-mcp
   ```

2. Add a `Dockerfile`:
   ```dockerfile
   FROM us-central1-docker.pkg.dev/genuine-flight-317411/devel/supergateway_base:latest
   # ... your server setup
   ```

3. Add `auto-build-config.yaml`:
   ```yaml
   image: my-new-mcp
   version: "1.0.0"
   ```

4. Add `build-push.sh`:
   ```bash
   #!/bin/bash
   set -e
   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
   REGISTRY="us-central1-docker.pkg.dev/genuine-flight-317411/devel"

   image=$(grep '^image:' "$SCRIPT_DIR/auto-build-config.yaml" | sed 's/image: *//')
   version=$(grep '^version:' "$SCRIPT_DIR/auto-build-config.yaml" | sed 's/version: *//' | tr -d '"')

   docker buildx build --pull --no-cache --build-arg BUILDKIT_INLINE_CACHE=1 --platform linux/arm64,linux/amd64 --tag "$REGISTRY/$image:$version" --push .
   ```

5. Make it executable:
   ```bash
   chmod +x servers/my-new-mcp/build-push.sh
   ```

The new server will be automatically discovered by `build-all-mcp-servers.sh`.
