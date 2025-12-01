#!/bin/bash

# Verification script for MariaDB integration
# This script checks that all components are properly deployed and working

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}         MariaDB Integration Verification                     ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

ERRORS=0
WARNINGS=0

# Function to check resource
check_resource() {
    local resource_type=$1
    local resource_name=$2
    local namespace=$3

    if kubectl get "$resource_type" "$resource_name" -n "$namespace" &>/dev/null; then
        echo -e "${GREEN}✓${NC} $resource_type/$resource_name exists"
        return 0
    else
        echo -e "${RED}✗${NC} $resource_type/$resource_name NOT FOUND"
        ((ERRORS++))
        return 1
    fi
}

# Function to check pod status
check_pod_ready() {
    local label=$1
    local namespace=$2
    local name=$3

    POD_STATUS=$(kubectl get pods -n "$namespace" -l "$label" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "NotFound")
    READY=$(kubectl get pods -n "$namespace" -l "$label" -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")

    if [ "$POD_STATUS" == "Running" ] && [ "$READY" == "True" ]; then
        echo -e "${GREEN}✓${NC} $name pod is running and ready"
        return 0
    elif [ "$POD_STATUS" == "Running" ]; then
        echo -e "${YELLOW}⚠${NC} $name pod is running but not ready"
        ((WARNINGS++))
        return 1
    else
        echo -e "${RED}✗${NC} $name pod is not running (status: $POD_STATUS)"
        ((ERRORS++))
        return 1
    fi
}

# Check namespace
echo -e "${BLUE}Checking namespace...${NC}"
check_resource "namespace" "mariadb" "default"
echo ""

# Check secrets
echo -e "${BLUE}Checking secrets...${NC}"
check_resource "secret" "mariadb-root-secret" "mariadb"
check_resource "secret" "mariadb-app-secret" "mariadb"
check_resource "secret" "mariadb-mcp-secret" "mariadb"
echo ""

# Check ConfigMaps
echo -e "${BLUE}Checking ConfigMaps...${NC}"
check_resource "configmap" "mariadb-config" "mariadb"
check_resource "configmap" "mariadb-init-scripts" "mariadb"
echo ""

# Check MariaDB deployment
echo -e "${BLUE}Checking MariaDB server...${NC}"
check_resource "statefulset" "mariadb" "mariadb"
check_resource "service" "mariadb-service" "mariadb"
check_pod_ready "app=mariadb" "mariadb" "MariaDB"

# Test MariaDB connectivity
if kubectl get pod -n mariadb -l app=mariadb | grep -q Running; then
    echo -n "Testing MariaDB connectivity... "
    if kubectl exec -n mariadb mariadb-0 -- mariadb -uroot -p$(kubectl get secret -n mariadb mariadb-root-secret -o jsonpath='{.data.password}' | base64 -d) -e "SELECT 1" &>/dev/null; then
        echo -e "${GREEN}✓ Database is accessible${NC}"
    else
        echo -e "${RED}✗ Cannot connect to database${NC}"
        ((ERRORS++))
    fi

    # Check if test database exists
    echo -n "Checking test database... "
    if kubectl exec -n mariadb mariadb-0 -- mariadb -uroot -p$(kubectl get secret -n mariadb mariadb-root-secret -o jsonpath='{.data.password}' | base64 -d) -e "USE testdb; SELECT COUNT(*) FROM customers;" &>/dev/null; then
        echo -e "${GREEN}✓ Test database and tables exist${NC}"
    else
        echo -e "${YELLOW}⚠ Test database may not be properly initialized${NC}"
        ((WARNINGS++))
    fi

    # Check users
    echo -n "Checking database users... "
    USER_COUNT=$(kubectl exec -n mariadb mariadb-0 -- mariadb -uroot -p$(kubectl get secret -n mariadb mariadb-root-secret -o jsonpath='{.data.password}' | base64 -d) -e "SELECT COUNT(*) FROM mysql.user WHERE User IN ('app_user', 'mcp_readonly');" 2>/dev/null | tail -1 || echo "0")
    if [ "$USER_COUNT" == "2" ]; then
        echo -e "${GREEN}✓ Database users configured${NC}"
    else
        echo -e "${RED}✗ Database users not properly configured${NC}"
        ((ERRORS++))
    fi
fi
echo ""

# Check MCP server
echo -e "${BLUE}Checking MCP server...${NC}"
check_resource "deployment" "mariadb-mcp-server" "mariadb"
check_resource "service" "mariadb-mcp-server" "mariadb"
check_pod_ready "app=mariadb-mcp-server" "mariadb" "MCP Server"

# Test MCP server connectivity (if available)
if kubectl get pod -n mariadb -l app=mariadb-mcp-server | grep -q Running; then
    echo -n "Testing MCP server endpoint... "
    # Try to check if the service responds
    if kubectl run -n mariadb test-curl --image=curlimages/curl:latest --rm -it --restart=Never -- \
        curl -s -o /dev/null -w "%{http_code}" http://mariadb-mcp-server:8000/health 2>/dev/null | grep -q "200\|404"; then
        echo -e "${GREEN}✓ MCP server is reachable${NC}"
    else
        echo -e "${YELLOW}⚠ Cannot verify MCP server endpoint${NC}"
        ((WARNINGS++))
    fi
fi
echo ""

# Check test applications (if deployed)
echo -e "${BLUE}Checking test applications (if deployed)...${NC}"
if kubectl get deployment deadlock-app -n mariadb &>/dev/null; then
    check_pod_ready "app=deadlock-app" "mariadb" "Deadlock App"
else
    echo "  Deadlock app not deployed"
fi

if kubectl get deployment slow-query-app -n mariadb &>/dev/null; then
    check_pod_ready "app=slow-query-app" "mariadb" "Slow Query App"
else
    echo "  Slow query app not deployed"
fi
echo ""

# Check NetworkPolicy
echo -e "${BLUE}Checking network policies...${NC}"
check_resource "networkpolicy" "mariadb-network-policy" "mariadb"
echo ""

# Check PVC
echo -e "${BLUE}Checking persistent storage...${NC}"
PVC_STATUS=$(kubectl get pvc mariadb-storage-mariadb-0 -n mariadb -o jsonpath='{.status.phase}' 2>/dev/null || echo "NotFound")
if [ "$PVC_STATUS" == "Bound" ]; then
    echo -e "${GREEN}✓${NC} PVC is bound"
else
    echo -e "${RED}✗${NC} PVC status: $PVC_STATUS"
    ((ERRORS++))
fi
echo ""

# Summary
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}                      Summary                                 ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✅ All checks passed! The MariaDB integration is properly set up.${NC}"
    echo ""
    echo "You can now:"
    echo "  1. Configure Holmes with the MCP server"
    echo "  2. Run test scenarios with ./test-deadlock.sh or ./test-slow-queries.sh"
    echo "  3. Use Holmes to troubleshoot database issues"
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}⚠ Setup is mostly complete with $WARNINGS warning(s)${NC}"
    echo "The integration should work, but you may want to investigate the warnings."
else
    echo -e "${RED}❌ Setup has $ERRORS error(s) and $WARNINGS warning(s)${NC}"
    echo "Please run ./setup.sh to complete the installation"
fi

# Show pod status
echo ""
echo -e "${BLUE}Current pod status in mariadb namespace:${NC}"
kubectl get pods -n mariadb