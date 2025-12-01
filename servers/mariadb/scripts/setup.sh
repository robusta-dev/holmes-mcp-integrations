#!/bin/bash

# Complete setup script for MariaDB integration with Holmes
# This script deploys everything needed for testing

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}     MariaDB Integration Setup for Holmes                     ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl is not installed or not in PATH${NC}"
    exit 1
fi

# Check cluster connection
echo -e "${YELLOW}Checking Kubernetes cluster connection...${NC}"
if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}Error: Cannot connect to Kubernetes cluster${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Connected to Kubernetes cluster${NC}"

# Step 1: Generate passwords
echo ""
echo -e "${YELLOW}Step 1: Generating secure passwords...${NC}"
cd "$(dirname "$0")"

if [ ! -f "../test-mariadb-server/01-secrets.yaml" ]; then
    ./generate-passwords.sh
    echo -e "${GREEN}✓ Passwords generated${NC}"
else
    echo -e "${GREEN}✓ Secrets file already exists${NC}"
fi

# Step 2: Create namespace
echo ""
echo -e "${YELLOW}Step 2: Creating MariaDB namespace...${NC}"
kubectl apply -f ../test-mariadb-server/00-namespace.yaml
echo -e "${GREEN}✓ Namespace created${NC}"

# Step 3: Apply secrets
echo ""
echo -e "${YELLOW}Step 3: Applying secrets...${NC}"
kubectl apply -f ../test-mariadb-server/01-secrets.yaml
echo -e "${GREEN}✓ Secrets applied${NC}"

# Step 4: Deploy MariaDB
echo ""
echo -e "${YELLOW}Step 4: Deploying MariaDB server...${NC}"
kubectl apply -f ../test-mariadb-server/02-configmap.yaml
kubectl apply -f ../test-mariadb-server/03-init-scripts.yaml
kubectl apply -f ../test-mariadb-server/04-statefulset.yaml
kubectl apply -f ../test-mariadb-server/05-service.yaml
kubectl apply -f ../test-mariadb-server/06-networkpolicy.yaml
echo -e "${GREEN}✓ MariaDB deployment started${NC}"

# Wait for MariaDB to be ready
echo ""
echo -e "${YELLOW}Waiting for MariaDB to be ready...${NC}"
kubectl wait --for=condition=ready pod -l app=mariadb -n mariadb --timeout=300s || {
    echo -e "${RED}MariaDB failed to start. Checking logs:${NC}"
    kubectl logs -n mariadb -l app=mariadb --tail=50
    exit 1
}
echo -e "${GREEN}✓ MariaDB is ready${NC}"

# Step 5: Deploy MCP server
echo ""
echo -e "${YELLOW}Step 5: Setting up MariaDB MCP server...${NC}"

# Use the pre-built minimal MCP server image
MCP_IMAGE="me-west1-docker.pkg.dev/robusta-development/development/mariadb-http-mcp-minimal:1.0.0"
echo "Using pre-built minimal MCP server image:"
echo "  $MCP_IMAGE"

# Update the deployment with the correct image
cd ../mcp-minimal
sed -i.bak "s|image: .*|image: $MCP_IMAGE|g" deployment.yaml

# Deploy MCP server (using minimal version)
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

cd ../scripts
echo -e "${GREEN}✓ MCP server deployment started (minimal version)${NC}"

# Step 6: Deploy test applications (optional)
echo ""
read -p "Do you want to deploy test applications? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Step 6: Deploying test applications...${NC}"

    # Create ConfigMaps for apps
    kubectl create configmap deadlock-app-config \
        --from-file=app.py=../test-apps/deadlock-app/app.py \
        -n mariadb --dry-run=client -o yaml | kubectl apply -f -

    kubectl create configmap slow-query-app-config \
        --from-file=app.py=../test-apps/slow-query-app/app.py \
        -n mariadb --dry-run=client -o yaml | kubectl apply -f -

    # Deploy applications
    kubectl apply -f ../test-apps/deadlock-app/deployment.yaml
    kubectl apply -f ../test-apps/slow-query-app/deployment.yaml

    echo -e "${GREEN}✓ Test applications deployed${NC}"
else
    echo -e "${YELLOW}Skipping test applications${NC}"
fi

# Step 7: Configure Holmes
echo ""
echo -e "${YELLOW}Step 7: Holmes configuration${NC}"
echo ""
echo "To configure Holmes with the MariaDB MCP server, add the following to your Holmes configuration:"
echo ""
echo -e "${BLUE}Option 1: Apply directly to Holmes deployment:${NC}"
echo "  kubectl apply -f ../holmes-config/mariadb-toolset.yaml"
echo ""
echo -e "${BLUE}Option 2: Add to your Holmes values.yaml:${NC}"
cat << 'EOF'
mcp_servers:
  mariadb:
    description: "MariaDB database troubleshooting"
    config:
      url: "http://mariadb-mcp-minimal.mariadb.svc.cluster.local:8000/mcp"
      mode: streamable-http
EOF

# Final status check
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}                    Setup Status                              ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

kubectl get all -n mariadb

echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Wait for all pods to be ready"
echo "  2. Configure Holmes with the MCP server settings above"
echo "  3. Test with: holmes ask 'Check the health of the MariaDB database'"
echo ""
echo "To run test scenarios:"
echo "  ./test-deadlock.sh     - Test deadlock detection"
echo "  ./test-slow-queries.sh - Test slow query analysis"
echo ""
echo "To cleanup everything:"
echo "  ./cleanup.sh"