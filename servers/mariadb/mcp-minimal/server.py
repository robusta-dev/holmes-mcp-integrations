#!/usr/bin/env python3
"""
Minimal MariaDB MCP Server
A lightweight MCP server for MariaDB without ML/embedding dependencies
Based on MariaDB MCP but simplified for database operations only
"""

import os
import json
import logging
import asyncio
import ssl
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

import asyncmy
from fastmcp import FastMCP
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Database configuration from environment
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", ""),
    "charset": os.getenv("DB_CHARSET", "utf8mb4"),
}

# SSL configuration
DB_SSL = os.getenv("DB_SSL", "false").lower() == "true"
DB_SSL_CA = os.getenv("DB_SSL_CA", "")  # Path to CA certificate
DB_SSL_CERT = os.getenv("DB_SSL_CERT", "")  # Path to client certificate
DB_SSL_KEY = os.getenv("DB_SSL_KEY", "")  # Path to client private key
DB_SSL_VERIFY_CERT = os.getenv("DB_SSL_VERIFY_CERT", "true").lower() == "true"
DB_SSL_VERIFY_IDENTITY = os.getenv("DB_SSL_VERIFY_IDENTITY", "false").lower() == "true"

# MCP configuration
READ_ONLY = os.getenv("MCP_READ_ONLY", "true").lower() == "true"
MAX_ROWS = int(os.getenv("MCP_MAX_ROWS", 1000))
MAX_POOL_SIZE = int(os.getenv("MCP_MAX_POOL_SIZE", 5))

# Create MCP server
mcp = FastMCP(
    name="mariadb-minimal",
    version="1.0.0"
)

# Database connection pool
db_pool = None


def create_ssl_context():
    """Create SSL context for secure database connections"""
    if not DB_SSL:
        return None

    ssl_context = ssl.create_default_context()

    if DB_SSL_CA:
        ca_path = os.path.expanduser(DB_SSL_CA)
        if os.path.exists(ca_path):
            ssl_context.load_verify_locations(cafile=ca_path)
            logger.info(f"Loaded CA certificate from: {ca_path}")
        else:
            logger.warning(f"CA certificate file not found: {ca_path}")

    if DB_SSL_CERT and DB_SSL_KEY:
        cert_path = os.path.expanduser(DB_SSL_CERT)
        key_path = os.path.expanduser(DB_SSL_KEY)

        if os.path.exists(cert_path) and os.path.exists(key_path):
            ssl_context.load_cert_chain(cert_path, key_path)
            logger.info(f"Loaded client certificate from: {cert_path}")
        else:
            logger.warning(f"Client certificate or key not found: cert={cert_path}, key={key_path}")

    if not DB_SSL_VERIFY_CERT:
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        logger.info("SSL certificate verification disabled")
    elif not DB_SSL_VERIFY_IDENTITY:
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        logger.info("SSL certificate verification enabled, hostname verification disabled")
    else:
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        logger.info("Full SSL verification enabled")

    return ssl_context


@asynccontextmanager
async def get_db_connection():
    """Get a database connection from the pool"""
    global db_pool

    if db_pool is None:
        pool_params = {
            "host": DB_CONFIG["host"],
            "port": DB_CONFIG["port"],
            "user": DB_CONFIG["user"],
            "password": DB_CONFIG["password"],
            "db": DB_CONFIG["database"],
            "charset": DB_CONFIG["charset"],
            "maxsize": MAX_POOL_SIZE,
            "minsize": 1,
            "autocommit": True,
        }

        # Add SSL context if configured
        ssl_context = create_ssl_context()
        if ssl_context:
            pool_params["ssl"] = ssl_context
            logger.info("Creating connection pool with SSL enabled")
        else:
            logger.info("Creating connection pool without SSL")

        db_pool = await asyncmy.create_pool(**pool_params)

    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            yield cursor


class QueryResult(BaseModel):
    """Model for query results"""
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    query: str


async def execute_query(query: str, params: Optional[tuple] = None, database: Optional[str] = None) -> QueryResult:
    """Execute a database query safely"""
    # Check for read-only mode
    if READ_ONLY:
        # Simple check for write operations
        query_upper = query.upper().strip()
        write_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "GRANT", "REVOKE"]
        if any(query_upper.startswith(kw) for kw in write_keywords):
            raise ValueError(f"Write operations not allowed in read-only mode: {query[:50]}...")

    async with get_db_connection() as cursor:
        # If database is specified, select it first
        if database:
            await cursor.execute(f"USE `{database}`")

        # Execute the actual query
        await cursor.execute(query, params)

        # Get column names
        columns = [desc[0] for desc in cursor.description] if cursor.description else []

        # Fetch results (limited by MAX_ROWS)
        if cursor.description:
            rows = await cursor.fetchmany(MAX_ROWS)
        else:
            rows = []

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            query=query
        )


