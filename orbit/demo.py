"""`orbit demo`: a report built from fictional data, to show what weekly changes look like.

Real data only changes week to week, so a fresh install has nothing to show.
The demo plays two fictional weekly runs through the same pipeline as
`orbit run` (save_snapshot, then record_events), so the report shows real
engine output, not hand-written events. Between week 1 and week 2:
- Ilona Fictional moves from Alder & Finch to Birch Lane (MOVED);
- Jane Doe leaves Birch Lane and "Janet Doe" joins Alder & Finch (POSSIBLE_MOVE,
  with the LEFT and JOINED kept);
- Avery Example joins Birch Lane (JOINED); Sam Sample leaves Alder & Finch (LEFT);
- one Birch Lane partner moves from New York to London (OFFICE_CHANGED);
- Cedar Row's list collapses from 10 to 2 (SUSPECT, so it produces no events).

Everything happens in a temporary database that is deleted afterwards. The
real database is never opened.
"""

import tempfile
from pathlib import Path

from orbit import store
from orbit.cli import record_events, save_snapshot
from orbit.models import FirmConfig, OrbitConfig, PartnerRecord
from orbit.report import write_report

DEMO_BANNER = "DEMO — fictional data showing how weekly changes appear."
WEEK_1 = "2026-11-12T09:00:00+00:00"
WEEK_2 = "2026-11-19T09:00:00+00:00"

FIRST_NAMES = [
    "Ada",
    "Ben",
    "Cora",
    "Dev",
    "Elin",
    "Finn",
    "Gale",
    "Hugo",
    "Iris",
    "Jude",
]


def demo_config() -> OrbitConfig:
    """Fictional firms, including one blocked firm so the health panel shows that case."""
    return OrbitConfig(
        user_agent="OrbitBot demo (no network)",
        firms=[
            FirmConfig(
                id="alder", name="Alder & Finch LLP", enabled=True, status="ACTIVE"
            ),
            FirmConfig(
                id="birch", name="Birch Lane LLP", enabled=True, status="ACTIVE"
            ),
            FirmConfig(id="cedar", name="Cedar Row LLP", enabled=True, status="ACTIVE"),
            FirmConfig(
                id="dogwood",
                name="Dogwood Hart LLP",
                status="BLOCKED",
                reason="Demo example: the directory is disallowed by robots.txt.",
            ),
        ],
    )


def person(firm: str, name: str, office: str = "New York") -> PartnerRecord:
    """One fictional partner at a fictional firm."""
    slug = name.lower().replace(" ", "-")
    practice = "Private Equity" if len(name) % 2 else "Mergers & Acquisitions"
    return PartnerRecord(
        name=name,
        firm_id=firm,
        title="Partner",
        practices=[practice],
        offices=[office],
        profile_url=f"https://{firm}.example/people/{slug}",
        person_key=slug,
    )


def staff(firm: str, surname: str, count: int) -> list[PartnerRecord]:
    """count stable fictional partners, alternating New York and London."""
    return [
        person(firm, f"{FIRST_NAMES[i]} {surname}", "New York" if i % 2 else "London")
        for i in range(count)
    ]


def week_1() -> dict[str, list[PartnerRecord]]:
    """The baseline week."""
    return {
        "alder": staff("alder", "Alderton", 8)
        + [person("alder", "Ilona Fictional"), person("alder", "Sam Sample")],
        "birch": staff("birch", "Birchall", 8)
        + [person("birch", "Jane Doe", "London")],
        "cedar": staff("cedar", "Cedarby", 10),
    }


def week_2() -> dict[str, list[PartnerRecord]]:
    """A week later: one move, a possible move, arrivals, departures, a broken scrape."""
    birch = staff("birch", "Birchall", 8)
    birch[1] = person("birch", birch[1].name, "London")  # was New York
    return {
        "alder": staff("alder", "Alderton", 8)
        + [person("alder", "Janet Doe", "London")],
        "birch": birch
        + [
            person("birch", "Ilona Fictional"),
            person("birch", "Avery Example", "London"),
        ],
        "cedar": staff("cedar", "Cedarby", 2),
    }


def seed_demo_data(conn) -> None:
    """Play both fictional weeks through the real pipeline into `conn`."""
    store.upsert_firms(conn, demo_config().firms)
    for started_at, snapshots in ((WEEK_1, week_1()), (WEEK_2, week_2())):
        run_id = store.create_run(conn, started_at=started_at)
        for firm_id, records in snapshots.items():
            save_snapshot(conn, run_id, firm_id, records)
        record_events(conn, run_id)
        store.finish_run(conn, run_id)
    conn.commit()


def run_demo(output: Path) -> Path:
    """Build the demo report at `output` from a throwaway database."""
    with tempfile.TemporaryDirectory(prefix="orbit-demo-") as folder:
        conn = store.connect(Path(folder) / "demo.db")
        try:
            seed_demo_data(conn)
            write_report(conn, demo_config(), output, banner=DEMO_BANNER)
        finally:
            conn.close()
    return output
