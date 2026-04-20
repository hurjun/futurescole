"""Visualizer: executes analysis queries and saves PNG charts to /output."""

import logging
import os
import time
from typing import Any

import matplotlib
matplotlib.use("Agg")  # headless rendering — no display required
import matplotlib.pyplot as plt
import psycopg2
import psycopg2.extensions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = "/output"

# Chart colour palette (consistent across plots)
COLOURS = ["#4C72B0", "#55A868", "#C44E52"]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db_connection(
    retries: int = 5,
    backoff_base: float = 2.0,
) -> psycopg2.extensions.connection:
    """Return a live psycopg2 connection, retrying with exponential backoff.

    Args:
        retries: Maximum number of connection attempts.
        backoff_base: Sleep multiplier between attempts.

    Raises:
        RuntimeError: If all retry attempts are exhausted.
    """
    dsn = {
        "host": os.environ["POSTGRES_HOST"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "dbname": os.environ["POSTGRES_DB"],
    }
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(**dsn)
            logger.info("DB connection established (attempt %d/%d)", attempt, retries)
            return conn
        except psycopg2.OperationalError as exc:
            logger.warning("Attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                sleep_sec = backoff_base * attempt
                logger.info("Retrying in %.1f seconds...", sleep_sec)
                time.sleep(sleep_sec)

    raise RuntimeError(f"Could not connect to PostgreSQL after {retries} attempts.")


def fetch(
    conn: psycopg2.extensions.connection,
    sql: str,
) -> list[tuple[Any, ...]]:
    """Execute *sql* and return all result rows.

    Args:
        conn: An open psycopg2 connection.
        sql: A SELECT statement to execute.

    Returns:
        List of row tuples.
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _save(fig: plt.Figure, filename: str) -> None:
    """Save *fig* as a PNG to OUTPUT_DIR and close it."""
    path = os.path.join(OUTPUT_DIR, filename)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    logger.info("Saved %s", path)


def plot_event_type_distribution(conn: psycopg2.extensions.connection) -> None:
    """Bar chart: total event count grouped by event_type.

    Args:
        conn: An open psycopg2 connection.
    """
    rows = fetch(
        conn,
        "SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY event_type",
    )
    labels = [r[0] for r in rows]
    counts = [r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(labels, counts, color=COLOURS[: len(labels)])
    ax.bar_label(bars, padding=3)
    ax.set_title("Event Count by Type")
    ax.set_xlabel("Event Type")
    ax.set_ylabel("Count")
    ax.set_ylim(0, max(counts) * 1.15)
    fig.tight_layout()
    _save(fig, "event_type_distribution.png")


def plot_hourly_trend(conn: psycopg2.extensions.connection) -> None:
    """Line chart: event volume aggregated per hour over the dataset window.

    Args:
        conn: An open psycopg2 connection.
    """
    rows = fetch(
        conn,
        """
        SELECT DATE_TRUNC('hour', timestamp) AS hour, COUNT(*) AS cnt
        FROM events
        GROUP BY hour
        ORDER BY hour
        """,
    )
    hours = [r[0] for r in rows]
    counts = [r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(hours, counts, marker="o", markersize=3, linewidth=1.5, color=COLOURS[0])
    ax.fill_between(hours, counts, alpha=0.15, color=COLOURS[0])
    ax.set_title("Hourly Event Trend")
    ax.set_xlabel("Hour (UTC)")
    ax.set_ylabel("Event Count")
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()
    _save(fig, "hourly_trend.png")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Connect to the database, render all charts, and exit."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = get_db_connection()
    try:
        plot_event_type_distribution(conn)
        plot_hourly_trend(conn)
    finally:
        conn.close()
    logger.info("Visualizer finished successfully.")


if __name__ == "__main__":
    main()
