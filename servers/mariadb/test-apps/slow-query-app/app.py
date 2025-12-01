#!/usr/bin/env python3
"""
Slow Query Generator Application
This app intentionally runs inefficient queries for testing Holmes troubleshooting
"""

import os
import time
import random
import logging
import mysql.connector
from mysql.connector import Error

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'mariadb-service.mariadb.svc.cluster.local'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'app_user'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'testdb'),
    'connection_timeout': 30
}

# Query settings
QUERY_INTERVAL = int(os.getenv('QUERY_INTERVAL', '15'))  # seconds between queries
SLOW_QUERY_TYPES = ['full_scan', 'missing_index', 'cartesian', 'subquery', 'wildcard']


class SlowQueryGenerator:
    """Generate various types of slow queries"""

    def __init__(self):
        self.running = True
        self.query_count = 0
        self.slow_query_count = 0

    def create_connection(self):
        """Create a database connection"""
        try:
            return mysql.connector.connect(**DB_CONFIG)
        except Error as e:
            logger.error(f"Failed to connect to database: {e}")
            return None

    def run_full_table_scan(self, cursor):
        """Query without using indexes - full table scan"""
        logger.info("Running FULL TABLE SCAN query...")
        start_time = time.time()

        query = """
            SELECT a.*, COUNT(*)
            FROM audit_log a
            WHERE a.details LIKE '%action%'
            GROUP BY a.log_id
            ORDER BY a.created_at DESC
        """

        cursor.execute(query)
        results = cursor.fetchall()

        duration = time.time() - start_time
        logger.warning(f"Full table scan completed in {duration:.2f} seconds. Rows: {len(results)}")
        return duration

    def run_missing_index_query(self, cursor):
        """Query on columns without indexes"""
        logger.info("Running MISSING INDEX query...")
        start_time = time.time()

        # Query on audit_log which has no indexes except primary key
        query = """
            SELECT *
            FROM audit_log
            WHERE action = 'Action_50'
              AND user_id = 3
              AND ip_address LIKE '192.%'
            ORDER BY created_at DESC
        """

        cursor.execute(query)
        results = cursor.fetchall()

        duration = time.time() - start_time
        logger.warning(f"Missing index query completed in {duration:.2f} seconds. Rows: {len(results)}")
        return duration

    def run_cartesian_join(self, cursor):
        """Cartesian product join (very inefficient)"""
        logger.info("Running CARTESIAN JOIN query...")
        start_time = time.time()

        query = """
            SELECT COUNT(*)
            FROM customers c1, customers c2, products p
            WHERE c1.customer_id != c2.customer_id
              AND p.price > 10
        """

        cursor.execute(query)
        result = cursor.fetchone()

        duration = time.time() - start_time
        logger.warning(f"Cartesian join completed in {duration:.2f} seconds. Count: {result[0]}")
        return duration

    def run_inefficient_subquery(self, cursor):
        """Correlated subquery that runs for each row"""
        logger.info("Running INEFFICIENT SUBQUERY...")
        start_time = time.time()

        query = """
            SELECT c.*,
                   (SELECT COUNT(*)
                    FROM orders o
                    WHERE o.customer_id = c.customer_id) as order_count,
                   (SELECT SUM(oi.quantity * oi.price)
                    FROM order_items oi
                    JOIN orders o2 ON oi.order_id = o2.order_id
                    WHERE o2.customer_id = c.customer_id) as total_spent
            FROM customers c
            WHERE c.customer_id IN (
                SELECT DISTINCT customer_id
                FROM orders
                WHERE status != 'cancelled'
            )
        """

        cursor.execute(query)
        results = cursor.fetchall()

        duration = time.time() - start_time
        logger.warning(f"Inefficient subquery completed in {duration:.2f} seconds. Rows: {len(results)}")
        return duration

    def run_wildcard_search(self, cursor):
        """Leading wildcard search (cannot use index)"""
        logger.info("Running LEADING WILDCARD query...")
        start_time = time.time()

        query = """
            SELECT a1.*, a2.action
            FROM audit_log a1
            JOIN audit_log a2 ON a1.user_id = a2.user_id
            WHERE a1.details LIKE '%error%'
              AND a2.ip_address LIKE '%168%'
              AND a1.log_id != a2.log_id
            ORDER BY a1.created_at DESC
            LIMIT 100
        """

        cursor.execute(query)
        results = cursor.fetchall()

        duration = time.time() - start_time
        logger.warning(f"Wildcard search completed in {duration:.2f} seconds. Rows: {len(results)}")
        return duration

    def run_random_slow_query(self):
        """Run a random slow query"""
        conn = self.create_connection()
        if not conn:
            return

        cursor = conn.cursor()
        self.query_count += 1

        try:
            query_type = random.choice(SLOW_QUERY_TYPES)
            logger.info(f"Query #{self.query_count}: Running {query_type} query")

            duration = 0
            if query_type == 'full_scan':
                duration = self.run_full_table_scan(cursor)
            elif query_type == 'missing_index':
                duration = self.run_missing_index_query(cursor)
            elif query_type == 'cartesian':
                duration = self.run_cartesian_join(cursor)
            elif query_type == 'subquery':
                duration = self.run_inefficient_subquery(cursor)
            elif query_type == 'wildcard':
                duration = self.run_wildcard_search(cursor)

            if duration > 2:  # If query took more than 2 seconds
                self.slow_query_count += 1
                logger.error(f"SLOW QUERY DETECTED! Type: {query_type}, Duration: {duration:.2f}s")
                logger.error(f"Total slow queries: {self.slow_query_count}/{self.query_count}")

        except Error as e:
            logger.error(f"Query error: {e}")
        finally:
            cursor.close()
            conn.close()

    def check_database_health(self):
        """Check if database is accessible"""
        conn = self.create_connection()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
                conn.close()
                return True
            except:
                pass
        return False

    def run(self):
        """Main run loop"""
        logger.info("Slow Query Generator starting...")
        logger.info(f"Configuration: INTERVAL={QUERY_INTERVAL}s")

        # Wait for database to be ready
        while not self.check_database_health():
            logger.info("Waiting for database to be ready...")
            time.sleep(5)

        logger.info("Database is ready. Starting slow query generation...")

        # Add more data to audit_log for slower queries
        conn = self.create_connection()
        if conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM audit_log")
                count = cursor.fetchone()[0]
                if count < 10000:
                    logger.info("Adding more data to audit_log table...")
                    for i in range(10):
                        cursor.execute("""
                            INSERT INTO audit_log (action, user_id, details, ip_address, user_agent)
                            SELECT
                                CONCAT('Action_', FLOOR(RAND() * 100)),
                                FLOOR(RAND() * 5) + 1,
                                CONCAT('Details for action ', FLOOR(RAND() * 1000), ' with error code ', FLOOR(RAND() * 500)),
                                CONCAT(FLOOR(RAND() * 256), '.', FLOOR(RAND() * 256), '.', FLOOR(RAND() * 256), '.', FLOOR(RAND() * 256)),
                                'Mozilla/5.0'
                            FROM
                                (SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5) t1,
                                (SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5) t2,
                                (SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5) t3,
                                (SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5) t4
                        """)
                    conn.commit()
                    logger.info("Added more test data")
            except:
                pass
            finally:
                cursor.close()
                conn.close()

        while self.running:
            try:
                self.run_random_slow_query()

                # Wait before next query
                logger.info(f"Waiting {QUERY_INTERVAL} seconds before next query...")
                time.sleep(QUERY_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Received interrupt signal, shutting down...")
                self.running = False
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(5)

        logger.info(f"Slow Query Generator stopped. Total slow queries: {self.slow_query_count}/{self.query_count}")


if __name__ == "__main__":
    generator = SlowQueryGenerator()
    generator.run()