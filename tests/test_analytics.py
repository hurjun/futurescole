"""Tests for the analytics aggregation queries.

These mirror the four queries in ``analysis/queries.sql`` (event count by type,
top users, hourly distribution, error ratio). The production queries target
PostgreSQL; here the same aggregation logic is run against an in-memory SQLite
database and cross-checked against an independent pure-Python computation, so the
query semantics are validated without a live Postgres container.

Dialect notes vs. the PostgreSQL originals:
    DATE_TRUNC('hour', ts)            -> strftime('%Y-%m-%d %H:00:00', ts)
    COUNT(*) FILTER (WHERE ...)       -> identical (SQLite supports FILTER >= 3.30)
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter


def _python_count_by_type(events: list[dict]) -> dict[str, int]:
    return dict(Counter(e["event_type"] for e in events))


def test_event_count_by_type_matches_python(
    loaded_conn: sqlite3.Connection, sample_events: list[dict]
) -> None:
    rows = loaded_conn.execute(
        "SELECT event_type, COUNT(*) AS cnt FROM events GROUP BY event_type ORDER BY cnt DESC"
    ).fetchall()
    result = {event_type: cnt for event_type, cnt in rows}

    expected = _python_count_by_type(sample_events)
    assert result == expected
    assert sum(result.values()) == len(sample_events)
    assert set(result) <= {"page_view", "purchase", "error"}
    # Counts must be returned in descending order.
    counts = [cnt for _, cnt in rows]
    assert counts == sorted(counts, reverse=True)


def test_top_users_query(loaded_conn: sqlite3.Connection, sample_events: list[dict]) -> None:
    rows = loaded_conn.execute(
        "SELECT user_id, COUNT(*) AS cnt FROM events "
        "GROUP BY user_id ORDER BY cnt DESC LIMIT 10"
    ).fetchall()

    assert len(rows) <= 10
    counts = [cnt for _, cnt in rows]
    assert counts == sorted(counts, reverse=True)

    expected = Counter(e["user_id"] for e in sample_events)
    # The top row must be the genuine most-active user.
    assert rows[0][1] == max(expected.values())
    # Every returned count agrees with the independent tally.
    for user_id, cnt in rows:
        assert expected[user_id] == cnt


def test_hourly_distribution_query(
    loaded_conn: sqlite3.Connection, sample_events: list[dict]
) -> None:
    rows = loaded_conn.execute(
        "SELECT strftime('%Y-%m-%d %H:00:00', timestamp) AS hour, COUNT(*) AS cnt "
        "FROM events GROUP BY hour ORDER BY hour"
    ).fetchall()

    result = {hour: cnt for hour, cnt in rows}
    expected = Counter(
        e["timestamp"].strftime("%Y-%m-%d %H:00:00") for e in sample_events
    )
    assert result == dict(expected)
    # Every event falls into exactly one hourly bucket.
    assert sum(result.values()) == len(sample_events)
    # Buckets must be ordered chronologically.
    hours = [hour for hour, _ in rows]
    assert hours == sorted(hours)


def test_error_ratio_query(loaded_conn: sqlite3.Connection, sample_events: list[dict]) -> None:
    row = loaded_conn.execute(
        """
        SELECT
            COUNT(*) AS total_events,
            COUNT(*) FILTER (WHERE event_type = 'error') AS error_events,
            ROUND(
                COUNT(*) FILTER (WHERE event_type = 'error') * 100.0
                / NULLIF(COUNT(*), 0), 2
            ) AS error_ratio_pct
        FROM events
        """
    ).fetchone()
    total, error_events, ratio = row

    py_total = len(sample_events)
    py_errors = sum(1 for e in sample_events if e["event_type"] == "error")
    assert total == py_total
    assert error_events == py_errors
    assert ratio == round(py_errors * 100.0 / py_total, 2)


def test_properties_jsonb_is_queryable(loaded_conn: sqlite3.Connection) -> None:
    """The JSON properties payload supports per-type field extraction."""
    rows = loaded_conn.execute(
        "SELECT properties FROM events WHERE event_type = 'purchase' LIMIT 50"
    ).fetchall()
    amounts = [json.loads(p)["amount_krw"] for (p,) in rows]
    assert amounts  # there should be purchases in a 3000-event sample
    assert all(1_000 <= a <= 500_000 for a in amounts)


def test_analytics_handles_empty_table(sqlite_conn: sqlite3.Connection) -> None:
    """The error-ratio query is null-safe on an empty dataset (NULLIF guard)."""
    row = sqlite_conn.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE event_type='error') * 100.0 "
        "/ NULLIF(COUNT(*), 0) FROM events"
    ).fetchone()
    assert row[0] == 0
    assert row[1] is None
