"""Shared pytest fixtures.

These tests deliberately avoid the live PostgreSQL container. The event-synthesis
logic is pure Python, and the analytics queries are validated against an in-memory
SQLite database whose schema mirrors ``db/init.sql``. This keeps the whole suite
fast, hermetic and runnable in CI without Docker.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "generator" / "main.py"

# Schema equivalent to db/init.sql, expressed in portable SQLite types.
# (SERIAL -> INTEGER AUTOINCREMENT, TIMESTAMPTZ -> TEXT/ISO-8601, JSONB -> TEXT.)
SQLITE_SCHEMA = """
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    properties  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SEED = 42


def _load_module(name: str, path: Path) -> ModuleType:
    """Import a standalone service module by file path under a stable name."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def gen() -> ModuleType:
    """The generator module, loaded without requiring psycopg2/matplotlib."""
    return _load_module("event_generator", GENERATOR_PATH)


def load_events(conn: sqlite3.Connection, events: list[dict]) -> None:
    """Insert generated event dicts into the SQLite ``events`` table.

    This mirrors ``generator.insert_events`` but uses the SQLite paramstyle so the
    transform/load path can be exercised without a PostgreSQL driver.
    """
    rows = [
        (
            e["event_type"],
            e["user_id"],
            e["session_id"],
            e["timestamp"].isoformat(),
            e["properties"],
        )
        for e in events
    ]
    conn.executemany(
        "INSERT INTO events (event_type, user_id, session_id, timestamp, properties) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


@pytest.fixture
def sample_events(gen: ModuleType) -> list[dict]:
    """A deterministic batch of generated events (seeded for reproducibility)."""
    gen.seed_all(SEED)
    return gen.generate_events(3000)


@pytest.fixture
def sqlite_conn() -> sqlite3.Connection:
    """An empty in-memory SQLite DB with the events schema."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SQLITE_SCHEMA)
    yield conn
    conn.close()


@pytest.fixture
def loaded_conn(sqlite_conn: sqlite3.Connection, sample_events: list[dict]) -> sqlite3.Connection:
    """SQLite DB pre-loaded with the deterministic sample events."""
    load_events(sqlite_conn, sample_events)
    return sqlite_conn
