"""Tests for the transform/load path.

Two complementary checks:

1. ``insert_events`` is exercised against a fake DB-API cursor so the exact SQL
   and bound parameters are verified without any database.
2. The generated events are round-tripped through an in-memory SQLite table to
   confirm every row loads and the JSONB ``properties`` payload survives intact.
"""

from __future__ import annotations

import json
import sqlite3
from types import ModuleType

from conftest import load_events

SEED = 42

VALID_TYPES = {"page_view", "purchase", "error"}
EXPECTED_KEYS = {
    "page_view": {"page_path", "referrer", "duration_ms"},
    "purchase": {"item_id", "amount_krw", "payment_method"},
    "error": {"error_code", "message", "stack_trace_hash"},
}


class _FakeCursor:
    """Captures executemany calls so the load SQL can be asserted on."""

    def __init__(self) -> None:
        self.executed_sql: str | None = None
        self.executed_rows: list[dict] | None = None

    def executemany(self, sql: str, rows: list[dict]) -> None:
        self.executed_sql = sql
        self.executed_rows = list(rows)

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1


def test_insert_events_binds_all_columns(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    events = gen.generate_events(200)

    conn = _FakeConn()
    gen.insert_events(conn, events)

    cur = conn.cursor_obj
    assert conn.commits == 1
    assert cur.executed_rows == events  # every event handed to executemany once
    # All five business columns are present in the parameterised INSERT.
    for col in ("event_type", "user_id", "session_id", "timestamp", "properties"):
        assert f"%({col})s" in cur.executed_sql
    assert "INSERT INTO events" in cur.executed_sql


def test_insert_events_no_db_required_for_logic(gen: ModuleType) -> None:
    """The load helper must not need psycopg2 to be importable/callable."""
    conn = _FakeConn()
    gen.insert_events(conn, [])
    assert conn.commits == 1
    assert conn.cursor_obj.executed_rows == []


def test_sqlite_round_trip_preserves_rows(gen: ModuleType, sqlite_conn: sqlite3.Connection) -> None:
    gen.seed_all(SEED)
    events = gen.generate_events(1000)
    load_events(sqlite_conn, events)

    (count,) = sqlite_conn.execute("SELECT COUNT(*) FROM events").fetchone()
    assert count == len(events)

    # Every stored row has a valid type and a JSON properties payload whose keys
    # match the contract for that event type.
    for event_type, properties in sqlite_conn.execute(
        "SELECT event_type, properties FROM events"
    ):
        assert event_type in VALID_TYPES
        parsed = json.loads(properties)
        assert set(parsed) == EXPECTED_KEYS[event_type]
