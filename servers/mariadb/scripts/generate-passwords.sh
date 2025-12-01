#!/bin/bash

# Generate secure passwords for MariaDB users
# This script generates passwords and creates the secrets YAML file

set -e

echo "🔐 Generating secure passwords for MariaDB users..."

# Function to generate a secure password
generate_password() {
    openssl rand -base64 32 | tr -d "=+/" | cut -c1-25
}

# Function to base64 encode
base64_encode() {
    echo -n "$1" | base64 | tr -d '\n'
}

# Generate passwords
ROOT_PASSWORD=$(generate_password)
APP_PASSWORD=$(generate_password)
MCP_PASSWORD=$(generate_password)

echo "Generated passwords:"
echo "  Root password: $ROOT_PASSWORD"
echo "  App password:  $APP_PASSWORD"
echo "  MCP password:  $MCP_PASSWORD"

# Create secrets YAML file
cat > ../test-mariadb-server/01-secrets.yaml <<EOF
# Auto-generated secrets for MariaDB
# Generated at: $(date)
# DO NOT COMMIT THIS FILE TO GIT
---
apiVersion: v1
kind: Secret
metadata:
  name: mariadb-root-secret
  namespace: mariadb
type: Opaque
data:
  password: $(base64_encode "$ROOT_PASSWORD")
---
apiVersion: v1
kind: Secret
metadata:
  name: mariadb-app-secret
  namespace: mariadb
type: Opaque
data:
  username: $(base64_encode "app_user")
  password: $(base64_encode "$APP_PASSWORD")
  database: $(base64_encode "testdb")
---
apiVersion: v1
kind: Secret
metadata:
  name: mariadb-mcp-secret
  namespace: mariadb
type: Opaque
data:
  username: $(base64_encode "mcp_readonly")
  password: $(base64_encode "$MCP_PASSWORD")
  host: $(base64_encode "mariadb-service.mariadb.svc.cluster.local")
  port: $(base64_encode "3306")
EOF

# Save passwords to a local file for reference (git-ignored)
cat > ../test-mariadb-server/.passwords <<EOF
# MariaDB Passwords (DO NOT COMMIT)
# Generated at: $(date)
ROOT_PASSWORD=$ROOT_PASSWORD
APP_PASSWORD=$APP_PASSWORD
MCP_PASSWORD=$MCP_PASSWORD
EOF

echo ""
echo "✅ Secrets YAML file created: ../test-mariadb-server/01-secrets.yaml"
echo "✅ Passwords saved to: ../test-mariadb-server/.passwords (for reference)"
echo ""
echo "⚠️  IMPORTANT: Add these files to .gitignore:"
echo "    mariadb/test-mariadb-server/01-secrets.yaml"
echo "    mariadb/test-mariadb-server/.passwords"