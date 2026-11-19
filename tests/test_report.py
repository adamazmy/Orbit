"""Report and demo tests: render from fictional data, never touch the real database."""

import html
import re
from pathlib import Path

import pytest

from orbit import demo, store
from orbit.cli import record_events, save_snapshot
from orbit.demo import DEMO_BANNER, demo_config, run_demo, seed_demo_data
from orbit.models import FirmConfig, OrbitConfig, PartnerRecord
from orbit.report import (
    build_report_data,
    coverage_summary,
    health_rows,
    render,
    write_report,
)

REAL_DB = Path(__file__).resolve().parent.parent / "data" / "orbit.db"


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "report-test.db")
    yield connection
    connection.close()


def visible_text(page):
    """The report's text as a reader sees it: no tags, styles or scripts, entities decoded."""
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.DOTALL)
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", page).split()))


# --- Report built from the fictional demo weeks ------------------------------


@pytest.fixture
def demo_html(conn):
    seed_demo_data(conn)
    return render(build_report_data(conn, demo_config()) | {"banner": None})


def test_sections_appear_in_order(demo_html):
    # Overview, changes, data health, then the partner table.
    positions = [
        demo_html.index(f'<section id="{section}"')
        for section in ("overview", "changes", "health", "partners")
    ]
    assert positions == sorted(positions)


def test_nav_links_to_every_section(demo_html):
    for section in ("overview", "changes", "partners", "health"):
        assert f'href="#{section}"' in demo_html


def test_moves_come_first_then_joins_and_departures(conn):
    seed_demo_data(conn)
    changes = build_report_data(conn, demo_config())["changes"]
    assert [c["label"] for c in changes] == [
        "Moved",
        "Joined",
        "Joined",
        "Left",
        "Left",
        "Office change",
    ]
    assert changes[0]["name"] == "Ilona Fictional"
    assert changes[0]["where"] == "Alder & Finch LLP → Birch Lane LLP"


def test_review_flags_are_labelled_and_kept_apart(conn):
    seed_demo_data(conn)
    data = build_report_data(conn, demo_config())
    assert [r["label"] for r in data["review"]] == ["Possible move"]
    assert all(c["type"] != "POSSIBLE_MOVE" for c in data["changes"])
    text = visible_text(render(data | {"banner": None}))
    assert "Needs review (1)" in text
    assert "Needs review Possible move Janet Doe" in text


def test_health_lists_suspect_and_config_blocked_firms(conn):
    seed_demo_data(conn)
    health = {r["name"]: r for r in build_report_data(conn, demo_config())["health"]}
    assert health["Cedar Row LLP"]["status"] == "SUSPECT"
    assert "10 -> 2" in health["Cedar Row LLP"]["reason"]
    assert health["Dogwood Hart LLP"]["status"] == "BLOCKED"
    assert "robots.txt" in health["Dogwood Hart LLP"]["reason"]


def test_suspect_firm_shows_its_last_good_list(conn):
    seed_demo_data(conn)
    partners = build_report_data(conn, demo_config())["partners"]
    cedar = [p for p in partners if p["firm"] == "Cedar Row LLP"]
    assert len(cedar) == 10  # week 1's list, not week 2's doubtful 2


def test_partner_table_has_filters_and_profile_links(demo_html):
    assert 'id="firm-filter"' in demo_html
    assert 'id="office-filter"' in demo_html
    assert 'id="practice-filter"' in demo_html
    assert 'href="https://birch.example/people/ilona-fictional"' in demo_html


def test_report_is_self_contained(demo_html):
    # No stylesheets, scripts or fonts loaded from anywhere else.
    assert not re.search(r"<(script|link)[^>]*(src|href)=", demo_html)
    assert "@import" not in demo_html


# --- Edge cases -----------------------------------------------------------------


def make_partner(firm, name):
    return PartnerRecord(
        name=name,
        firm_id=firm,
        title="Partner",
        offices=["London"],
        profile_url=f"https://{firm}.example/p/{len(name)}",
        person_key=name,
    )


def test_first_snapshot_says_baseline_recorded(conn):
    store.upsert_firms(conn, demo_config().firms)
    run_id = store.create_run(conn)
    save_snapshot(conn, run_id, "alder", [make_partner("alder", "Jane Example")])
    text = visible_text(
        render(build_report_data(conn, demo_config()) | {"banner": None})
    )
    assert (
        "Alder & Finch LLP: baseline recorded, changes appear from the next run" in text
    )


