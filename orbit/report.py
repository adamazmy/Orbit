"""The HTML report: one self-contained file a recruiter can open anywhere.

Reads one run from the database and renders templates/report.html.j2 into a
single HTML file (inline CSS and a little vanilla JS, nothing from a CDN), so
it can be emailed or opened offline. Sections, in order:
1. header: run date, firms tracked, partners per firm;
2. this week's changes (MOVED first), with review flags kept apart;
3. firm health, including firms that are blocked or out of scope in config;
4. a searchable table of every tracked partner.

The data work (grouping, wording, filter options) happens here in Python, so
the template only lays things out.
"""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from orbit import store
from orbit.models import Event, OrbitConfig

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "report.html.j2"

# Most important first: a move is the headline, then arrivals and departures.
CHANGE_ORDER = ["MOVED", "JOINED", "LEFT", "OFFICE_CHANGED", "TITLE_CHANGED"]
REVIEW_TYPES = ("POSSIBLE_MOVE", "LISTED_AT_TWO_FIRMS")
# Why a firm's partner list comes from an earlier run instead of this one.
STALE_REASONS = {
    "SUSPECT": "this run looked broken",
    "FAILED": "this run failed",
    "BLOCKED": "this run was blocked",
}
CONFIG_STATUS_LABELS = {
    "BLOCKED": "BLOCKED",
    "OUT_OF_SCOPE": "OUT OF SCOPE",
    "NOT_ASSESSED": "NOT ASSESSED",
}


def write_report(
    conn, config: OrbitConfig, output: Path, banner: str | None = None
) -> Path:
    """Render the latest run to `output` (folders created as needed) and return the path."""
    data = build_report_data(conn, config)
    data["banner"] = banner
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(data), encoding="utf-8")
    return output


def render(data: dict[str, Any]) -> str:
    """Fill the Jinja2 template. Autoescaping is on: names come from websites."""
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.get_template(TEMPLATE_NAME).render(**data)


def build_report_data(conn, config: OrbitConfig) -> dict[str, Any]:
    """Everything the template shows, for the most recent run."""
    names = {firm.id: firm.name for firm in config.firms}
    run_id = store.get_latest_run_id(conn)
    data: dict[str, Any] = {
        "generated_at": _format_date(datetime.now(UTC).isoformat()),
        "run": None,
        "health": health_rows(config, {}),
    }
    if run_id is None:
        return data  # nothing collected yet: the template says so
    firm_runs = {row["firm_id"]: row for row in store.get_firm_runs(conn, run_id)}
    events = store.get_events(conn, run_id)
    snapshots = snapshot_runs(conn, run_id, firm_runs)
    notes = stale_notes(run_id, firm_runs, snapshots)
    partners = partner_rows(conn, snapshots, names)
    data.update(
        run={
            "id": run_id,
            "date": _format_date(store.get_run_started_at(conn, run_id)),
        },
        firms=firm_summary(firm_runs, names, snapshots, partners),
        stale=[{"name": names.get(f, f), "note": n} for f, n in sorted(notes.items())],
        changes=[describe_event(e, names) for e in sort_changes(events)],
        review=[describe_event(e, names) for e in events if e.type in REVIEW_TYPES],
        baseline=baseline_firms(conn, run_id, firm_runs, names),
        health=health_rows(config, firm_runs),
        partners=partners,
        filters=filter_options(partners),
    )
    return data


def firm_summary(
    firm_runs: dict, names: dict[str, str], snapshots: dict[str, int], partners: list
) -> list[dict]:
    """Header chip per firm collected this run.

    The count is the number of rows the partners table shows for the firm, so
    a SUSPECT or FAILED firm shows its last good count, with this run's count
    alongside as the warning (the health table keeps this run's figure).
    """
    shown = Counter(p["firm"] for p in partners)
    chips = []
    for firm_id, row in sorted(firm_runs.items()):
        if row["status"] == "SKIPPED":
            continue
        snapshot_run = snapshots.get(firm_id)
        is_stale = snapshot_run is not None and row["status"] != "OK"
        name = names.get(firm_id, firm_id)
        chips.append(
            {
                "name": name,
                "status": row["status"],
                "count": shown[name],
                "last_good_run": snapshot_run if is_stale else None,
                "this_run_count": row["partner_count"],
            }
        )
    return chips


def sort_changes(events: list[Event]) -> list[Event]:
    """Market changes only (no review flags), MOVED first, then by name."""
    changes = [e for e in events if e.type in CHANGE_ORDER]
    return sorted(changes, key=lambda e: (CHANGE_ORDER.index(e.type), e.name))


def baseline_firms(
    conn, run_id: int, firm_runs: dict, names: dict[str, str]
) -> list[str]:
    """OK firms whose first OK snapshot is this run: no changes can show for them yet."""
    return [
        names.get(firm_id, firm_id)
        for firm_id, row in sorted(firm_runs.items())
        if row["status"] == "OK"
        and store.get_last_ok_run_id(conn, firm_id, before_run_id=run_id) is None
    ]


