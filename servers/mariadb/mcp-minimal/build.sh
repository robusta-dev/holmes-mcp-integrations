#!/bin/bash

# Build script for MariaDB MCP Minimal Server

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Building MariaDB MCP Minimal Server${NC}"
echo "====================================="

# Default values
REGISTRY=${DOCKER_REGISTRY:-"localhost:5000"}
IMAGE_NAME="mariadb-mcp-minimal"
IMAGE_TAG="latest"
FULL_IMAGE="$REGISTRY/$IMAGE_NAME:$IMAGE_TAG"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --registry)
            REGISTRY="$2"
            shift 2
            ;;
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --push)
            PUSH=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./build.sh [--registry REGISTRY] [--tag TAG] [--push]"
            exit 1
            ;;
    esac
done

FULL_IMAGE="$REGISTRY/$IMAGE_NAME:$IMAGE_TAG"

echo -e "${YELLOW}Building image: $FULL_IMAGE${NC}"

# Build the image
echo -e "${BLUE}Step 1: Building Docker image...${NC}"
docker build -t "$FULL_IMAGE" .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Image built successfully${NC}"
else
    echo -e "${RED}✗ Build failed${NC}"
    exit 1
fi

# Get image size
SIZE=$(docker images "$FULL_IMAGE" --format "{{.Size}}")
echo -e "${GREEN}Image size: $SIZE${NC}"

# Push if requested
if [ "$PUSH" = true ]; then
    echo -e "${BLUE}Step 2: Pushing image to registry...${NC}"
    docker push "$FULL_IMAGE"

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Image pushed successfully${NC}"
    else
        echo -e "${RED}✗ Push failed${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}Skipping push (use --push to push to registry)${NC}"
fi

# Update deployment file
echo -e "${BLUE}Step 3: Updating deployment.yaml...${NC}"
sed -i.bak "s|image: .*|image: $FULL_IMAGE|g" deployment.yaml
echo -e "${GREEN}✓ Updated deployment.yaml with image: $FULL_IMAGE${NC}"

echo ""
echo -e "${GREEN}✅ Build complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Deploy to Kubernetes:"
echo "     kubectl apply -f deployment.yaml"
echo "     kubectl apply -f service.yaml"
echo ""
echo "  2. Update Holmes configuration to use:"
echo "     http://mariadb-mcp-minimal.mariadb.svc.cluster.local:8000/mcp"
echo ""
echo "Image: $FULL_IMAGE"
echo "Size: $SIZE"