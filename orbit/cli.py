"""Command-line entry point: `orbit run`, `orbit report` and `orbit demo`.

`orbit run` loads the config, starts a run, and for every enabled firm:
fetch (adapter) -> keep partners only -> health check -> store the snapshot.
Then it diffs every OK firm against its last OK snapshot, pairs cross-firm
moves, stores the resulting events, and writes reports/latest.html.

`orbit report` re-renders the report from the database without fetching.
`orbit demo` builds reports/demo-week1.html and demo-week2.html from fictional
data in a throwaway database.
"""

import logging
import sqlite3
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import typer

from orbit import diff, store
from orbit.adapters import ADAPTERS
from orbit.adapters.base import AdapterError
from orbit.fetch import FetchBlocked, Fetcher, FetchFailed
from orbit.health import check_health
from orbit.models import Event, FirmConfig, PartnerRecord, load_config
from orbit.normalise import filter_partners, normalise_name
from orbit.report import write_report

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("config/firms.yaml")
DEFAULT_DB = Path("data/orbit.db")
DEFAULT_REPORT = Path("reports/latest.html")
DEFAULT_DEMO_DIR = Path("reports")

app = typer.Typer(
    help="Orbit: track Corporate/M&A partners and what changed.", no_args_is_help=True
)


class FirmOutcome(NamedTuple):
    """What happened to one firm in one run (mirrors a firm_runs row)."""

    status: str  # OK | SUSPECT | FAILED | BLOCKED | SKIPPED
    partner_count: int
    message: str


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug logs."),
) -> None:
    """Orbit partner market tracker."""
    # A callback keeps `run` as a named subcommand even while it's the only one.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def run(
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to firms.yaml."),
    db: Path = typer.Option(DEFAULT_DB, help="Path to the SQLite database."),
    output: Path = typer.Option(DEFAULT_REPORT, help="Where to write the HTML report."),
) -> None:
    """Run one collection cycle over every enabled firm, then write the report."""
    orbit_config = load_config(config)
    enabled = orbit_config.enabled_firms()
    conn = store.connect(db)
    try:
        store.upsert_firms(conn, orbit_config.firms)
        run_id = store.create_run(conn)
        conn.commit()
        typer.echo(f"Run {run_id}: {len(enabled)} enabled firm(s)")
        with Fetcher(orbit_config.user_agent, run_id) as fetcher:
            for firm in enabled:
                outcome = collect_firm(conn, fetcher, firm, run_id)
                line = (
                    f"  - {firm.id}: {outcome.status}, {outcome.partner_count} partners"
                )
                typer.echo(f"{line} ({outcome.message})" if outcome.message else line)
        events = record_events(conn, run_id)
        store.finish_run(conn, run_id)
        conn.commit()
        write_report(conn, orbit_config, output)
    finally:
        conn.close()
    typer.echo(describe_events(events))
    typer.echo(f"Run {run_id} saved to {db}; report written to {output}")


@app.command()
def report(
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to firms.yaml."),
    db: Path = typer.Option(DEFAULT_DB, help="Path to the SQLite database."),
    output: Path = typer.Option(DEFAULT_REPORT, help="Where to write the HTML report."),
) -> None:
    """Re-render the report for the latest run, without fetching anything."""
    orbit_config = load_config(config)
    conn = store.connect(db)
    try:
        write_report(conn, orbit_config, output)
    finally:
        conn.close()
    typer.echo(f"Report written to {output}")


@app.command()
def demo(
    output_dir: Path = typer.Option(
        DEFAULT_DEMO_DIR, help="Folder for the demo reports (one per week)."
    ),
) -> None:
    """Build one report per fictional week (never touches the real database)."""
    # Imported here, not at the top: orbit.demo reuses this module's pipeline functions.
    from orbit.demo import run_demo

    for path in run_demo(output_dir):
        typer.echo(f"Demo report (fictional data) written to {path}")


def collect_firm(
    conn: sqlite3.Connection, fetcher: Fetcher, firm: FirmConfig, run_id: int
) -> FirmOutcome:
    """Fetch one firm's records and hand them to save_snapshot; record failures as statuses.

    Each firm is committed on its own, so one firm failing never loses another's data.
    """
    adapter_class = ADAPTERS.get(firm.id)
    if adapter_class is None:
        outcome = FirmOutcome("SKIPPED", 0, "no adapter registered")
    else:
        try:
            records = adapter_class(firm, fetcher).fetch_partners()
            return save_snapshot(conn, run_id, firm.id, records)
        except FetchBlocked as error:
            outcome = FirmOutcome("BLOCKED", 0, str(error))
        except (FetchFailed, AdapterError) as error:
            outcome = FirmOutcome("FAILED", 0, str(error))
        except Exception as error:
            # A bug in one adapter must not stop the other firms from being collected.
            logger.exception("Unexpected error in the %s adapter", firm.id)
            outcome = FirmOutcome("FAILED", 0, f"unexpected error: {error!r}")
    store.record_firm_run(
        conn, run_id, firm.id, outcome.status, outcome.partner_count, outcome.message
    )
    conn.commit()
    return outcome


def save_snapshot(
    conn: sqlite3.Connection, run_id: int, firm_id: str, records: list[PartnerRecord]
) -> FirmOutcome:
    """Keep partners only, health-check the count, then store the snapshot and status.

    A SUSPECT snapshot is stored too (useful when investigating), but only OK
    snapshots are ever diffed, so it can't produce events.
    """
    partners = filter_partners(records)
    last_ok_count = store.get_last_ok_partner_count(conn, firm_id, before_run_id=run_id)
    status, message = check_health(len(partners), last_ok_count)
    store.save_observations(
        conn, run_id, partners, [normalise_name(p.name) for p in partners]
    )
    store.record_firm_run(conn, run_id, firm_id, status, len(partners), message)
    conn.commit()
    return FirmOutcome(status, len(partners), message)


def record_events(conn: sqlite3.Connection, run_id: int) -> list[Event]:
    """Diff this run against each firm's last OK run and store the events."""
    events = diff.diff_run(conn, run_id)
    store.save_events(conn, run_id, events)
    conn.commit()
    return events


def describe_events(events: list[Event]) -> str:
    """One line summarising the run's events, e.g. 'Events: 3 (JOINED 2, MOVED 1)'."""
    if not events:
        return "Events: none"
    counts = Counter(event.type for event in events)
    summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    flagged = sum(1 for event in events if event.needs_review)
    review = f"; {flagged} need review" if flagged else ""
    return f"Events: {len(events)} ({summary}{review})"
