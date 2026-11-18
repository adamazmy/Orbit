"""SQLite storage: the schema from PLAN.md section 4 and simple read/write helpers.

Observations are append-only: each run adds a fresh snapshot of who was seen
where, and nothing is overwritten. That history is what makes change detection
possible, and it doubles as an audit trail.

Uses the stdlib sqlite3 module directly (no ORM). Each function takes an open
connection; callers decide when to commit, so one firm's work can be saved as
a single transaction.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from orbit.models import FirmConfig, PartnerRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS firms (
    id TEXT PRIMARY KEY,
    name TEXT,
    directory_url TEXT,
    enabled INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT
);

-- One row per firm per run: health + status (OK | SUSPECT | FAILED | BLOCKED | SKIPPED).
CREATE TABLE IF NOT EXISTS firm_runs (
    run_id INTEGER,
    firm_id TEXT,
    status TEXT,
    partner_count INTEGER,
    message TEXT,
    PRIMARY KEY (run_id, firm_id)
);

-- The snapshot: who was seen where in each run. practices/offices are JSON lists.
CREATE TABLE IF NOT EXISTS observations (
    run_id INTEGER,
    firm_id TEXT,
    person_key TEXT,
    full_name TEXT,
    name_norm TEXT,
    title TEXT,
    practices TEXT,
    offices TEXT,
    profile_url TEXT
);

-- Every diff reads one firm's snapshot for one run, so index that lookup.
CREATE INDEX IF NOT EXISTS idx_observations_run_firm ON observations (run_id, firm_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER,
    type TEXT,             -- JOINED | LEFT | MOVED | OFFICE_CHANGED | TITLE_CHANGED
    name TEXT,
    from_firm TEXT,
    to_firm TEXT,
    details TEXT,          -- JSON (old/new office etc.)
    confidence REAL,       -- for MOVED (fuzzy name match)
    needs_review INTEGER   -- 1 if confidence below the auto threshold
);
"""


def _now() -> str:
    """Current UTC time as an ISO 8601 string (sortable, timezone-explicit)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database, creating the file, its folder and the schema if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_firms(conn: sqlite3.Connection, firms: list[FirmConfig]) -> None:
    """Mirror the firm list from config into the firms table."""
    conn.executemany(
        """
        INSERT INTO firms (id, name, directory_url, enabled) VALUES (?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            name = excluded.name,
            directory_url = excluded.directory_url,
            enabled = excluded.enabled
        """,
        [(f.id, f.name, f.directory_url, int(f.enabled)) for f in firms],
    )


def create_run(conn: sqlite3.Connection) -> int:
    """Start a new run and return its id."""
    cursor = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_now(),))
    return cursor.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int) -> None:
    """Stamp a run as finished."""
    conn.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (_now(), run_id))


def record_firm_run(
    conn: sqlite3.Connection,
    run_id: int,
    firm_id: str,
    status: str,
    partner_count: int,
    message: str = "",
) -> None:
    """Save one firm's outcome for a run (status, partner count, explanation)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO firm_runs (run_id, firm_id, status, partner_count, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, firm_id, status, partner_count, message),
    )


def save_observations(
    conn: sqlite3.Connection,
    run_id: int,
    records: list[PartnerRecord],
    name_norms: list[str | None] | None = None,
) -> None:
    """Append one run's snapshot of partners.

    name_norms[i] is the normalised form of records[i].name. It's passed in
    rather than computed here so the store stays free of matching logic.
    Until normalise.py exists, callers pass nothing and name_norm is stored as NULL.
    """
    if name_norms is None:
        name_norms = [None] * len(records)
    rows = [
        (
            run_id,
            record.firm_id,
            record.person_key,
            record.name,
            name_norm,
            record.title,
            json.dumps(record.practices),
            json.dumps(record.offices),
            record.profile_url,
        )
        for record, name_norm in zip(records, name_norms, strict=True)
    ]
    conn.executemany(
        """
        INSERT INTO observations (
            run_id, firm_id, person_key, full_name, name_norm,
            title, practices, offices, profile_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def get_observations(
    conn: sqlite3.Connection, run_id: int, firm_id: str
) -> list[PartnerRecord]:
    """Return the partners seen at one firm in one run."""
    rows = conn.execute(
        """
        SELECT firm_id, person_key, full_name, title, practices, offices, profile_url
        FROM observations WHERE run_id = ? AND firm_id = ?
        ORDER BY full_name
        """,
        (run_id, firm_id),
    ).fetchall()
    return [
        PartnerRecord(
            name=row["full_name"],
            firm_id=row["firm_id"],
            title=row["title"],
            practices=json.loads(row["practices"]),
            offices=json.loads(row["offices"]),
            profile_url=row["profile_url"],
            person_key=row["person_key"],
        )
        for row in rows
    ]


def get_last_ok_run_id(
    conn: sqlite3.Connection, firm_id: str, before_run_id: int | None = None
) -> int | None:
    """Most recent run where this firm's status was OK, optionally before a given run.

    Diffs compare against this, not simply the previous run, so a broken
    scrape last week doesn't turn into fake joiners this week.
    """
    query = "SELECT MAX(run_id) FROM firm_runs WHERE firm_id = ? AND status = 'OK'"
    params: list[object] = [firm_id]
    if before_run_id is not None:
        query += " AND run_id < ?"
        params.append(before_run_id)
    row = conn.execute(query, params).fetchone()
    return row[0]


def get_firm_runs(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    """Every firm's status for one run (feeds the health panel)."""
    return conn.execute(
        "SELECT firm_id, status, partner_count, message FROM firm_runs WHERE run_id = ? "
        "ORDER BY firm_id",
        (run_id,),
    ).fetchall()
