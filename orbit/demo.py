"""`orbit demo`: reports built from fictional data, to show what weekly changes look like.

Real data only changes week to week, so a fresh install has nothing to show.
The demo plays two fictional weekly runs through the same pipeline as
`orbit run` (save_snapshot, then record_events), so the reports show real
engine output, not hand-written events. A report is written after each week:
- demo-week1.html: the baseline, where nothing can have changed yet;
- demo-week2.html: a week later, with every kind of change.

Between week 1 and week 2:
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


def demo_weeks() -> list[tuple[str, dict[str, list[PartnerRecord]]]]:
    """The fictional weeks in order: (run start time, partners per firm)."""
    return [(WEEK_1, week_1()), (WEEK_2, week_2())]


def demo_banner(week: int, total: int) -> str:
    """The banner on each demo report, so it can't be mistaken for real data."""
    return f"DEMO: week {week} of {total}, fictional data"


def play_week(conn, started_at: str, snapshots: dict[str, list[PartnerRecord]]) -> None:
    """Run one fictional week through the real pipeline, as `orbit run` would."""
    run_id = store.create_run(conn, started_at=started_at)
    for firm_id, records in snapshots.items():
        save_snapshot(conn, run_id, firm_id, records)
    record_events(conn, run_id)
    store.finish_run(conn, run_id)
    conn.commit()


def seed_demo_data(conn) -> None:
    """Play every fictional week into `conn` (the tests use the final state)."""
    store.upsert_firms(conn, demo_config().firms)
    for started_at, snapshots in demo_weeks():
        play_week(conn, started_at, snapshots)


def run_demo(output_dir: Path) -> list[Path]:
    """Write demo-week1.html, demo-week2.html... into `output_dir` from a throwaway database.

    The report always shows the latest run, so each week's report is written
    right after that week is played.
    """
    weeks = demo_weeks()
    written = []
    with tempfile.TemporaryDirectory(prefix="orbit-demo-") as folder:
        conn = store.connect(Path(folder) / "demo.db")
        try:
            store.upsert_firms(conn, demo_config().firms)
            for number, (started_at, snapshots) in enumerate(weeks, start=1):
                play_week(conn, started_at, snapshots)
                output = output_dir / f"demo-week{number}.html"
                banner = demo_banner(number, len(weeks))
                written.append(write_report(conn, demo_config(), output, banner=banner))
        finally:
            conn.close()
    return written