@mcp.tool(
    name="list_databases",
    description="List all databases in the MariaDB server"
)
async def list_databases() -> Dict[str, Any]:
    """List all databases"""
    try:
        result = await execute_query("SHOW DATABASES")
        databases = [row[0] for row in result.rows]

        return {
            "success": True,
            "databases": databases,
            "count": len(databases)
        }
    except Exception as e:
        logger.error(f"Error listing databases: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="list_tables",
    description="List all tables in a database"
)
async def list_tables(database: str) -> Dict[str, Any]:
    """List all tables in a specific database"""
    try:
        result = await execute_query(f"SHOW TABLES FROM `{database}`")
        tables = [row[0] for row in result.rows]

        return {
            "success": True,
            "database": database,
            "tables": tables,
            "count": len(tables)
        }
    except Exception as e:
        logger.error(f"Error listing tables in {database}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="describe_table",
    description="Get the schema of a table"
)
async def describe_table(database: str, table: str) -> Dict[str, Any]:
    """Describe a table's structure"""
    try:
        # Get column information
        column_result = await execute_query(
            f"SHOW COLUMNS FROM `{database}`.`{table}`"
        )

        columns = []
        for row in column_result.rows:
            columns.append({
                "field": row[0],
                "type": row[1],
                "null": row[2],
                "key": row[3],
                "default": row[4],
                "extra": row[5] if len(row) > 5 else None
            })

        # Get indexes
        index_result = await execute_query(
            f"SHOW INDEX FROM `{database}`.`{table}`"
        )

        indexes = []
        for row in index_result.rows:
            indexes.append({
                "key_name": row[2],
                "column_name": row[4],
                "non_unique": row[1],
                "seq_in_index": row[3]
            })

        return {
            "success": True,
            "database": database,
            "table": table,
            "columns": columns,
            "indexes": indexes
        }
    except Exception as e:
        logger.error(f"Error describing table {database}.{table}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="execute_query",
    description="Execute a SQL query on the database"
)
async def execute_sql_query(query: str, database: Optional[str] = None) -> Dict[str, Any]:
    """Execute a SQL query"""
    try:
        # Pass the database parameter to execute_query
        result = await execute_query(query, database=database)

        # Format response
        response = {
            "success": True,
            "query": query,
            "columns": result.columns,
            "row_count": result.row_count
        }

        # Include rows if there are any
        if result.rows:
            # Convert rows to list of dicts for better readability
            rows_as_dicts = []
            for row in result.rows:
                row_dict = {}
                for i, col in enumerate(result.columns):
                    value = row[i]
                    # Convert bytes to string if needed
                    if isinstance(value, bytes):
                        value = value.decode('utf-8', errors='ignore')
                    row_dict[col] = value
                rows_as_dicts.append(row_dict)

            response["rows"] = rows_as_dicts

            # Add warning if results were truncated
            if result.row_count >= MAX_ROWS:
                response["warning"] = f"Results limited to {MAX_ROWS} rows"

        return response

    except Exception as e:
        logger.error(f"Error executing query: {e}")
        return {
            "success": False,
            "error": str(e),
            "query": query[:200] + "..." if len(query) > 200 else query
        }


