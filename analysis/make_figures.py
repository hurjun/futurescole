"""Render a sample analytics figure from the seeded generator, offline.

This is a small, dependency-light companion to the Dockerized ``visualizer``
service. Instead of a live PostgreSQL container it:

1. runs the real generator (:func:`generate_events` from ``generator/main.py``)
   under a fixed ``SEED`` so the dataset is reproducible,
2. loads the events into an in-memory SQLite DB whose schema mirrors
   ``db/init.sql``,
3. executes the same analytics aggregations shipped in ``analysis/queries.sql``
   (event count by type, and hourly traffic), and
4. saves a single small PNG that documents the result in the README.

The aggregation SQL below is the SQLite-dialect equivalent of the production
PostgreSQL queries (``DATE_TRUNC('hour', ts)`` -> ``strftime('%H', ts)``); the
test-suite (``tests/test_analytics.py``) cross-checks that this equivalence
holds. The chart palette matches the ``visualizer`` service.

Usage (from the repo root, with matplotlib + faker installed)::

    SEED=42 EVENT_COUNT=5000 python analysis/make_figures.py

Defaults to ``SEED=42`` and ``EVENT_COUNT=5000`` so the figure matches the
"Results" table in the README.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend, selected before importing pyplot

import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "generator" / "main.py"
OUTPUT_PATH = REPO_ROOT / "docs" / "sample_results.png"

# Same palette the visualizer service uses, for a consistent look.
COLOURS = ["#4C72B0", "#55A868", "#C44E52"]
PEAK_SHADE = "#4C72B0"

# Schema equivalent to db/init.sql in portable SQLite types.
SQLITE_SCHEMA = """
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    properties  TEXT NOT NULL
);
"""


def _load_generator():
    """Import ``generator/main.py`` without requiring psycopg2 to be installed."""
    spec = importlib.util.spec_from_file_location("event_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_dataset(seed: int, event_count: int) -> sqlite3.Connection:
    """Generate seeded events and load them into an in-memory SQLite DB."""
    gen = _load_generator()
    gen.seed_all(seed)
    events = gen.generate_events(event_count)

    conn = sqlite3.connect(":memory:")
    conn.executescript(SQLITE_SCHEMA)
    conn.executemany(
        "INSERT INTO events (event_type, user_id, session_id, timestamp, properties) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                e["event_type"],
                e["user_id"],
                e["session_id"],
                e["timestamp"].isoformat(),
                e["properties"],
            )
            for e in events
        ],
    )
    conn.commit()
    return conn


def _plot_event_type_distribution(ax: plt.Axes, conn: sqlite3.Connection) -> None:
    """Bar chart of event count by type (analysis/queries.sql #1)."""
    rows = conn.execute(
        "SELECT event_type, COUNT(*) AS cnt FROM events GROUP BY event_type ORDER BY cnt DESC"
    ).fetchall()
    labels = [r[0] for r in rows]
    counts = [r[1] for r in rows]

    bars = ax.bar(labels, counts, color=COLOURS[: len(labels)])
    ax.bar_label(bars, padding=3, fontsize=9)
    ax.set_title("Event Count by Type")
    ax.set_xlabel("Event type")
    ax.set_ylabel("Count")
    ax.set_ylim(0, max(counts) * 1.15)


def _plot_hourly_traffic(ax: plt.Axes, conn: sqlite3.Connection) -> None:
    """Bar chart of traffic by hour-of-day, highlighting the peak window.

    This is the wall-clock-independent reading of analysis/queries.sql #3
    (hourly distribution): bucketing by the hour component (0-23 UTC) instead of
    by absolute hour makes the figure reproducible across runs and surfaces the
    designed peak-hour bias (09:00-18:00 KST = 00:00-09:00 UTC).
    """
    rows = conn.execute(
        "SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS hour, COUNT(*) AS cnt "
        "FROM events GROUP BY hour ORDER BY hour"
    ).fetchall()
    by_hour = dict(rows)
    hours = list(range(24))
    counts = [by_hour.get(h, 0) for h in hours]

    peak = [0 <= h < 9 for h in hours]
    colors = [PEAK_SHADE if p else "#B0B0B0" for p in peak]
    ax.bar(hours, counts, color=colors, width=0.85)
    ax.axvspan(-0.5, 8.5, color=PEAK_SHADE, alpha=0.08)
    ax.set_title("Traffic by Hour of Day (UTC)")
    ax.set_xlabel("Hour of day (UTC)  -  shaded = peak 09:00-18:00 KST")
    ax.set_ylabel("Event count")
    ax.set_xticks(range(0, 24, 2))


def main() -> None:
    seed = int(os.environ.get("SEED", 42))
    event_count = int(os.environ.get("EVENT_COUNT", 5000))

    conn = _build_dataset(seed, event_count)
    (total,) = conn.execute("SELECT COUNT(*) FROM events").fetchone()

    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(11, 4.2))
    _plot_event_type_distribution(ax_left, conn)
    _plot_hourly_traffic(ax_right, conn)
    fig.suptitle(
        f"Seeded sample (SEED={seed}, {total:,} events) - reproducible",
        fontsize=11,
    )
    fig.tight_layout()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PATH, dpi=110)
    plt.close(fig)
    conn.close()
    print(f"Wrote {OUTPUT_PATH} ({total:,} events, SEED={seed})")


if __name__ == "__main__":
    main()
