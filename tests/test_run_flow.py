"""End-to-end run scenarios on a temporary database (no network, fictional people).

Each test plays a few weekly runs through the same steps `orbit run` uses after
fetching: save_snapshot (partner filter, health check, store) and
record_events (diff against last OK run, cross-firm moves).
"""

import pytest

from orbit import store
from orbit.cli import record_events, save_snapshot
from orbit.models import PartnerRecord


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "orbit-test.db")
    yield connection
    connection.close()


def partner(firm, name, title="Partner"):
    key = name.lower().replace(" ", "-")
    return PartnerRecord(
        name=name,
        firm_id=firm,
        title=title,
        offices=["New York"],
        profile_url=f"https://{firm}.test/people/{key}",
        person_key=key,
    )


def staff_name(firm, i):
    """A fictional name unique across firms once normalised, e.g. 'Ab Firma'.

    Letters only: the name normaliser drops digits and single-letter initials.
    """
    letters = "abcdefghijklmnopqrstuvwxyz"
    code = letters[i // 26] + letters[i % 26]
    return f"{code.title()} {firm.replace('-', '').title()}"


def staff(firm, count):
    """count fictional partners at a firm (the same people in every run)."""
    return [partner(firm, staff_name(firm, i)) for i in range(1, count + 1)]


def play_run(conn, snapshots, date=None):
    """One run: save each firm's snapshot, then diff. Returns (statuses, events).

    date (e.g. "2026-10-01") sets the run's start time, for the 4-week window.
    """
    run_id = store.create_run(conn)
    if date:
        conn.execute(
            "UPDATE runs SET started_at = ? WHERE id = ?",
            (f"{date}T09:00:00+00:00", run_id),
        )
    statuses = {
        firm: save_snapshot(conn, run_id, firm, records).status
        for firm, records in snapshots.items()
    }
    return statuses, record_events(conn, run_id)


def test_first_run_is_a_baseline_with_no_events(conn):
    statuses, events = play_run(
        conn, {"firm-a": staff("firm-a", 5), "firm-b": staff("firm-b", 5)}
    )
    assert statuses == {"firm-a": "OK", "firm-b": "OK"}
    assert events == []


def test_cross_firm_move_is_one_moved_event(conn):
    # Like Iliana Karaoglan: leaves firm A, appears at firm B the same week.
    mover = partner("firm-a", "Ilona Fictional")
    play_run(
        conn, {"firm-a": staff("firm-a", 5) + [mover], "firm-b": staff("firm-b", 5)}
    )

    arrived = partner("firm-b", "Ilona Fictional")
    _, events = play_run(
        conn, {"firm-a": staff("firm-a", 5), "firm-b": staff("firm-b", 5) + [arrived]}
    )

    assert [e.type for e in events] == ["MOVED"]
    move = events[0]
    assert (move.name, move.from_firm, move.to_firm) == (
        "Ilona Fictional",
        "firm-a",
        "firm-b",
    )
    assert move.needs_review is False
    # Stored in the events table (read back with a database id, otherwise identical).
    stored = store.get_events(conn, 2)
    assert [e.model_copy(update={"id": None}) for e in stored] == events


def test_collapse_to_suspect_produces_no_events(conn):
    play_run(conn, {"firm-a": staff("firm-a", 90)})
    statuses, events = play_run(conn, {"firm-a": staff("firm-a", 10)})
    assert statuses == {"firm-a": "SUSPECT"}
    assert events == []  # not 80 fake departures


def test_after_suspect_run_diff_uses_last_ok_snapshot(conn):
    play_run(conn, {"firm-a": staff("firm-a", 90)})
    play_run(conn, {"firm-a": staff("firm-a", 10)})  # broken scrape: SUSPECT
    newcomer = partner("firm-a", "Jordan Fictional")
    statuses, events = play_run(conn, {"firm-a": staff("firm-a", 90) + [newcomer]})
    # Compared with run 1 (last OK), not run 2: only the real joiner shows.
    assert statuses == {"firm-a": "OK"}
    assert [(e.type, e.name) for e in events] == [("JOINED", "Jordan Fictional")]


def test_suspect_firm_does_not_feed_moves(conn):
    play_run(conn, {"firm-a": staff("firm-a", 10), "firm-b": staff("firm-b", 5)})
    # firm-a collapses (SUSPECT), and firm-b gains someone with a firm-a name.
    _, events = play_run(
        conn,
        {
            "firm-a": staff("firm-a", 2),
            "firm-b": staff("firm-b", 5) + [partner("firm-b", staff_name("firm-a", 3))],
        },
    )
    assert [e.type for e in events] == ["JOINED"]


def test_non_partners_are_filtered_before_counting(conn):
    records = staff("firm-a", 4) + [
        partner("firm-a", "John Sample", title="Of Counsel")
    ]
    statuses, _ = play_run(conn, {"firm-a": records})
    assert store.get_firm_runs(conn, 1)[0]["partner_count"] == 4
    assert statuses == {"firm-a": "OK"}


def test_normalised_names_are_stored(conn):
    play_run(conn, {"firm-a": [partner("firm-a", "Dr. William J. Smith Jr.")]})
    row = conn.execute("SELECT full_name, name_norm FROM observations").fetchone()
    assert tuple(row) == ("Dr. William J. Smith Jr.", "william smith")


# --- Moves spread over several weeks -----------------------------------------


def move_over_two_runs(conn, second_date):
    """Ilona leaves firm A on 8 Oct; she appears at firm B on second_date."""
    mover = partner("firm-a", "Ilona Fictional")
    a, b = staff("firm-a", 5), staff("firm-b", 5)
    play_run(conn, {"firm-a": a + [mover], "firm-b": b}, date="2026-10-01")
    _, week_2 = play_run(conn, {"firm-a": a, "firm-b": b}, date="2026-10-08")
    arrived = partner("firm-b", "Ilona Fictional")
    _, later = play_run(conn, {"firm-a": a, "firm-b": b + [arrived]}, date=second_date)
    return week_2, later


def test_move_spread_over_weeks_within_window_is_moved(conn):
    week_2, later = move_over_two_runs(conn, second_date="2026-10-22")
    assert [e.type for e in week_2] == ["LEFT"]  # on its own, a departure
    (move,) = later  # two weeks later she appears at firm B
    assert move.type == "MOVED"
    assert (move.from_firm, move.to_firm) == ("firm-a", "firm-b")
    left_id = store.get_events(conn, 2)[0].id
    assert move.details["matched_event_id"] == left_id


def test_move_outside_window_stays_left_and_joined(conn):
    _, later = move_over_two_runs(conn, second_date="2026-11-19")  # 6 weeks later
    assert [e.type for e in later] == ["JOINED"]


def test_matched_earlier_event_is_not_reused(conn):
    move_over_two_runs(conn, second_date="2026-10-15")
    # A week later someone with the same name joins firm C: the old LEFT is taken.
    a, b = (
        staff("firm-a", 5),
        staff("firm-b", 5) + [partner("firm-b", "Ilona Fictional")],
    )
    play_run(
        conn,
        {"firm-a": a, "firm-b": b, "firm-c": staff("firm-c", 5)},
        date="2026-10-22",
    )
    snapshots = {
        "firm-a": a,
        "firm-b": b,
        "firm-c": staff("firm-c", 5) + [partner("firm-c", "Ilona Fictional")],
    }
    _, events = play_run(conn, snapshots, date="2026-10-29")
    assert sorted(e.type for e in events) == ["JOINED", "LISTED_AT_TWO_FIRMS"]


# --- Listed at two firms -------------------------------------------------------


def test_person_listed_at_two_firms_in_same_run_is_flagged(conn):
    a, b = staff("firm-a", 5), staff("firm-b", 5)
    play_run(conn, {"firm-a": a + [partner("firm-a", "Ilona Fictional")], "firm-b": b})
    # Firm B lists her; firm A hasn't removed her profile yet.
    snapshots = {
        "firm-a": a + [partner("firm-a", "Ilona Fictional")],
        "firm-b": b + [partner("firm-b", "Ilona Fictional")],
    }
    _, events = play_run(conn, snapshots)
    assert sorted(e.type for e in events) == ["JOINED", "LISTED_AT_TWO_FIRMS"]
    flag = next(e for e in events if e.type == "LISTED_AT_TWO_FIRMS")
    assert flag.needs_review is True
    assert flag.details["firms"] == ["firm-a", "firm-b"]
