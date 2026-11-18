"""Command-line entry point: `orbit run` (and later `orbit report`, `orbit health`).

`orbit run` loads the config, starts a run, and for every enabled firm that
has an adapter: fetches its partners through the polite fetcher and stores
them as that run's snapshot, recording the firm's status. Normalisation,
health checks and diffing are added in later build steps.
"""

import logging
import sqlite3
from pathlib import Path
from typing import NamedTuple

import typer

from orbit import store
from orbit.adapters import ADAPTERS
from orbit.adapters.base import AdapterError
from orbit.fetch import FetchBlocked, Fetcher, FetchFailed
from orbit.models import FirmConfig, load_config

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("config/firms.yaml")
DEFAULT_DB = Path("data/orbit.db")

app = typer.Typer(
    help="Orbit: track Corporate/M&A partners and what changed.", no_args_is_help=True
)


class FirmOutcome(NamedTuple):
    """What happened to one firm in one run (mirrors a firm_runs row)."""

    status: str  # OK | FAILED | BLOCKED | SKIPPED
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
) -> None:
    """Run one collection cycle over every enabled firm."""
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
        store.finish_run(conn, run_id)
        conn.commit()
    finally:
        conn.close()
    typer.echo(f"Run {run_id} saved to {db}")


def collect_firm(
    conn: sqlite3.Connection, fetcher: Fetcher, firm: FirmConfig, run_id: int
) -> FirmOutcome:
    """Fetch one firm's partners, store the snapshot and its status, and commit.

    Each firm is committed on its own, so one firm failing never loses another's data.
    """
    adapter_class = ADAPTERS.get(firm.id)
    records = []
    if adapter_class is None:
        outcome = FirmOutcome("SKIPPED", 0, "no adapter registered")
    else:
        try:
            records = adapter_class(firm, fetcher).fetch_partners()
            outcome = FirmOutcome("OK", len(records), "")
        except FetchBlocked as error:
            outcome = FirmOutcome("BLOCKED", 0, str(error))
        except (FetchFailed, AdapterError) as error:
            outcome = FirmOutcome("FAILED", 0, str(error))
        except Exception as error:
            # A bug in one adapter must not stop the other firms from being collected.
            logger.exception("Unexpected error in the %s adapter", firm.id)
            outcome = FirmOutcome("FAILED", 0, f"unexpected error: {error!r}")
    if outcome.status == "OK":
        store.save_observations(conn, run_id, records)
    store.record_firm_run(
        conn, run_id, firm.id, outcome.status, outcome.partner_count, outcome.message
    )
    conn.commit()
    return outcome