def describe_event(event: Event, names: dict[str, str]) -> dict[str, Any]:
    """Turn one event into display text: a label, who, where, and a profile link."""
    firm_from = names.get(event.from_firm or "", event.from_firm)
    firm_to = names.get(event.to_firm or "", event.to_firm)
    details = event.details
    row = {"type": event.type, "name": event.name, "link": details.get("profile_url")}
    if event.type == "MOVED":
        row.update(label="Moved", where=f"{firm_from} → {firm_to}")
        row["link"] = details.get("to", {}).get("profile_url")
        row["detail"] = ", ".join(details.get("to", {}).get("offices", []))
    elif event.type == "JOINED":
        row.update(
            label="Joined", where=firm_to, detail=", ".join(details.get("offices", []))
        )
    elif event.type == "LEFT":
        row.update(label="Left", where=firm_from, detail="Profile no longer listed")
    elif event.type == "OFFICE_CHANGED":
        old, new = details.get("old_offices", []), details.get("new_offices", [])
        row.update(label="Office change", where=firm_to)
        row["detail"] = f"{', '.join(old) or '—'} → {', '.join(new) or '—'}"
    elif event.type == "TITLE_CHANGED":
        row.update(label="Title change", where=firm_to)
        row["detail"] = f"{details.get('old_title')} → {details.get('new_title')}"
    else:
        row.update(_describe_review(event, firm_from, firm_to))
    return row


def _describe_review(event: Event, firm_from: str, firm_to: str) -> dict[str, Any]:
    """Wording for the two review flags, which state a doubt, never a fact."""
    details = event.details
    if event.type == "POSSIBLE_MOVE":
        left_name = details.get("from", {}).get("name", "?")
        return {
            "label": "Possible move",
            "where": f"{firm_from} → {firm_to}",
            "detail": (
                f"Left {firm_from} as “{left_name}”, joined {firm_to} as “{event.name}” "
                f"(name match {event.confidence:.0f}%). Check before relying on it."
            ),
            "link": details.get("to", {}).get("profile_url"),
        }
    firms = details.get("firms", [])
    return {
        "label": "Listed at two firms",
        "where": " and ".join(firms),
        "detail": "Same name on both directories this week: a recent move, or two people.",
        "link": (details.get("profile_urls") or [None])[0],
    }


def health_rows(config: OrbitConfig, firm_runs: dict) -> list[dict]:
    """One row per firm in config: this run's status, or its config status and reason."""
    rows = []
    for firm in config.firms:
        if firm.id in firm_runs:
            run = firm_runs[firm.id]
            rows.append(
                {
                    "name": firm.name,
                    "status": run["status"],
                    "count": run["partner_count"],
                    "reason": run["message"] or "",
                }
            )
        elif firm.status != "ACTIVE":
            rows.append(
                {
                    "name": firm.name,
                    "status": CONFIG_STATUS_LABELS[firm.status],
                    "count": None,
                    "reason": firm.reason or "",
                }
            )
    # Firms collected this run first, then blocked, then everything else.
    order = [
        "OK",
        "SUSPECT",
        "FAILED",
        "BLOCKED",
        "SKIPPED",
        "OUT OF SCOPE",
        "NOT ASSESSED",
    ]
    return sorted(rows, key=lambda r: (order.index(r["status"]), r["name"]))


def snapshot_runs(conn, run_id: int, firm_runs: dict) -> dict[str, int]:
    """Which run's partner list to show per firm: this run if OK, else its last OK run.

    A doubtful (SUSPECT) or missing (FAILED) list is never shown. Firms with no
    OK run at all are left out.
    """
    snapshots = {}
    for firm_id, run in firm_runs.items():
        if run["status"] == "OK":
            snapshots[firm_id] = run_id
            continue
        last_ok = store.get_last_ok_run_id(conn, firm_id, before_run_id=run_id)
        if last_ok is not None:
            snapshots[firm_id] = last_ok
    return snapshots


def stale_notes(
    run_id: int, firm_runs: dict, snapshots: dict[str, int]
) -> dict[str, str]:
    """A warning for every firm whose list comes from an earlier run, so it isn't read as current."""
    notes = {}
    for firm_id, snapshot_run in snapshots.items():
        if snapshot_run == run_id:
            continue
        reason = STALE_REASONS.get(
            firm_runs[firm_id]["status"], "this run had no usable list"
        )
        notes[firm_id] = (
            f"Showing last good snapshot (run {snapshot_run}), because {reason}"
        )
    return notes


def partner_rows(conn, snapshots: dict[str, int], names: dict[str, str]) -> list[dict]:
    """Every tracked partner, from the snapshot chosen for each firm by snapshot_runs."""
    rows = []
    for firm_id, snapshot_run in sorted(snapshots.items()):
        for record in store.get_observations(conn, snapshot_run, firm_id):
            rows.append(
                {
                    "name": record.name,
                    "firm": names.get(firm_id, firm_id),
                    "title": record.title,
                    "offices": record.offices,
                    "practices": record.practices,
                    "url": record.profile_url,
                }
            )
    return sorted(rows, key=lambda r: (r["firm"], r["name"]))


def filter_options(partners: list[dict]) -> dict[str, list[str]]:
    """The values offered in the table's firm / office / practice drop-downs."""
    return {
        "firms": sorted({p["firm"] for p in partners}),
        "offices": sorted({o for p in partners for o in p["offices"]}),
        "practices": sorted({x for p in partners for x in p["practices"]}),
    }


def _format_date(iso: str | None) -> str:
    """'2026-11-19T09:00:00+00:00' -> '19 Nov 2026'."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).strftime("%d %b %Y").lstrip("0")