@mcp.tool(
    name="show_process_list",
    description="Show current database connections and running queries"
)
async def show_process_list() -> Dict[str, Any]:
    """Show current processes"""
    try:
        result = await execute_query("SHOW FULL PROCESSLIST")

        processes = []
        for row in result.rows:
            processes.append({
                "id": row[0],
                "user": row[1],
                "host": row[2],
                "database": row[3],
                "command": row[4],
                "time": row[5],
                "state": row[6],
                "info": row[7] if len(row) > 7 else None
            })

        return {
            "success": True,
            "processes": processes,
            "count": len(processes)
        }
    except Exception as e:
        logger.error(f"Error getting process list: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="show_innodb_status",
    description="Get InnoDB engine status including deadlock information"
)
async def show_innodb_status() -> Dict[str, Any]:
    """Show InnoDB status for deadlock debugging"""
    try:
        result = await execute_query("SHOW ENGINE INNODB STATUS")

        if result.rows and len(result.rows) > 0:
            status_text = result.rows[0][2] if len(result.rows[0]) > 2 else str(result.rows[0])

            # Parse for deadlock information
            deadlock_info = None
            if "LATEST DETECTED DEADLOCK" in status_text:
                lines = status_text.split('\n')
                deadlock_start = False
                deadlock_lines = []

                for line in lines:
                    if "LATEST DETECTED DEADLOCK" in line:
                        deadlock_start = True
                    elif deadlock_start and line.startswith("---"):
                        break
                    elif deadlock_start:
                        deadlock_lines.append(line)

                if deadlock_lines:
                    deadlock_info = '\n'.join(deadlock_lines[:50])  # Limit lines

            return {
                "success": True,
                "status": status_text[:5000],  # Limit output
                "has_deadlock": deadlock_info is not None,
                "deadlock_info": deadlock_info
            }
        else:
            return {
                "success": True,
                "status": "No InnoDB status available"
            }

    except Exception as e:
        logger.error(f"Error getting InnoDB status: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="show_variables",
    description="Show MariaDB system variables"
)
async def show_variables(pattern: Optional[str] = None) -> Dict[str, Any]:
    """Show system variables"""
    try:
        if pattern:
            query = f"SHOW VARIABLES LIKE '%{pattern}%'"
        else:
            query = "SHOW VARIABLES"

        result = await execute_query(query)

        variables = {}
        for row in result.rows:
            variables[row[0]] = row[1]

        return {
            "success": True,
            "variables": variables,
            "count": len(variables)
        }
    except Exception as e:
        logger.error(f"Error getting variables: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="show_status",
    description="Show MariaDB server status"
)
async def show_status(pattern: Optional[str] = None) -> Dict[str, Any]:
    """Show server status"""
    try:
        if pattern:
            query = f"SHOW STATUS LIKE '%{pattern}%'"
        else:
            query = "SHOW STATUS"

        result = await execute_query(query)

        status = {}
        for row in result.rows:
            status[row[0]] = row[1]

        return {
            "success": True,
            "status": status,
            "count": len(status)
        }
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(
    name="analyze_slow_queries",
    description="Analyze slow queries from performance schema"
)
async def analyze_slow_queries(limit: int = 10) -> Dict[str, Any]:
    """Analyze slow queries"""
    try:
        # Check if performance_schema is enabled
        check_result = await execute_query(
            "SELECT VARIABLE_VALUE FROM performance_schema.global_variables WHERE VARIABLE_NAME = 'performance_schema'"
        )

        if not check_result.rows or check_result.rows[0][0] != 'ON':
            return {
                "success": False,
                "error": "Performance schema is not enabled"
            }

        # Get slow queries
        query = """
        SELECT
            DIGEST_TEXT as query_pattern,
            COUNT_STAR as exec_count,
            ROUND(SUM_TIMER_WAIT/1000000000000, 2) as total_time_sec,
            ROUND(AVG_TIMER_WAIT/1000000000000, 2) as avg_time_sec,
            ROUND(MAX_TIMER_WAIT/1000000000000, 2) as max_time_sec,
            FIRST_SEEN,
            LAST_SEEN
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT IS NOT NULL
        ORDER BY SUM_TIMER_WAIT DESC
        LIMIT %s
        """

        result = await execute_query(query, (limit,))

        slow_queries = []
        for row in result.rows:
            slow_queries.append({
                "query_pattern": row[0][:200] if row[0] else None,
                "exec_count": row[1],
                "total_time_sec": float(row[2]) if row[2] else 0,
                "avg_time_sec": float(row[3]) if row[3] else 0,
                "max_time_sec": float(row[4]) if row[4] else 0,
                "first_seen": str(row[5]) if row[5] else None,
                "last_seen": str(row[6]) if row[6] else None
            })

        return {
            "success": True,
            "slow_queries": slow_queries,
            "count": len(slow_queries)
        }
    except Exception as e:
        logger.error(f"Error analyzing slow queries: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# Cleanup on shutdown
async def cleanup():
    """Clean up database connections"""
    global db_pool
    if db_pool:
        db_pool.close()
        await db_pool.wait_closed()
        db_pool = None


# Main entry point
if __name__ == "__main__":
    import sys
    import uvicorn

    # Log configuration
    logger.info(f"Starting MariaDB Minimal MCP Server")
    logger.info(f"Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    logger.info(f"Read-only mode: {READ_ONLY}")
    logger.info(f"Max rows: {MAX_ROWS}")
    logger.info(f"SSL enabled: {DB_SSL}")
    if DB_SSL:
        logger.info(f"  CA certificate: {DB_SSL_CA if DB_SSL_CA else 'Not configured'}")
        logger.info(f"  Client certificate: {DB_SSL_CERT if DB_SSL_CERT else 'Not configured'}")
        logger.info(f"  Verify certificate: {DB_SSL_VERIFY_CERT}")
        logger.info(f"  Verify hostname: {DB_SSL_VERIFY_IDENTITY}")

    # Parse command line arguments for transport mode
    if "--transport" in sys.argv and "http" in sys.argv:
        # Run in HTTP mode
        logger.info("Starting in HTTP transport mode")
        host = "0.0.0.0"
        port = 8000

        # Parse host and port from command line if provided
        if "--host" in sys.argv:
            host_idx = sys.argv.index("--host") + 1
            if host_idx < len(sys.argv):
                host = sys.argv[host_idx]

        if "--port" in sys.argv:
            port_idx = sys.argv.index("--port") + 1
            if port_idx < len(sys.argv):
                port = int(sys.argv[port_idx])

        # Run with uvicorn for HTTP transport
        uvicorn.run(
            mcp.http_app(),
            host=host,
            port=port,
            log_level="info"
        )
    else:
        # Default to stdio
        mcp.run()