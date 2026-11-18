"""Tests for the diff engine's pure functions (no network, no database, fictional people)."""

from orbit.diff import diff_firm, find_dual_listings, match_moves, name_score
from orbit.models import Event, PartnerRecord


def person(name, key=None, firm="firm-a", title="Partner", offices=("New York",)):
    key = key or name.lower().replace(" ", "-")
    return PartnerRecord(
        name=name,
        firm_id=firm,
        title=title,
        offices=list(offices),
        profile_url=f"https://{firm}.test/people/{key}",
        person_key=key,
    )


def types(events):
    return sorted(event.type for event in events)


# --- Within one firm --------------------------------------------------------


def test_no_change_no_events():
    snapshot = [person("Jane Example"), person("John Sample")]
    assert diff_firm("firm-a", snapshot, snapshot) == []


def test_joined_and_left():
    old = [person("Jane Example"), person("John Sample")]
    new = [person("Jane Example"), person("Alex Placeholder")]
    events = diff_firm("firm-a", old, new)
    assert types(events) == ["JOINED", "LEFT"]
    joined = next(e for e in events if e.type == "JOINED")
    assert (joined.name, joined.to_firm, joined.from_firm) == (
        "Alex Placeholder",
        "firm-a",
        None,
    )
    left = next(e for e in events if e.type == "LEFT")
    assert (left.name, left.from_firm, left.to_firm) == ("John Sample", "firm-a", None)


def test_office_change():
    old = [person("Jane Example", offices=["New York"])]
    new = [person("Jane Example", offices=["London"])]
    (event,) = diff_firm("firm-a", old, new)
    assert event.type == "OFFICE_CHANGED"
    assert event.details["old_offices"] == ["New York"]
    assert event.details["new_offices"] == ["London"]


def test_office_order_is_not_a_change():
    old = [person("Jane Example", offices=["New York", "London"])]
    new = [person("Jane Example", offices=["London", "New York"])]
    assert diff_firm("firm-a", old, new) == []


def test_title_change():
    old = [person("Jane Example", title="Partner")]
    new = [person("Jane Example", title="Managing Partner")]
    (event,) = diff_firm("firm-a", old, new)
    assert event.type == "TITLE_CHANGED"
    assert (event.details["old_title"], event.details["new_title"]) == (
        "Partner",
        "Managing Partner",
    )


def test_same_name_new_key_is_left_and_joined_within_a_firm():
    # Matching inside a firm is by person_key only; names are used across firms.
    old = [person("Jane Example", key="jane-1")]
    new = [person("Jane Example", key="jane-2")]
    assert types(diff_firm("firm-a", old, new)) == ["JOINED", "LEFT"]


# --- Across firms -----------------------------------------------------------


def left(name, firm="firm-a"):
    return Event(type="LEFT", name=name, from_firm=firm, details={"person_key": name})


def joined(name, firm="firm-b"):
    return Event(type="JOINED", name=name, to_firm=firm, details={"person_key": name})


def test_exact_name_across_firms_is_one_auto_move():
    events = match_moves([left("Jordan Fictional"), joined("Jordan Fictional")])
    (move,) = events
    assert move.type == "MOVED"
    assert (move.from_firm, move.to_firm) == ("firm-a", "firm-b")
    assert move.confidence == 100
    assert move.needs_review is False


def test_name_variants_auto_match():
    (move,) = match_moves([left("William J. Smith"), joined("Bill Smith")])
    assert move.type == "MOVED"
    assert move.needs_review is False


def test_middle_name_on_one_site_still_matches():
    (move,) = match_moves(
        [left("Michael Patrick Fictional"), joined("Michael Fictional")]
    )
    assert move.type == "MOVED"
    assert move.needs_review is False


def test_similar_but_different_names_are_flagged_not_auto_matched():
    assert name_score("Jane Doe", "Janet Doe") >= 92  # high score...
    events = match_moves([left("Jane Doe"), joined("Janet Doe")])
    # ...but never asserted: the facts stay, plus a flagged link.
    assert types(events) == ["JOINED", "LEFT", "POSSIBLE_MOVE"]


