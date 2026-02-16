"""
Seed a Prefect server with sample flow runs for testing the MCP integration.

Usage:
  # Against a local/port-forwarded Prefect server:
  PREFECT_API_URL=http://localhost:4200/api python seed-data.py

  # As a K8s Job (see seed-job.yaml)

Requires: pip install prefect
"""

from prefect import flow, task
import time
import random


@task
def extract_data(source: str):
    """Simulate data extraction from a source."""
    time.sleep(0.5)
    rows = random.randint(100, 10000)
    print(f"Extracted {rows} rows from {source}")
    return {"source": source, "rows": rows}


@task
def transform_data(data: dict):
    """Simulate data transformation."""
    time.sleep(0.3)
    data["transformed"] = True
    data["rows_after_filter"] = int(data["rows"] * 0.8)
    print(f"Transformed {data['rows']} -> {data['rows_after_filter']} rows")
    return data


@task
def load_data(data: dict, destination: str):
    """Simulate loading data to a destination."""
    time.sleep(0.2)
    print(f"Loaded {data['rows_after_filter']} rows to {destination}")
    return True


@flow(name="etl-pipeline")
def etl_pipeline(source: str = "postgres", destination: str = "warehouse"):
    """A sample ETL pipeline."""
    raw = extract_data(source)
    transformed = transform_data(raw)
    load_data(transformed, destination)
    return "ETL complete"


@flow(name="health-check")
def health_check():
    """Quick system health check."""
    print("All systems operational")
    return "healthy"


@flow(name="failing-pipeline")
def failing_pipeline():
    """A flow that always fails — useful for testing error investigation."""
    extract_data("broken-source")
    raise ValueError(
        "Connection refused: could not connect to source database. "
        "Check that the database is running and credentials are valid."
    )


@flow(name="data-sync")
def data_sync():
    """Sync data across multiple sources."""
    sources = ["users-db", "orders-db", "inventory-db"]
    for source in sources:
        raw = extract_data(source)
        transformed = transform_data(raw)
        load_data(transformed, f"{source}-replica")
    return f"Synced {len(sources)} sources"


if __name__ == "__main__":
    print("=== Seeding Prefect with sample flow runs ===\n")

    # Run successful flows
    print("--- Running ETL pipeline ---")
    etl_pipeline()

    print("\n--- Running health check ---")
    health_check()

    print("\n--- Running data sync ---")
    data_sync()

    # Run a flow that fails
    print("\n--- Running failing pipeline (expected failure) ---")
    try:
        failing_pipeline()
    except Exception as e:
        print(f"Failed as expected: {e}")

    # Run ETL a few more times for history
    print("\n--- Running ETL pipeline (additional runs for history) ---")
    for i in range(3):
        etl_pipeline(source=f"source-{i}", destination="warehouse")

    print("\n=== Done! Check Prefect UI or use the MCP server to query results ===")