def test_no_runs_yet(conn):
    text = visible_text(
        render(build_report_data(conn, demo_config()) | {"banner": None})
    )
    assert "No runs yet" in text


def test_names_from_websites_are_escaped(conn):
    run_id = store.create_run(conn)
    save_snapshot(
        conn, run_id, "alder", [make_partner("alder", "<script>alert(1)</script> Doe")]
    )
    html = render(build_report_data(conn, demo_config()) | {"banner": None})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- The demo command ----------------------------------------------------------------


def test_demo_has_banner_and_every_kind_of_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = run_demo(tmp_path / "reports" / "demo.html")
    text = visible_text(output.read_text(encoding="utf-8"))
    assert DEMO_BANNER in text
    for label in (
        "Moved",
        "Possible move",
        "Joined",
        "Left",
        "Office",
        "Needs review",
        "Suspect (showing last good data)",
        "Blocked (unable to collect)",
    ):
        assert label in text


def test_demo_never_touches_the_real_database(tmp_path, monkeypatch):
    opened = []
    real_connect = store.connect

    def spy_connect(path):
        opened.append(Path(path))
        return real_connect(path)

    monkeypatch.setattr(store, "connect", spy_connect)
    monkeypatch.chdir(tmp_path)
    before = REAL_DB.stat().st_mtime if REAL_DB.exists() else None

    run_demo(tmp_path / "reports" / "demo.html")

    assert len(opened) == 1
    assert opened[0].name == "demo.db"
    assert not opened[0].exists()  # the throwaway database is deleted afterwards
    assert not (tmp_path / "data").exists()
    after = REAL_DB.stat().st_mtime if REAL_DB.exists() else None
    assert before == after


def test_write_report_creates_folders(conn, tmp_path):
    output = write_report(conn, demo_config(), tmp_path / "nested" / "latest.html")
    assert output.exists()


def test_demo_module_does_not_reference_the_real_database():
    source = Path(demo.__file__).read_text(encoding="utf-8")
    assert "orbit.db" not in source
    assert "DEFAULT_DB" not in source


# --- Stale data is always labelled -------------------------------------------------

SUSPECT_NOTE = "Showing last good snapshot (run 1), because this run looked broken"


def test_suspect_firm_is_labelled_in_header_and_partner_table(conn):
    seed_demo_data(conn)  # Cedar Row: 10 partners (OK) in run 1, 2 (SUSPECT) in run 2
    data = build_report_data(conn, demo_config())
    assert data["stale"] == [{"name": "Cedar Row LLP", "note": SUSPECT_NOTE}]

    text = visible_text(render(data | {"banner": None}))
    partners = text.split("Partners", 2)[2]  # after the nav link and the KPI label
    chip = "Cedar Row LLP · 10 (last good, run 1) · Suspect: this run returned 2"
    assert chip in partners
    assert SUSPECT_NOTE in partners


def test_header_count_matches_partner_rows_shown(conn):
    seed_demo_data(conn)
    data = build_report_data(conn, demo_config())
    for firm in data["firms"]:
        rows = [p for p in data["partners"] if p["firm"] == firm["name"]]
        assert firm["count"] == len(rows), firm["name"]
    cedar = next(f for f in data["firms"] if f["name"] == "Cedar Row LLP")
    assert (cedar["count"], cedar["this_run_count"]) == (10, 2)


def test_health_table_keeps_this_runs_count(conn):
    seed_demo_data(conn)
    health = build_report_data(conn, demo_config())["health"]
    cedar = next(r for r in health if r["name"] == "Cedar Row LLP")
    assert (cedar["status"], cedar["count"]) == ("SUSPECT", 2)
    assert cedar["reason"]


def test_ok_firms_carry_no_stale_label(conn):
    seed_demo_data(conn)
    data = build_report_data(conn, demo_config())
    ok_firms = [f for f in data["firms"] if f["status"] == "OK"]
    assert [f["last_good_run"] for f in ok_firms] == [None, None]


def test_failed_firm_is_labelled_too(conn):
    store.upsert_firms(conn, demo_config().firms)
    first = store.create_run(conn)
    save_snapshot(conn, first, "alder", [make_partner("alder", "Jane Example")])
    second = store.create_run(conn)
    store.record_firm_run(conn, second, "alder", "FAILED", 0, "HTTP 500")
    data = build_report_data(conn, demo_config())
    assert data["stale"] == [
        {
            "name": "Alder & Finch LLP",
            "note": f"Showing last good snapshot (run {first}), because this run failed",
        }
    ]
    assert [p["name"] for p in data["partners"]] == ["Jane Example"]


