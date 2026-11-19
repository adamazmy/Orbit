"""The HTML report: one self-contained file a recruiter can open anywhere.

Reads one run from the database and renders templates/report.html.j2 into a
single HTML file (inline CSS and a little vanilla JS, nothing from a CDN), so
it can be emailed or opened offline. A dashboard, in order:
1. overview: KPI cards (partners tracked, firms collected, changes, last update);
2. changes since the last run (MOVED first), with review flags as their own cards;
3. data health: every firm in config, in recruiter wording, with the technical
   reason in an expandable row, and a coverage summary;
4. a searchable table of every tracked partner, with per-firm counts.

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
# Badge text on each change card.
BADGES = {
    "MOVED": "Moved",
    "JOINED": "Joined",
    "LEFT": "Left",
    "OFFICE_CHANGED": "Office",
    "TITLE_CHANGED": "Title",
    "POSSIBLE_MOVE": "Needs review",
    "LISTED_AT_TWO_FIRMS": "Needs review",
}
# Data health in recruiter wording, in display order. Run statuses and config
# statuses share one list; the technical reason stays in each row's details.
STATUS_LABELS = {
    "OK": "Collected",
    "SUSPECT": "Suspect",
    "FAILED": "Failed",
    "BLOCKED": "Blocked (unable to collect)",
    "SKIPPED": "Not collected (no adapter)",
    "OUT_OF_SCOPE": "Out of scope",
    "NOT_ASSESSED": "Not yet assessed",
}
# Added to Suspect / Failed when the report falls back to an earlier list.
SHOWING_LAST_GOOD = " (showing last good data)"
COVERAGE_WORDS = {
    "OK": "collected",
    "SUSPECT": "suspect",
    "FAILED": "failed",
    "BLOCKED": "blocked",
    "SKIPPED": "not collected",
    "OUT_OF_SCOPE": "out of scope",
    "NOT_ASSESSED": "not yet assessed",
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
    health = health_rows(config, {}, {}, {})
    data: dict[str, Any] = {
        "generated_at": _format_date(datetime.now(UTC).isoformat()),
        "run": None,
        "health": health,
        "coverage": coverage_summary(health),
    }
    if run_id is None:
        return data  # nothing collected yet: the template says so
    firm_runs = {row["firm_id"]: row for row in store.get_firm_runs(conn, run_id)}
    events = store.get_events(conn, run_id)
    snapshots = snapshot_runs(conn, run_id, firm_runs)
    notes = stale_notes(run_id, firm_runs, snapshots)
    partners = partner_rows(conn, snapshots, names)
    baseline = baseline_firms(conn, run_id, firm_runs, names)
    changes = [describe_event(e, names) for e in sort_changes(events)]
    review = [describe_event(e, names) for e in events if e.type in REVIEW_TYPES]
    health = health_rows(
        config, firm_runs, snapshots, last_success_dates(conn, run_id, firm_runs)
    )
    run_date = _format_date(store.get_run_started_at(conn, run_id))
    data.update(
        run={"id": run_id, "date": run_date},
        firms=firm_summary(firm_runs, names, snapshots, partners),
        stale=[{"name": names.get(f, f), "note": n} for f, n in sorted(notes.items())],
        changes=changes,
        review=review,
        baseline=baseline,
        monitored=monitored_firm_count(firm_runs, names, baseline),
        health=health,
        coverage=coverage_summary(health),
        kpis=kpi_cards(partners, firm_runs, config, changes, review, run_date),
        partners=partners,
        filters=filter_options(partners),
    )
    return data


def kpi_cards(
    partners: list[dict],
    firm_runs: dict,
    config: OrbitConfig,
    changes: list[dict],
    review: list[dict],
    run_date: str,
) -> dict[str, Any]:
    """The four overview numbers at the top of the report."""
    return {
        "partners": len(partners),
        "partner_firms": len({p["firm"] for p in partners}),
        "collected": sum(1 for row in firm_runs.values() if row["status"] == "OK"),
        "configured": len(config.firms),
        "changes": len(changes),
        "review": len(review),
        "updated": run_date,
    }


def monitored_firm_count(
    firm_runs: dict, names: dict[str, str], baseline: list[str]
) -> int:
    """Firms OK in this run and in an earlier one: the firms a change could show for."""
    ok_now = [f for f, row in firm_runs.items() if row["status"] == "OK"]
    return sum(1 for firm_id in ok_now if names.get(firm_id, firm_id) not in baseline)


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
    row = {
        "type": event.type,
        "badge": BADGES[event.type],
        "name": event.name,
        "link": details.get("profile_url"),
    }
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


def health_rows(
    config: OrbitConfig,
    firm_runs: dict,
    snapshots: dict[str, int],
    last_success: dict[str, str],
) -> list[dict]:
    """One row per firm in config: this run's status, or its config status and reason.

    `status` stays the technical code (OK, SUSPECT, BLOCKED...); `label` is the
    recruiter wording on the badge. The partner count is this run's, even when
    the partners table falls back to an earlier list.
    """
    rows = []
    for firm in config.firms:
        if firm.id in firm_runs:
            run = firm_runs[firm.id]
            status, count, reason = run["status"], run["partner_count"], run["message"]
        elif firm.status != "ACTIVE":
            status, count, reason = firm.status, None, firm.reason
        else:
            continue  # active in config but not part of this run
        label = STATUS_LABELS[status]
        if status in ("SUSPECT", "FAILED") and firm.id in snapshots:
            label += SHOWING_LAST_GOOD
        rows.append(
            {
                "name": firm.name,
                "status": status,
                "label": label,
                "count": count,
                "last_success": last_success.get(firm.id, ""),
                "reason": reason or "",
                "evidence_url": firm.evidence_url,
            }
        )
    order = list(STATUS_LABELS)
    return sorted(rows, key=lambda r: (order.index(r["status"]), r["name"]))


def last_success_dates(conn, run_id: int, firm_runs: dict) -> dict[str, str]:
    """For each firm in this run, the date of its most recent OK collection."""
    dates = {}
    for firm_id, row in firm_runs.items():
        if row["status"] == "OK":
            last_ok = run_id
        else:
            last_ok = store.get_last_ok_run_id(conn, firm_id, before_run_id=run_id)
        if last_ok is not None:
            dates[firm_id] = _format_date(store.get_run_started_at(conn, last_ok))
    return dates


def coverage_summary(health: list[dict]) -> str:
    """'3 collected · 3 blocked · 1 out of scope · 8 not yet assessed', from the rows."""
    counts = Counter(row["status"] for row in health)
    return " · ".join(
        f"{counts[status]} {word}"
        for status, word in COVERAGE_WORDS.items()
        if counts[status]
    )


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
