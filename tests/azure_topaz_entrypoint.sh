#!/bin/sh
# Logs az in to the Topaz emulator, seeds a resource group, then starts the image's azure-api-mcp.
# TODO: replace Topaz with a real Reader-only service principal (MCP_TEST_AZURE_* secrets).
set -e
python3 -c "import ssl, certifi; open('/tmp/ca.pem', 'w').write(open(certifi.where()).read() + ssl.get_server_certificate(('topaz.local.dev', 8899)))"
export REQUESTS_CA_BUNDLE=/tmp/ca.pem AZURE_CORE_INSTANCE_DISCOVERY=false
az cloud register -n Topaz --cloud-config @/tests/topaz-cloud.json >/dev/null
az cloud set -n Topaz
az login -u topazadmin@topaz.local.dev -p admin --allow-no-subscriptions -o none 2>/dev/null
az group create -n mcp-test-rg -l westeurope -o none
exec azure-api-mcp "$@"
