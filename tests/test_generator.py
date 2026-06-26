"""Unit tests for the probabilistic event-generation logic.

The generator models user behaviour statistically (session funnels, peak-hour
bias, a fixed user pool). These tests pin down both the structural invariants of
a single session and the aggregate distributions over a large seeded sample.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from types import ModuleType

SEED = 42


# ---------------------------------------------------------------------------
# Property builders
# ---------------------------------------------------------------------------

def test_property_builders_have_required_keys(gen: ModuleType) -> None:
    assert set(gen._page_view_properties()) == {"page_path", "referrer", "duration_ms"}
    assert set(gen._purchase_properties()) == {"item_id", "amount_krw", "payment_method"}
    assert set(gen._error_properties()) == {"error_code", "message", "stack_trace_hash"}


def test_property_value_domains(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    pv = gen._page_view_properties()
    assert pv["page_path"] in gen.PAGE_PATHS
    assert 200 <= pv["duration_ms"] <= 30_000

    pur = gen._purchase_properties()
    assert pur["payment_method"] in gen.PAYMENT_METHODS
    assert 1_000 <= pur["amount_krw"] <= 500_000

    err = gen._error_properties()
    assert err["error_code"] in gen.ERROR_CODES


def test_make_event_shape_and_json(gen: ModuleType) -> None:
    ts = datetime(2024, 1, 1, 12, tzinfo=UTC)
    event = gen._make_event("page_view", "u1", "s1", ts)
    assert set(event) == {"event_type", "user_id", "session_id", "timestamp", "properties"}
    assert event["event_type"] == "page_view"
    assert event["timestamp"] == ts
    # properties must be a JSON *string* ready for a JSONB column.
    assert isinstance(event["properties"], str)
    assert set(json.loads(event["properties"])) == {"page_path", "referrer", "duration_ms"}


# ---------------------------------------------------------------------------
# Weighted hour
# ---------------------------------------------------------------------------

def test_weighted_hour_in_range(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    assert all(0 <= gen._weighted_hour() <= 23 for _ in range(1000))


def test_weighted_hour_is_peak_biased(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    hours = [gen._weighted_hour() for _ in range(5000)]
    peak = sum(1 for h in hours if gen.PEAK_UTC_START <= h < gen.PEAK_UTC_END)
    # Theoretical peak share = 0.70 + 0.30 * (9/24) ~= 0.81.
    assert 0.74 < peak / len(hours) < 0.88


# ---------------------------------------------------------------------------
# Session structure
# ---------------------------------------------------------------------------

def test_session_starts_with_page_views(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    for _ in range(500):
        base = datetime.now(UTC)
        events = gen._generate_session("u1", base)
        page_views = [e for e in events if e["event_type"] == "page_view"]

        # 1-5 page views, and they must come first (browsing precedes conversion).
        assert 1 <= len(page_views) <= 5
        assert all(e["event_type"] == "page_view" for e in events[: len(page_views)])

        # At most one purchase and one error per session.
        assert sum(1 for e in events if e["event_type"] == "purchase") <= 1
        assert sum(1 for e in events if e["event_type"] == "error") <= 1

        # All events share one session id and the originating user id.
        assert {e["session_id"] for e in events} == {events[0]["session_id"]}
        assert {e["user_id"] for e in events} == {"u1"}


def test_page_view_timestamps_are_monotonic(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    base = datetime.now(UTC)
    for _ in range(200):
        events = gen._generate_session("u1", base)
        page_views = [e for e in events if e["event_type"] == "page_view"]
        ts = [e["timestamp"] for e in page_views]
        assert ts == sorted(ts)


# ---------------------------------------------------------------------------
# Aggregate generation
# ---------------------------------------------------------------------------

def test_generate_events_reaches_target_and_is_valid(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    target = 1000
    events = gen.generate_events(target)

    assert len(events) >= target  # sessions are atomic, so count may overshoot
    assert all(e["event_type"] in {"page_view", "purchase", "error"} for e in events)
    # The user pool is fixed, so distinct users cannot exceed its size.
    assert len({e["user_id"] for e in events}) <= gen.USER_POOL_SIZE


def test_session_funnel_distribution(gen: ModuleType) -> None:
    """Over many sessions the conversion / error rates hit their design targets."""
    gen.seed_all(SEED)
    events = gen.generate_events(9000)

    by_session: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_session[e["session_id"]].append(e)

    n_sessions = len(by_session)
    conversions = sum(
        1 for s in by_session.values() if any(e["event_type"] == "purchase" for e in s)
    )
    errors = sum(
        1 for s in by_session.values() if any(e["event_type"] == "error" for e in s)
    )
    avg_page_views = sum(
        sum(1 for e in s if e["event_type"] == "page_view") for s in by_session.values()
    ) / n_sessions

    assert 0.16 <= conversions / n_sessions <= 0.24  # design target ~20%
    assert 0.06 <= errors / n_sessions <= 0.14       # design target ~10%
    assert 2.7 <= avg_page_views <= 3.3              # 1-5 uniform -> mean 3


def test_generate_events_is_peak_hour_biased(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    events = gen.generate_events(9000)
    peak = sum(1 for e in events if gen.PEAK_UTC_START <= e["timestamp"].hour < gen.PEAK_UTC_END)
    assert peak / len(events) > 0.74


def test_event_timestamps_within_window(gen: ModuleType) -> None:
    gen.seed_all(SEED)
    now = datetime.now(UTC)
    events = gen.generate_events(2000)
    lower = now - timedelta(days=gen.EVENT_WINDOW_DAYS + 1)
    upper = now + timedelta(days=1)
    assert all(lower <= e["timestamp"] <= upper for e in events)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

def test_seed_makes_generation_deterministic(gen: ModuleType) -> None:
    now = datetime(2024, 6, 1, 12, tzinfo=UTC)  # fixed clock for full determinism

    gen.seed_all(SEED)
    first = gen.generate_events(1500, now=now)
    gen.seed_all(SEED)
    second = gen.generate_events(1500, now=now)

    assert len(first) == len(second)
    # With a seed and a fixed clock the dataset is reproducible byte-for-byte,
    # including the UUID-derived ids inside properties and the timestamps.
    assert [e["event_type"] for e in first] == [e["event_type"] for e in second]
    assert [e["timestamp"] for e in first] == [e["timestamp"] for e in second]
    assert [e["user_id"] for e in first] == [e["user_id"] for e in second]
    assert [e["session_id"] for e in first] == [e["session_id"] for e in second]
    assert [e["properties"] for e in first] == [e["properties"] for e in second]


def test_different_seeds_diverge(gen: ModuleType) -> None:
    now = datetime(2024, 6, 1, 12, tzinfo=UTC)
    gen.seed_all(1)
    a = gen.generate_events(1000, now=now)
    gen.seed_all(2)
    b = gen.generate_events(1000, now=now)
    assert [e["properties"] for e in a] != [e["properties"] for e in b]
