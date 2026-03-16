#!/bin/bash
# Test that a Grafana API key works and can query Prometheus via the datasource proxy.
# Usage: ./test-grafana-api-key.sh <api-key> <grafana-url>

set -euo pipefail

API_KEY="${1:?Usage: $0 <api-key> <grafana-url>}"
GRAFANA_URL="${2:?Usage: $0 <api-key> <grafana-url>}"
GRAFANA_URL="${GRAFANA_URL%/}"

AUTH_HEADER="Authorization: Bearer ${API_KEY}"

echo "=== 1. Health check ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${GRAFANA_URL}/api/health")
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: /api/health returned ${HTTP_CODE}"
  exit 1
fi
echo "OK: Grafana is reachable"

echo ""
echo "=== 2. Authenticate with API key ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -H "${AUTH_HEADER}" "${GRAFANA_URL}/api/org")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: /api/org returned ${HTTP_CODE}"
  echo "$BODY"
  exit 1
fi
ORG_NAME=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name',''))" 2>/dev/null || echo "")
echo "OK: Authenticated (org: ${ORG_NAME})"

echo ""
echo "=== 3. List datasources ==="
RESPONSE=$(curl -s -w "\n%{http_code}" -H "${AUTH_HEADER}" "${GRAFANA_URL}/api/datasources")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: /api/datasources returned ${HTTP_CODE}"
  echo "$BODY"
  exit 1
fi

DS_COUNT=$(echo "$BODY" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
echo "OK: Found ${DS_COUNT} datasource(s)"

# Find a Prometheus datasource
PROM_UID=$(echo "$BODY" | python3 -c "
import sys, json
ds = json.load(sys.stdin)
for d in ds:
    if d.get('type') == 'prometheus':
        print(d['uid'])
        break
" 2>/dev/null || echo "")

if [ -z "$PROM_UID" ]; then
  echo ""
  echo "=== 4. Prometheus query: SKIPPED (no Prometheus datasource found) ==="
  echo ""
  echo "All authentication tests passed. No Prometheus datasource to test."
  exit 0
fi

echo ""
echo "=== 4. Query Prometheus via datasource proxy (uid: ${PROM_UID}) ==="
QUERY="up"
RESPONSE=$(curl -s -w "\n%{http_code}" -H "${AUTH_HEADER}" \
  "${GRAFANA_URL}/api/datasources/uid/${PROM_UID}/resources/api/v1/query?query=${QUERY}")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: Prometheus proxy returned ${HTTP_CODE}"
  echo "$BODY"
  echo ""
  echo "This endpoint requires Grafana 9.5+. Older versions do not support /api/datasources/uid/.../resources."
  exit 1
fi

RESULT_COUNT=$(echo "$BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(len(data.get('data', {}).get('result', [])))
" 2>/dev/null || echo "?")
echo "OK: query '${QUERY}' returned ${RESULT_COUNT} series"

echo ""
echo "All tests passed."
