#!/bin/bash

# Cleanup script for MariaDB integration
# This script removes all resources created by the setup

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}           MariaDB Integration Cleanup                        ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Confirmation
echo -e "${YELLOW}⚠️  WARNING: This will delete all MariaDB resources${NC}"
echo "This includes:"
echo "  - MariaDB database and all data"
echo "  - MCP server"
echo "  - Test applications"
echo "  - Secrets and configurations"
echo "  - PersistentVolumeClaims"
echo ""
read -p "Are you sure you want to continue? (yes/no) " -r
echo

if [[ ! $REPLY == "yes" ]]; then
    echo "Cleanup cancelled"
    exit 0
fi

# Delete test applications
echo -e "${YELLOW}Removing test applications...${NC}"
kubectl delete deployment deadlock-app -n mariadb 2>/dev/null || true
kubectl delete deployment slow-query-app -n mariadb 2>/dev/null || true
kubectl delete deployment high-load-app -n mariadb 2>/dev/null || true
kubectl delete deployment connection-leak-app -n mariadb 2>/dev/null || true
kubectl delete configmap deadlock-app-config -n mariadb 2>/dev/null || true
kubectl delete configmap slow-query-app-config -n mariadb 2>/dev/null || true
kubectl delete configmap high-load-app-config -n mariadb 2>/dev/null || true
kubectl delete configmap connection-leak-app-config -n mariadb 2>/dev/null || true
echo -e "${GREEN}✓ Test applications removed${NC}"

# Delete MCP server
echo -e "${YELLOW}Removing MCP server...${NC}"
kubectl delete deployment mariadb-mcp-server -n mariadb 2>/dev/null || true
kubectl delete service mariadb-mcp-server -n mariadb 2>/dev/null || true
echo -e "${GREEN}✓ MCP server removed${NC}"

# Delete MariaDB
echo -e "${YELLOW}Removing MariaDB server...${NC}"
kubectl delete statefulset mariadb -n mariadb 2>/dev/null || true
kubectl delete service mariadb-service -n mariadb 2>/dev/null || true
kubectl delete service mariadb-headless -n mariadb 2>/dev/null || true
kubectl delete networkpolicy mariadb-network-policy -n mariadb 2>/dev/null || true
kubectl delete configmap mariadb-config -n mariadb 2>/dev/null || true
kubectl delete configmap mariadb-init-scripts -n mariadb 2>/dev/null || true
echo -e "${GREEN}✓ MariaDB server removed${NC}"

# Delete PVCs
echo -e "${YELLOW}Removing PersistentVolumeClaims...${NC}"
kubectl delete pvc mariadb-storage-mariadb-0 -n mariadb 2>/dev/null || true
echo -e "${GREEN}✓ PVCs removed${NC}"

# Delete secrets
echo -e "${YELLOW}Removing secrets...${NC}"
kubectl delete secret mariadb-root-secret -n mariadb 2>/dev/null || true
kubectl delete secret mariadb-app-secret -n mariadb 2>/dev/null || true
kubectl delete secret mariadb-mcp-secret -n mariadb 2>/dev/null || true
echo -e "${GREEN}✓ Secrets removed${NC}"

# Delete namespace (this will clean up any remaining resources)
echo -e "${YELLOW}Removing namespace...${NC}"
kubectl delete namespace mariadb --wait=false 2>/dev/null || true
echo -e "${GREEN}✓ Namespace deletion initiated${NC}"

# Clean up local files
echo -e "${YELLOW}Cleaning up local files...${NC}"
rm -f ../test-mariadb-server/01-secrets.yaml 2>/dev/null || true
rm -f ../test-mariadb-server/.passwords 2>/dev/null || true
echo -e "${GREEN}✓ Local files cleaned${NC}"

# Wait for namespace to be fully deleted
echo ""
echo -e "${YELLOW}Waiting for namespace to be fully deleted...${NC}"
echo "This may take a minute..."

COUNTER=0
while kubectl get namespace mariadb 2>/dev/null; do
    if [ $COUNTER -gt 60 ]; then
        echo -e "${YELLOW}⚠ Namespace deletion is taking longer than expected${NC}"
        echo "You may need to manually check for stuck resources:"
        echo "  kubectl get all -n mariadb"
        echo "  kubectl delete namespace mariadb --force --grace-period=0"
        break
    fi
    sleep 2
    COUNTER=$((COUNTER + 1))
    echo -n "."
done

echo ""
echo ""
echo -e "${GREEN}✅ Cleanup complete!${NC}"
echo ""
echo "All MariaDB integration resources have been removed."
echo "To set up again, run: ./setup.sh"