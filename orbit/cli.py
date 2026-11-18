"""Command-line entry point: `orbit run` (and later `orbit report`, `orbit health`).

For now `orbit run` only loads and validates the config, mirrors the firm list
into the database, and records an empty run. Scraping, health checks and
diffing are added in later build steps.
"""

import logging
from pathlib import Path

import typer

from orbit import store
from orbit.adapters import ADAPTERS
from orbit.models import load_config

DEFAULT_CONFIG = Path("config/firms.yaml")
DEFAULT_DB = Path("data/orbit.db")

app = typer.Typer(
    help="Orbit: track Corporate/M&A partners and what changed.", no_args_is_help=True
)


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
    conn = store.connect(db)
    try:
        store.upsert_firms(conn, orbit_config.firms)
        run_id = store.create_run(conn)
        # Firm processing (fetch -> normalise -> health -> store -> diff) arrives in later steps.
        store.finish_run(conn, run_id)
        conn.commit()
    finally:
        conn.close()

    enabled = orbit_config.enabled_firms()
    typer.echo(f"Run {run_id} recorded in {db}")
    typer.echo(f"Firms in config: {len(orbit_config.firms)}, enabled: {len(enabled)}")
    for firm in enabled:
        adapter_state = (
            "adapter registered" if firm.id in ADAPTERS else "no adapter yet"
        )
        typer.echo(f"  - {firm.id}: {adapter_state}")
