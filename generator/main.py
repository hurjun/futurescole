"""Event generator: creates random events and bulk-inserts them into PostgreSQL."""

import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import psycopg2
import psycopg2.extensions
from faker import Faker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

fake = Faker()

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
EVENT_WEIGHTS: dict[str, int] = {"page_view": 60, "purchase": 25, "error": 15}
PAYMENT_METHODS: list[str] = ["card", "kakao_pay", "naver_pay", "toss"]
PAGE_PATHS: list[str] = ["/", "/products", "/cart", "/checkout", "/mypage", "/search"]
ERROR_CODES: list[str] = ["500", "404", "403", "502", "timeout"]

# Spread events over the past 7 days
EVENT_WINDOW_SECONDS: int = 86_400 * 7
# Reuse a small user pool to simulate realistic repeat-visitor patterns
USER_POOL_SIZE: int = 50


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _db_dsn() -> dict[str, str]:
    """Read DB connection parameters from environment variables."""
    return {
        "host": os.environ["POSTGRES_HOST"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "dbname": os.environ["POSTGRES_DB"],
    }


def get_db_connection(
    retries: int = 5,
    backoff_base: float = 2.0,
) -> psycopg2.extensions.connection:
    """Return a live psycopg2 connection, retrying with exponential backoff.

    Args:
        retries: Maximum number of connection attempts.
        backoff_base: Sleep multiplier between attempts (attempt * backoff_base).

    Raises:
        RuntimeError: If all retry attempts are exhausted.
    """
    dsn = _db_dsn()
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(**dsn)
            logger.info("DB connection established (attempt %d/%d)", attempt, retries)
            return conn
        except psycopg2.OperationalError as exc:
            logger.warning("Connection attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                sleep_sec = backoff_base * attempt
                logger.info("Retrying in %.1f seconds...", sleep_sec)
                time.sleep(sleep_sec)

    raise RuntimeError(f"Could not connect to PostgreSQL after {retries} attempts.")


def insert_events(
    conn: psycopg2.extensions.connection,
    events: list[dict[str, Any]],
) -> None:
    """Bulk-insert a list of event dicts using executemany.

    Args:
        conn: An open psycopg2 connection.
        events: List of event dicts matching the INSERT column order.
    """
    sql = """
        INSERT INTO events (event_type, user_id, session_id, timestamp, properties)
        VALUES (%(event_type)s, %(user_id)s, %(session_id)s, %(timestamp)s, %(properties)s)
    """
    with conn.cursor() as cur:
        cur.executemany(sql, events)
    conn.commit()
    logger.info("Inserted %d events", len(events))


# ---------------------------------------------------------------------------
# Event generation
# ---------------------------------------------------------------------------

def _page_view_properties() -> dict[str, Any]:
    return {
        "page_path": random.choice(PAGE_PATHS),
        "referrer": fake.uri() if random.random() > 0.4 else "",
        "duration_ms": random.randint(200, 30_000),
    }


def _purchase_properties() -> dict[str, Any]:
    return {
        "item_id": str(uuid.uuid4()),
        "amount_krw": random.randint(1_000, 500_000),
        "payment_method": random.choice(PAYMENT_METHODS),
    }


def _error_properties() -> dict[str, Any]:
    return {
        "error_code": random.choice(ERROR_CODES),
        "message": fake.sentence(),
        "stack_trace_hash": uuid.uuid4().hex[:16],
    }


_PROPERTY_BUILDERS = {
    "page_view": _page_view_properties,
    "purchase": _purchase_properties,
    "error": _error_properties,
}


def _make_properties(event_type: str) -> str:
    """Return a JSON-serialised properties payload for the given event type."""
    return json.dumps(_PROPERTY_BUILDERS[event_type]())


def _sample_event_type() -> str:
    types = list(EVENT_WEIGHTS.keys())
    weights = list(EVENT_WEIGHTS.values())
    return random.choices(types, weights=weights)[0]


def generate_events(count: int) -> list[dict[str, Any]]:
    """Generate *count* random events distributed over the past 7 days.

    Args:
        count: Number of events to generate.

    Returns:
        List of event dicts ready for bulk-insert.
    """
    now = datetime.now(timezone.utc)
    user_pool = [str(uuid.uuid4()) for _ in range(USER_POOL_SIZE)]

    events: list[dict[str, Any]] = []
    for _ in range(count):
        event_type = _sample_event_type()
        offset_sec = random.randint(0, EVENT_WINDOW_SECONDS)
        events.append(
            {
                "event_type": event_type,
                "user_id": random.choice(user_pool),
                "session_id": str(uuid.uuid4()),
                "timestamp": now - timedelta(seconds=offset_sec),
                "properties": _make_properties(event_type),
            }
        )
    return events


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Generate events and persist them to the database."""
    count = int(os.environ.get("EVENT_COUNT", 1_000))
    logger.info("Starting event generation: count=%d", count)

    conn = get_db_connection()
    try:
        events = generate_events(count)
        insert_events(conn, events)
    finally:
        conn.close()

    logger.info("Generator finished successfully.")


if __name__ == "__main__":
    main()
