# MariaDB Integration for Holmes

This directory contains a complete MariaDB integration for Holmes, including a MariaDB MCP (Model Context Protocol) server that enables Holmes to troubleshoot database issues effectively.

## Overview

The integration provides:
- **MariaDB Server**: Full database deployment on Kubernetes with persistent storage
- **MCP Server**: Bridges Holmes to MariaDB for database troubleshooting
- **Test Applications**: Simulate real-world database problems (deadlocks, slow queries, etc.)
- **Authentication**: Secure, read-only access for Holmes via dedicated database user
- **Test Scenarios**: Scripts to validate Holmes' troubleshooting capabilities

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Holmes    │────▶│  MCP Server  │────▶│   MariaDB   │
└─────────────┘     └──────────────┘     └─────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ Read-only    │
                    │ mcp_readonly │
                    │ user         │
                    └──────────────┘
```

## Quick Start

### Prerequisites
- Kubernetes cluster with kubectl configured
- Docker (for building MCP server image)
- Holmes installed and running in your cluster

### 1. Deploy Everything
```bash
cd scripts
./setup.sh
```

This script will:
1. Generate secure passwords
2. Create the `mariadb` namespace
3. Deploy MariaDB with persistent storage
4. Build and deploy the MCP server
5. Optionally deploy test applications

### 2. Configure Holmes

Add the MCP server configuration to your Holmes deployment:

```yaml
mcp_servers:
  mariadb:
    description: "MariaDB database troubleshooting"
    config:
      url: "http://mariadb-mcp-server.mariadb.svc.cluster.local:8000/mcp/messages"
      mode: streamable-http
```

Or apply the provided configuration:
```bash
kubectl apply -f holmes-config/mariadb-toolset.yaml
```

### 3. Test the Integration

Verify everything is working:
```bash
./verify-setup.sh
```

Test with Holmes:
```bash
holmes ask "Check the health of the MariaDB database in the mariadb namespace"
```

## Test Scenarios

### Deadlock Detection
```bash
./test-deadlock.sh
```
This deploys an app that creates database deadlocks. Test with:
```bash
holmes ask "Are there any database deadlocks occurring?"
```

### Slow Query Analysis
```bash
./test-slow-queries.sh
```
This runs inefficient queries. Test with:
```bash
holmes ask "What are the slowest queries in the database?"
```

## Directory Structure

```
mariadb/
├── test-mariadb-server/     # MariaDB Kubernetes manifests
│   ├── 00-namespace.yaml    # Namespace definition
│   ├── 01-secrets.yaml      # Generated credentials (git-ignored)
│   ├── 02-configmap.yaml    # MariaDB configuration
│   ├── 03-init-scripts.yaml # Database initialization
│   ├── 04-statefulset.yaml  # MariaDB StatefulSet
│   ├── 05-service.yaml      # Services
│   └── 06-networkpolicy.yaml# Network security
│
├── mcp-server/              # MCP server deployment
│   ├── Dockerfile          # Container image
│   ├── configmap.yaml      # MCP configuration
│   ├── deployment.yaml     # Kubernetes deployment
│   └── service.yaml        # Kubernetes service
│
├── test-apps/               # Problem simulation apps
│   ├── deadlock-app/       # Creates deadlocks
│   ├── slow-query-app/     # Runs slow queries
│   ├── high-load-app/      # Connection stress (TODO)
│   └── connection-leak-app/# Connection leaks (TODO)
│
├── holmes-config/           # Holmes integration
│   └── mariadb-toolset.yaml# MCP server configuration
│
├── scripts/                 # Management scripts
│   ├── setup.sh            # Complete setup
│   ├── verify-setup.sh     # Health check
│   ├── test-deadlock.sh    # Run deadlock test
│   ├── test-slow-queries.sh# Run slow query test
│   ├── cleanup.sh          # Remove everything
│   └── generate-passwords.sh# Password generation
│
└── README.md               # This file
```

## Security Features

### Read-Only Access
The MCP server uses a dedicated `mcp_readonly` user with:
- SELECT privileges only
- Access to performance_schema and information_schema
- PROCESS privilege to see running queries
- Connection limits to prevent abuse

### Network Policies
NetworkPolicy restricts database access to:
- MCP server pods
- Test application pods
- Pods within the mariadb namespace

### Secret Management
- Passwords are generated automatically
- Stored in Kubernetes secrets
- Never committed to git (add to .gitignore)

## Database Schema

The test database (`testdb`) includes:
- `customers` - Customer records
- `products` - Product catalog
- `orders` - Order transactions
- `order_items` - Order line items
- `inventory` - Stock levels (for deadlock testing)
- `audit_log` - Unindexed table (for slow query testing)

## Troubleshooting Capabilities

Holmes can diagnose:
- **Deadlocks**: Transaction conflicts and lock waits
- **Slow Queries**: Missing indexes, inefficient joins
- **Connection Issues**: Pool exhaustion, connection limits
- **Performance**: Query statistics, table scans
- **Schema Issues**: Missing indexes, table structure

### Example Holmes Queries

```bash
# Check for deadlocks
holmes ask "Show me recent deadlocks in MariaDB"