def test_review_band_keeps_facts_and_adds_flagged_link():
    score = name_score("Michael Chen", "Michelle Chen")
    assert 85 <= score < 92
    events = match_moves([left("Michael Chen"), joined("Michelle Chen")])
    assert types(events) == ["JOINED", "LEFT", "POSSIBLE_MOVE"]
    link = next(e for e in events if e.type == "POSSIBLE_MOVE")
    assert link.needs_review is True
    assert (link.from_firm, link.to_firm) == ("firm-a", "firm-b")
    assert 85 <= link.confidence < 92


def test_different_people_do_not_match():
    events = match_moves([left("Sarah Jones"), joined("Sam Jones")])
    assert types(events) == ["JOINED", "LEFT"]


def test_left_and_joined_at_same_firm_are_not_a_move():
    events = match_moves(
        [left("Jane Example", "firm-a"), joined("Jane Example", "firm-a")]
    )
    assert types(events) == ["JOINED", "LEFT"]


def test_each_event_is_used_once_and_best_match_wins():
    events = match_moves(
        [
            left("Jordan Fictional", "firm-a"),
            joined("Jordan Fictional", "firm-b"),
            joined("Jordan Fictionale", "firm-c"),
        ]
    )
    assert types(events) == ["JOINED", "MOVED"]
    move = next(e for e in events if e.type == "MOVED")
    assert move.to_firm == "firm-b"


def test_move_keeps_both_sides():
    (move,) = match_moves([left("Jordan Fictional"), joined("Jordan Fictional")])
    assert move.details["from"]["name"] == "Jordan Fictional"
    assert move.details["to"]["person_key"] == "Jordan Fictional"


# --- Pairing with earlier runs ----------------------------------------------


def stored(event, event_id):
    """An event as read back from the database (it has an id)."""
    return event.model_copy(update={"id": event_id})


def test_joined_now_pairs_with_left_from_earlier_run():
    earlier = [stored(left("Jordan Fictional", "firm-a"), 7)]
    events = match_moves([joined("Jordan Fictional", "firm-b")], earlier)
    (move,) = events  # this run's JOINED is replaced; the earlier LEFT stays stored
    assert move.type == "MOVED"
    assert (move.from_firm, move.to_firm) == ("firm-a", "firm-b")
    assert move.details["matched_event_id"] == 7


def test_left_now_pairs_with_joined_from_earlier_run():
    # The new firm listed them first; the old firm only removed them this week.
    earlier = [stored(joined("Jordan Fictional", "firm-b"), 9)]
    (move,) = match_moves([left("Jordan Fictional", "firm-a")], earlier)
    assert move.type == "MOVED"
    assert move.details["matched_event_id"] == 9


def test_two_earlier_events_are_not_paired_now():
    earlier = [
        stored(left("Jordan Fictional", "firm-a"), 1),
        stored(joined("Jordan Fictional", "firm-b"), 2),
    ]
    assert match_moves([], earlier) == []


# --- Listed at two firms -----------------------------------------------------


def test_same_person_listed_at_two_firms_is_flagged():
    snapshots = {
        "firm-a": [person("William J. Smith", firm="firm-a"), person("Jane Example")],
        "firm-b": [person("Bill Smith", firm="firm-b")],
    }
    (flag,) = find_dual_listings(snapshots)
    assert flag.type == "LISTED_AT_TWO_FIRMS"
    assert flag.needs_review is True
    assert flag.details["firms"] == ["firm-a", "firm-b"]


def test_different_names_are_not_dual_listings():
    snapshots = {
        "firm-a": [person("Jane Example", firm="firm-a")],
        "firm-b": [person("Janet Example", firm="firm-b")],
    }
    assert find_dual_listings(snapshots) == []
