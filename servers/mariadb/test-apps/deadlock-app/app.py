#!/usr/bin/env python3
"""
Deadlock Generator Application
This app intentionally creates database deadlocks for testing Holmes troubleshooting
"""

import os
import time
import random
import threading
import logging
import mysql.connector
from mysql.connector import Error

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database configuration from environment
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'mariadb-service.mariadb.svc.cluster.local'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'app_user'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'testdb'),
    'autocommit': False,
    'connection_timeout': 10
}

# Deadlock generation settings
DEADLOCK_FREQUENCY = int(os.getenv('DEADLOCK_FREQUENCY', '10'))  # seconds between attempts
THREAD_COUNT = int(os.getenv('THREAD_COUNT', '2'))  # number of concurrent threads


class DeadlockGenerator:
    """Generate deadlocks by having multiple transactions update rows in different orders"""

    def __init__(self):
        self.running = True
        self.deadlock_count = 0
        self.attempt_count = 0

    def create_connection(self):
        """Create a database connection"""
        try:
            return mysql.connector.connect(**DB_CONFIG)
        except Error as e:
            logger.error(f"Failed to connect to database: {e}")
            return None

    def transaction_type_a(self):
        """Transaction that updates inventory then orders"""
        conn = self.create_connection()
        if not conn:
            return

        cursor = conn.cursor()
        try:
            # Start transaction
            conn.start_transaction()

            # Update inventory first (warehouse 1 then 2)
            cursor.execute("""
                UPDATE inventory
                SET quantity = quantity - 1
                WHERE product_id = 1 AND warehouse_id = 1
            """)

            # Small delay to increase chance of deadlock
            time.sleep(0.1)

            cursor.execute("""
                UPDATE inventory
                SET quantity = quantity - 1
                WHERE product_id = 1 AND warehouse_id = 2
            """)

            # Update orders
            cursor.execute("""
                UPDATE orders
                SET status = 'processing'
                WHERE order_id = 1
            """)

            # Commit transaction
            conn.commit()
            logger.info("Transaction Type A completed successfully")

        except Error as e:
            conn.rollback()
            if "Deadlock found" in str(e):
                self.deadlock_count += 1
                logger.warning(f"DEADLOCK detected in Type A transaction! Total deadlocks: {self.deadlock_count}")
            else:
                logger.error(f"Transaction Type A error: {e}")
        finally:
            cursor.close()
            conn.close()

    def transaction_type_b(self):
        """Transaction that updates orders then inventory (opposite order)"""
        conn = self.create_connection()
        if not conn:
            return

        cursor = conn.cursor()
        try:
            # Start transaction
            conn.start_transaction()

            # Update inventory in opposite order (warehouse 2 then 1)
            cursor.execute("""
                UPDATE inventory
                SET quantity = quantity + 1
                WHERE product_id = 1 AND warehouse_id = 2
            """)

            # Small delay to increase chance of deadlock
            time.sleep(0.1)

            cursor.execute("""
                UPDATE inventory
                SET quantity = quantity + 1
                WHERE product_id = 1 AND warehouse_id = 1
            """)

            # Update orders
            cursor.execute("""
                UPDATE orders
                SET status = 'pending'
                WHERE order_id = 1
            """)

            # Commit transaction
            conn.commit()
            logger.info("Transaction Type B completed successfully")

        except Error as e:
            conn.rollback()
            if "Deadlock found" in str(e):
                self.deadlock_count += 1
                logger.warning(f"DEADLOCK detected in Type B transaction! Total deadlocks: {self.deadlock_count}")
            else:
                logger.error(f"Transaction Type B error: {e}")
        finally:
            cursor.close()
            conn.close()

    def run_concurrent_transactions(self):
        """Run both transaction types concurrently to cause deadlocks"""
        self.attempt_count += 1
        logger.info(f"Starting deadlock attempt #{self.attempt_count}")

        # Create threads for concurrent transactions
        threads = []

        for i in range(THREAD_COUNT // 2):
            t1 = threading.Thread(target=self.transaction_type_a, name=f"TypeA-{i}")
            t2 = threading.Thread(target=self.transaction_type_b, name=f"TypeB-{i}")
            threads.extend([t1, t2])

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all threads to complete
        for t in threads:
            t.join()

        logger.info(f"Deadlock attempt #{self.attempt_count} completed. Total deadlocks: {self.deadlock_count}")

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
        logger.info("Deadlock Generator starting...")
        logger.info(f"Configuration: FREQUENCY={DEADLOCK_FREQUENCY}s, THREADS={THREAD_COUNT}")

        # Wait for database to be ready
        while not self.check_database_health():
            logger.info("Waiting for database to be ready...")
            time.sleep(5)

        logger.info("Database is ready. Starting deadlock generation...")

        while self.running:
            try:
                self.run_concurrent_transactions()

                # Wait before next attempt
                logger.info(f"Waiting {DEADLOCK_FREQUENCY} seconds before next attempt...")
                time.sleep(DEADLOCK_FREQUENCY)

            except KeyboardInterrupt:
                logger.info("Received interrupt signal, shutting down...")
                self.running = False
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(5)

        logger.info(f"Deadlock Generator stopped. Total deadlocks generated: {self.deadlock_count}")


if __name__ == "__main__":
    generator = DeadlockGenerator()
    generator.run()