# Analyze slow queries
holmes ask "What queries are taking the longest to execute?"

# Check connections
holmes ask "How many database connections are currently in use?"

# Find blocking queries
holmes ask "Are there any queries blocking other transactions?"

# Suggest optimizations
holmes ask "Which tables need indexes for better performance?"
```

## MCP Server Details

The MariaDB MCP server runs natively in HTTP mode and provides these tools:
- List databases and tables
- View table schemas
- Execute SQL queries (restricted by user permissions)
- Access performance metrics
- Check running processes
- Analyze lock information

### Environment Variables

The MCP server uses these environment variables:
- `DB_HOST`: Database host
- `DB_PORT`: Database port (3306)
- `DB_USER`: Username (mcp_readonly)
- `DB_PASSWORD`: Password from secret
- `DB_NAME`: Default database (testdb)
- `MCP_READ_ONLY`: Enforce read-only SQL mode (true) - built-in MCP safety feature
- `MCP_MAX_POOL_SIZE`: Connection pool size (5)
- `DB_SSL`: SSL connection (false for internal cluster)

The server runs as a native HTTP endpoint at:
```
http://mariadb-mcp-server.mariadb.svc.cluster.local:8000/mcp/messages
```

## Maintenance

### Check Status
```bash
kubectl get all -n mariadb
./verify-setup.sh
```

### View Logs
```bash
# MariaDB logs
kubectl logs -n mariadb mariadb-0

# MCP server logs
kubectl logs -n mariadb -l app=mariadb-mcp-server

# Test app logs
kubectl logs -n mariadb -l app=deadlock-app
```

### Access MariaDB Directly
```bash
# Access MariaDB shell
kubectl exec -it -n mariadb mariadb-0 -- mariadb -uroot -p$(kubectl get secret -n mariadb mariadb-root-secret -o jsonpath='{.data.password}' | base64 -d)

# Run a quick query
kubectl exec -n mariadb mariadb-0 -- mariadb -uroot -p$(kubectl get secret -n mariadb mariadb-root-secret -o jsonpath='{.data.password}' | base64 -d) -e "SHOW DATABASES;"
```

### Clean Up
Remove all resources:
```bash
./cleanup.sh
```

## Building the MCP Server

The MCP server runs the native MariaDB MCP in HTTP mode:

```bash
cd mcp-server
docker build -t <your-registry>/mariadb-mcp:latest .
docker push <your-registry>/mariadb-mcp:latest
```

Update `mcp-server/deployment.yaml` with your image.

The server runs natively in HTTP mode without needing Supergateway, using:
```
python server.py --transport http --host 0.0.0.0 --port 8000 --path /mcp/messages
```

## Development

### Adding Test Scenarios

1. Create app in `test-apps/your-scenario/`
2. Add ConfigMap and Deployment manifests
3. Create test script in `scripts/test-your-scenario.sh`

### Customizing MariaDB

Edit `mariadb-server/02-configmap.yaml` for:
- Performance tuning
- Logging configuration
- InnoDB settings
- Slow query thresholds

### Extending the MCP Server

The MCP server can be extended with:
- Custom SQL analysis tools
- Performance monitoring
- Schema migration checks
- Replication monitoring

## Troubleshooting Setup Issues

### MariaDB Won't Start
```bash
kubectl describe pod mariadb-0 -n mariadb
kubectl logs mariadb-0 -n mariadb
```

### MCP Server Connection Issues
```bash
# Check if MCP server can reach MariaDB
kubectl exec -n mariadb deploy/mariadb-mcp-server -- nc -zv mariadb-service 3306

# Check MCP logs
kubectl logs -n mariadb deploy/mariadb-mcp-server
```

### Test Apps Not Working
```bash
# Check app logs
kubectl logs -n mariadb -l app=deadlock-app

# Verify database connectivity
kubectl exec -n mariadb deploy/deadlock-app -- nc -zv mariadb-service 3306
```

## Best Practices

1. **Always use read-only access** for troubleshooting tools
2. **Set resource limits** on all pods to prevent cluster issues
3. **Use NetworkPolicies** to restrict database access
4. **Monitor PVC usage** - database can grow with test data
5. **Rotate passwords** periodically for security
6. **Clean up test apps** when not testing to save resources

## Known Limitations

1. MCP server currently supports single database instance only
2. No replication monitoring (single instance deployment)
3. Test apps generate synthetic workloads, not production patterns
4. Supergateway health endpoint may not respond (normal behavior)

## Contributing

To add new test scenarios or improve the integration:
1. Create feature branch
2. Add test scenario in `test-apps/`
3. Update documentation
4. Test with `verify-setup.sh`
5. Submit PR

## Support

For issues or questions:
1. Check Holmes logs: `kubectl logs -n <namespace> <holmes-pod>`
2. Verify MCP server: `./verify-setup.sh`
3. Check MariaDB health: `kubectl exec -n mariadb mariadb-0 -- mysqladmin ping`
4. Review NetworkPolicies if connection issues occur

## License

This integration follows the HolmesGPT project license.