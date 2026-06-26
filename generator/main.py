"""Event generator: simulates realistic web-service traffic and persists to PostgreSQL.

The event-synthesis logic in this module is intentionally free of any database
dependency: ``psycopg2`` is imported lazily inside the persistence helpers only.
This keeps the probabilistic generation functions importable (and unit-testable)
without a live PostgreSQL instance or the native driver installed.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from faker import Faker

if TYPE_CHECKING:  # pragma: no cover - imported only for type checking
    import psycopg2.extensions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

fake = Faker()

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

PAGE_PATHS: list[str] = [
    "/", "/products", "/products/detail", "/cart", "/checkout", "/mypage", "/search",
]
PAYMENT_METHODS: list[str] = ["card", "kakao_pay", "naver_pay", "toss"]
ERROR_CODES: list[str] = ["500", "404", "403", "502", "timeout"]

# Reuse a fixed user pool to simulate repeat-visitor patterns.
USER_POOL_SIZE: int = 50

# Spread events over the past 7 days.
EVENT_WINDOW_DAYS: int = 7

# Peak hours: 09:00–18:00 KST (00:00–09:00 UTC).
# Events in this range are 4× more likely than off-peak hours.
PEAK_UTC_START: int = 0   # 09 KST = 00 UTC
PEAK_UTC_END: int = 9     # 18 KST = 09 UTC


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

def seed_all(seed: int) -> None:
    """Seed every source of randomness for deterministic generation.

    Seeding both the stdlib ``random`` module and Faker makes a run fully
    reproducible, which is what the test-suite relies on and what lets a
    reviewer regenerate the exact same dataset.

    Args:
        seed: Integer seed applied to ``random`` and Faker.
    """
    random.seed(seed)
    Faker.seed(seed)
    fake.seed_instance(seed)


def _uuid() -> str:
    """Return a UUID4 string drawn from the seeded ``random`` source.

    ``uuid.uuid4()`` reads from ``os.urandom`` and so ignores ``random.seed``;
    deriving the UUID from ``random.getrandbits`` keeps generated ids fully
    reproducible when a seed is set.
    """
    return str(uuid.UUID(int=random.getrandbits(128), version=4))


def _hex_token(n_bytes: int) -> str:
    """Return a reproducible lowercase hex token of *n_bytes* bytes."""
    return f"{random.getrandbits(n_bytes * 8):0{n_bytes * 2}x}"


# ---------------------------------------------------------------------------
# Session-based event simulation
# ---------------------------------------------------------------------------
# Each session follows a realistic user journey:
#   1. One or more page views (browsing)
#   2. Optionally a purchase (conversion)
#   3. Possibly an error at any step

def _weighted_hour() -> int:
    """Return an hour (0–23 UTC) biased toward Korean business hours."""
    if random.random() < 0.70:
        return random.randint(PEAK_UTC_START, PEAK_UTC_END - 1)
    return random.randint(0, 23)


def _session_timestamp(base: datetime) -> datetime:
    """Return a random timestamp within the past EVENT_WINDOW_DAYS days.

    The hour component is skewed toward peak traffic hours.
    """
    day_offset = random.randint(0, EVENT_WINDOW_DAYS - 1)
    hour = _weighted_hour()
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return base.replace(hour=hour, minute=minute, second=second) - timedelta(days=day_offset)


def _page_view_properties() -> dict[str, Any]:
    return {
        "page_path": random.choice(PAGE_PATHS),
        "referrer": fake.uri() if random.random() > 0.4 else "",
        "duration_ms": random.randint(200, 30_000),
    }


def _purchase_properties() -> dict[str, Any]:
    return {
        "item_id": _uuid(),
        "amount_krw": random.randint(1_000, 500_000),
        "payment_method": random.choice(PAYMENT_METHODS),
    }


def _error_properties() -> dict[str, Any]:
    return {
        "error_code": random.choice(ERROR_CODES),
        "message": fake.sentence(),
        "stack_trace_hash": _hex_token(8),
    }


_PROPERTY_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "page_view": _page_view_properties,
    "purchase": _purchase_properties,
    "error": _error_properties,
}


def _make_event(
    event_type: str,
    user_id: str,
    session_id: str,
    ts: datetime,
) -> dict[str, Any]:
    """Assemble a single event dict ready for DB insertion."""
    return {
        "event_type": event_type,
        "user_id": user_id,
        "session_id": session_id,
        "timestamp": ts,
        "properties": json.dumps(_PROPERTY_BUILDERS[event_type]()),
    }


def _generate_session(
    user_id: str,
    base_ts: datetime,
) -> list[dict[str, Any]]:
    """Simulate one user session and return its events.

    A session always starts with 1–5 page views.
    With 20 % probability the user converts (purchase).
    With 10 % probability an error occurs during the session.
    Event timestamps are spaced a few seconds apart within the session.
    """
    session_id = _uuid()
    events: list[dict[str, Any]] = []
    ts = base_ts

    # Browsing phase: 1–5 page views
    num_page_views = random.randint(1, 5)
    for _ in range(num_page_views):
        events.append(_make_event("page_view", user_id, session_id, ts))
        ts += timedelta(seconds=random.randint(5, 120))

    # Conversion phase: 20 % chance of purchase
    if random.random() < 0.20:
        events.append(_make_event("purchase", user_id, session_id, ts))
        ts += timedelta(seconds=random.randint(5, 30))

    # Error phase: 10 % chance of error anywhere in the session
    if random.random() < 0.10:
        span_seconds = int((ts - base_ts).total_seconds()) or 1
        error_ts = base_ts + timedelta(seconds=random.randint(0, span_seconds))
        events.append(_make_event("error", user_id, session_id, error_ts))

    return events


def generate_events(target_count: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Generate approximately *target_count* events via session simulation.

    Sessions are generated until the total event count reaches *target_count*.
    The actual count may slightly exceed the target because sessions are
    atomic units — a session is never cut mid-way.

    Args:
        target_count: Approximate number of events to generate.
        now: Reference "current time" the 7-day event window is measured back
            from. Defaults to the wall clock; pass a fixed value (together with
            ``seed_all``) for a fully reproducible dataset.

    Returns:
        List of event dicts ready for bulk-insert.
    """
    if now is None:
        now = datetime.now(UTC)
    user_pool = [_uuid() for _ in range(USER_POOL_SIZE)]

    all_events: list[dict[str, Any]] = []
    while len(all_events) < target_count:
        user_id = random.choice(user_pool)
        base_ts = _session_timestamp(now)
        session_events = _generate_session(user_id, base_ts)
        all_events.extend(session_events)

    logger.info("Generated %d events across ~%d sessions", len(all_events), len(all_events) // 3)
    return all_events


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
        backoff_base: Sleep multiplier — actual sleep is attempt × backoff_base seconds.

    Raises:
        RuntimeError: If all retry attempts are exhausted.
    """
    import psycopg2  # lazy import: only required when actually persisting

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
                logger.info("Retrying in %.1f s...", sleep_sec)
                time.sleep(sleep_sec)

    raise RuntimeError(f"Could not connect to PostgreSQL after {retries} attempts.")


def insert_events(
    conn: psycopg2.extensions.connection,
    events: list[dict[str, Any]],
) -> None:
    """Bulk-insert events with a single executemany call.

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
    logger.info("Inserted %d events into the database", len(events))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Generate session-based events and persist them to PostgreSQL."""
    seed_env = os.environ.get("SEED")
    if seed_env is not None:
        seed_all(int(seed_env))
        logger.info("Deterministic mode enabled (SEED=%s)", seed_env)

    target = int(os.environ.get("EVENT_COUNT", 1_000))
    logger.info("Target event count: %d", target)

    conn = get_db_connection()
    try:
        events = generate_events(target)
        insert_events(conn, events)
    finally:
        conn.close()

    logger.info("Generator finished successfully.")


if __name__ == "__main__":
    main()