# --- Dashboard: coverage, monitored firms, recruiter wording -------------------

COVERAGE_CONFIG = OrbitConfig(
    user_agent="test",
    firms=[
        FirmConfig(id="a", name="A", enabled=True, status="ACTIVE"),
        FirmConfig(id="b", name="B", enabled=True, status="ACTIVE"),
        FirmConfig(id="c", name="C", status="BLOCKED", reason="robots.txt"),
        FirmConfig(id="d", name="D", status="BLOCKED", reason="challenge"),
        FirmConfig(id="e", name="E", status="OUT_OF_SCOPE", reason="litigation"),
        FirmConfig(id="f", name="F", status="NOT_ASSESSED", reason="later"),
    ],
)


def test_coverage_summary_is_computed_from_config_and_run():
    firm_runs = {
        "a": {"status": "OK", "partner_count": 5, "message": ""},
        "b": {"status": "BLOCKED", "partner_count": 0, "message": "HTTP 403"},
    }
    health = health_rows(COVERAGE_CONFIG, firm_runs, {}, {})
    assert coverage_summary(health) == (
        "1 collected · 3 blocked · 1 out of scope · 1 not yet assessed"
    )


def test_demo_coverage_summary_and_kpis(conn):
    seed_demo_data(conn)
    data = build_report_data(conn, demo_config())
    assert data["coverage"] == "2 collected · 1 suspect · 1 blocked"
    kpis = data["kpis"]
    assert (kpis["partners"], kpis["partner_firms"]) == (29, 3)
    assert (kpis["collected"], kpis["configured"]) == (2, 4)
    assert (kpis["changes"], kpis["review"]) == (6, 1)
    assert "2 collected · 1 suspect · 1 blocked" in visible_text(
        render(data | {"banner": None})
    )


def two_quiet_runs(conn, second_run_firms):
    """Run 1 collects Alder; run 2 collects `second_run_firms`, with no changes at Alder."""
    store.upsert_firms(conn, demo_config().firms)
    first = store.create_run(conn)
    save_snapshot(conn, first, "alder", [make_partner("alder", "Jane Example")])
    record_events(conn, first)
    second = store.create_run(conn)
    for firm in second_run_firms:
        save_snapshot(conn, second, firm, [make_partner(firm, "Jane Example")])
    record_events(conn, second)


def test_no_changes_names_the_monitored_firm_count(conn):
    two_quiet_runs(conn, ["alder"])
    text = visible_text(
        render(build_report_data(conn, demo_config()) | {"banner": None})
    )
    assert "No changes detected across the 1 monitored firm." in text


def test_baseline_firms_are_not_counted_as_monitored(conn):
    # Birch's first snapshot is a baseline: no change could show for it yet.
    two_quiet_runs(conn, ["alder", "birch"])
    data = build_report_data(conn, demo_config())
    assert data["monitored"] == 1
    text = visible_text(render(data | {"banner": None}))
    assert "Birch Lane LLP: baseline recorded" in text


def test_health_uses_recruiter_wording_with_details(conn):
    seed_demo_data(conn)
    health = {r["name"]: r for r in build_report_data(conn, demo_config())["health"]}
    assert health["Alder & Finch LLP"]["label"] == "Collected"
    assert health["Alder & Finch LLP"]["last_success"] == "19 Nov 2026"
    assert health["Cedar Row LLP"]["label"] == "Suspect (showing last good data)"
    assert health["Cedar Row LLP"]["last_success"] == "12 Nov 2026"
    assert health["Dogwood Hart LLP"]["label"] == "Blocked (unable to collect)"


def test_evidence_link_appears_in_the_row_details():
    config = OrbitConfig(
        user_agent="test",
        firms=[
            FirmConfig(
                id="x",
                name="X LLP",
                status="BLOCKED",
                reason="Cloudflare challenge",
                evidence_url="https://example.com/evidence.txt",
            )
        ],
    )
    html = render(
        {"run": None, "generated_at": "", "banner": None}
        | {"health": (health := health_rows(config, {}, {}, {}))}
        | {"coverage": coverage_summary(health)}
    )
    details = html.split("<details", 1)[1].split("</details>", 1)[0]
    assert "Cloudflare challenge" in details
    assert 'href="https://example.com/evidence.txt"' in details
