#!/bin/bash

# Test script for deadlock scenario
# This script deploys the deadlock app and tests Holmes' ability to detect it

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}              MariaDB Deadlock Test Scenario                  ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Check if MariaDB is running
echo -e "${YELLOW}Checking MariaDB status...${NC}"
if ! kubectl get pod -n mariadb -l app=mariadb | grep -q Running; then
    echo -e "${RED}Error: MariaDB is not running. Run ./setup.sh first${NC}"
    exit 1
fi
echo -e "${GREEN}✓ MariaDB is running${NC}"

# Deploy deadlock app if not already deployed
echo ""
echo -e "${YELLOW}Deploying deadlock generator application...${NC}"

# Create ConfigMap from app.py
kubectl create configmap deadlock-app-config \
    --from-file=app.py=../test-apps/deadlock-app/app.py \
    -n mariadb --dry-run=client -o yaml | kubectl apply -f -

# Deploy the application
kubectl apply -f ../test-apps/deadlock-app/deployment.yaml

# Wait for deployment
echo "Waiting for deadlock app to start..."
kubectl wait --for=condition=available deployment/deadlock-app -n mariadb --timeout=120s || true

# Check if app is running
POD_NAME=$(kubectl get pods -n mariadb -l app=deadlock-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [ -z "$POD_NAME" ]; then
    echo -e "${RED}Failed to deploy deadlock app${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Deadlock generator deployed: $POD_NAME${NC}"

# Let it run for a bit to generate some deadlocks
echo ""
echo -e "${YELLOW}Waiting for deadlocks to be generated...${NC}"
echo "Monitoring application logs for 30 seconds..."
echo ""

# Show logs for 30 seconds
timeout 30 kubectl logs -f -n mariadb "$POD_NAME" 2>/dev/null || true

# Check for deadlocks in the logs
echo ""
echo -e "${YELLOW}Checking for generated deadlocks...${NC}"
DEADLOCK_COUNT=$(kubectl logs -n mariadb "$POD_NAME" --tail=100 | grep -c "DEADLOCK detected" || echo "0")

if [ "$DEADLOCK_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓ Generated $DEADLOCK_COUNT deadlocks${NC}"
else
    echo -e "${YELLOW}⚠ No deadlocks detected yet, but they may occur soon${NC}"
fi

# Test with Holmes
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}                  Testing with Holmes                         ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "Now you can test Holmes with these queries:"
echo ""
echo -e "${GREEN}1. Check for deadlocks:${NC}"
echo "   holmes ask 'Are there any database deadlocks occurring in the MariaDB database?'"
echo ""
echo -e "${GREEN}2. Analyze transaction issues:${NC}"
echo "   holmes ask 'Why are database transactions failing in the testdb database?'"
echo ""
echo -e "${GREEN}3. Get deadlock details:${NC}"
echo "   holmes ask 'Show me the details of recent deadlocks in MariaDB'"
echo ""
echo -e "${GREEN}4. Check blocking queries:${NC}"
echo "   holmes ask 'Are there any blocking queries or lock waits in the database?'"
echo ""

# Show current status
echo -e "${BLUE}Current database status:${NC}"
kubectl exec -n mariadb mariadb-0 -- mariadb -uroot -p$(kubectl get secret -n mariadb mariadb-root-secret -o jsonpath='{.data.password}' | base64 -d) -e "SHOW ENGINE INNODB STATUS\G" 2>/dev/null | grep -A 20 "LATEST DETECTED DEADLOCK" || echo "No recent deadlocks in INNODB status"

echo ""
echo -e "${YELLOW}Test app will continue generating deadlocks...${NC}"
echo "To stop the test: kubectl delete deployment deadlock-app -n mariadb"
echo "To see logs: kubectl logs -f -n mariadb -l app=deadlock-app"