#!/bin/bash

# Test script for slow query scenario
# This script deploys the slow query app and tests Holmes' ability to detect performance issues

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}            MariaDB Slow Query Test Scenario                  ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Check if MariaDB is running
echo -e "${YELLOW}Checking MariaDB status...${NC}"
if ! kubectl get pod -n mariadb -l app=mariadb | grep -q Running; then
    echo -e "${RED}Error: MariaDB is not running. Run ./setup.sh first${NC}"
    exit 1
fi
echo -e "${GREEN}✓ MariaDB is running${NC}"

# Deploy slow query app if not already deployed
echo ""
echo -e "${YELLOW}Deploying slow query generator application...${NC}"

# Create ConfigMap from app.py
kubectl create configmap slow-query-app-config \
    --from-file=app.py=../test-apps/slow-query-app/app.py \
    -n mariadb --dry-run=client -o yaml | kubectl apply -f -

# Deploy the application
kubectl apply -f ../test-apps/slow-query-app/deployment.yaml

# Wait for deployment
echo "Waiting for slow query app to start..."
kubectl wait --for=condition=available deployment/slow-query-app -n mariadb --timeout=120s || true

# Check if app is running
POD_NAME=$(kubectl get pods -n mariadb -l app=slow-query-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [ -z "$POD_NAME" ]; then
    echo -e "${RED}Failed to deploy slow query app${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Slow query generator deployed: $POD_NAME${NC}"

# Let it run for a bit to generate some slow queries
echo ""
echo -e "${YELLOW}Waiting for slow queries to be generated...${NC}"
echo "Monitoring application logs for 45 seconds..."
echo ""

# Show logs for 45 seconds (queries run every 15 seconds)
timeout 45 kubectl logs -f -n mariadb "$POD_NAME" 2>/dev/null || true

# Check for slow queries in the logs
echo ""
echo -e "${YELLOW}Checking for slow queries...${NC}"
SLOW_COUNT=$(kubectl logs -n mariadb "$POD_NAME" --tail=100 | grep -c "SLOW QUERY DETECTED" || echo "0")

if [ "$SLOW_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓ Detected $SLOW_COUNT slow queries${NC}"
else
    echo -e "${YELLOW}⚠ No slow queries logged yet, they may take time to appear${NC}"
fi

# Check slow query log in MariaDB
echo ""
echo -e "${YELLOW}Checking MariaDB slow query log...${NC}"
SLOW_LOG_COUNT=$(kubectl exec -n mariadb mariadb-0 -- bash -c "mariadb -uroot -p\$MYSQL_ROOT_PASSWORD -e 'SELECT COUNT(*) as count FROM mysql.slow_log' 2>/dev/null | tail -1" || echo "0")
echo "Slow queries in MariaDB log: $SLOW_LOG_COUNT"

# Test with Holmes
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}                  Testing with Holmes                         ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "Now you can test Holmes with these queries:"
echo ""
echo -e "${GREEN}1. Analyze slow queries:${NC}"
echo "   holmes ask 'What are the slowest queries running in the MariaDB database?'"
echo ""
echo -e "${GREEN}2. Check for missing indexes:${NC}"
echo "   holmes ask 'Are there any tables in testdb that need indexes for better performance?'"
echo ""
echo -e "${GREEN}3. Get query performance stats:${NC}"
echo "   holmes ask 'Show me the performance statistics for queries in the testdb database'"
echo ""
echo -e "${GREEN}4. Analyze specific table performance:${NC}"
echo "   holmes ask 'Why are queries on the audit_log table running slowly?'"
echo ""
echo -e "${GREEN}5. Check current running queries:${NC}"
echo "   holmes ask 'What queries are currently running in the database?'"
echo ""

# Show current query statistics
echo -e "${BLUE}Current database performance snapshot:${NC}"
kubectl exec -n mariadb mariadb-0 -- mariadb -uroot -p$(kubectl get secret -n mariadb mariadb-root-secret -o jsonpath='{.data.password}' | base64 -d) -e "
SELECT
    SUBSTRING(digest_text, 1, 100) as query_pattern,
    count_star as exec_count,
    ROUND(sum_timer_wait/1000000000000, 2) as total_time_sec,
    ROUND(avg_timer_wait/1000000000000, 2) as avg_time_sec
FROM performance_schema.events_statements_summary_by_digest
WHERE schema_name = 'testdb'
ORDER BY sum_timer_wait DESC
LIMIT 5;
" 2>/dev/null || echo "No performance data available yet"

echo ""
echo -e "${YELLOW}Test app will continue generating slow queries...${NC}"
echo "To stop the test: kubectl delete deployment slow-query-app -n mariadb"
echo "To see logs: kubectl logs -f -n mariadb -l app=slow-query-app"