# MariaDB MCP Minimal Server

A lightweight MCP server for MariaDB that provides essential database troubleshooting capabilities without ML/embedding dependencies.

## Features

This minimal server provides all the essential database tools needed for Holmes troubleshooting:

- **Database Operations**:
  - List databases and tables
  - Describe table schemas
  - Execute SQL queries (read-only mode by default)

- **Troubleshooting Tools**:
  - Show process list (running queries)
  - Show InnoDB status (deadlock detection)
  - Analyze slow queries from performance schema
  - Show variables and status

- **Safety Features**:
  - Read-only mode enforcement
  - Query result limits
  - Connection pooling

## Size Comparison

| Version | Image Size | Build Time | Dependencies |
|---------|-----------|------------|--------------|
| Full MariaDB MCP | ~2-3 GB | 10-15 min | Includes ML packages |
| **Minimal MCP** | **~150 MB** | **< 1 min** | **Database only** |

## Building the Image

```bash
cd mariadb/mcp-minimal
docker build -t mariadb-mcp-minimal:latest .
```

## Deploying to Kubernetes

1. Build and push the image:
```bash
docker build -t <your-registry>/mariadb-mcp-minimal:latest .
docker push <your-registry>/mariadb-mcp-minimal:latest
```

2. Update the image in `deployment.yaml`

3. Deploy to your cluster:
```bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

## Configuration

The server uses environment variables:

- `DB_HOST`: MariaDB host
- `DB_PORT`: MariaDB port (default: 3306)
- `DB_USER`: Database username
- `DB_PASSWORD`: Database password
- `DB_NAME`: Default database
- `MCP_READ_ONLY`: Enforce read-only mode (default: true)
- `MCP_MAX_ROWS`: Maximum rows to return (default: 1000)
- `MCP_MAX_POOL_SIZE`: Connection pool size (default: 5)

## Holmes Integration

Configure Holmes to use the minimal server:

```yaml
mcp_servers:
  mariadb:
    description: "MariaDB database troubleshooting (minimal)"
    config:
      url: "http://mariadb-mcp-minimal.mariadb.svc.cluster.local:8000/mcp"
      mode: streamable-http
```

## Available Tools

1. **list_databases** - List all databases
2. **list_tables** - List tables in a database
3. **describe_table** - Get table schema
4. **execute_query** - Run SQL queries
5. **show_process_list** - View active connections
6. **show_innodb_status** - Check for deadlocks
7. **show_variables** - View system variables
8. **show_status** - View server status
9. **analyze_slow_queries** - Analyze performance

## Testing the Server Locally

1. Run MariaDB locally:
```bash
docker run -d --name mariadb-test \
  -e MYSQL_ROOT_PASSWORD=test123 \
  -p 3306:3306 \
  mariadb:11
```

2. Run the MCP server:
```bash
export DB_HOST=localhost
export DB_USER=root
export DB_PASSWORD=test123
python server.py --transport http --host 0.0.0.0 --port 8000
```

3. Test with curl:
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"method": "tools/list", "params": {}}'
```

## Why Minimal?

The full MariaDB MCP includes:
- `sentence-transformers` (~1GB with PyTorch)
- `google-genai` and `openai` clients
- CUDA/GPU dependencies

The minimal version excludes these, focusing only on database operations, resulting in:
- 95% smaller image size
- 10x faster build time
- Lower memory usage
- Same database functionality

Perfect for troubleshooting without the overhead of ML features!