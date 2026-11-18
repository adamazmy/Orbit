"""Diff engine: turns two snapshots of a firm into change events.

Within a firm, people are matched by person_key (the site's stable ID):
- in the new snapshot only -> JOINED; in the old one only -> LEFT;
- in both -> OFFICE_CHANGED and/or TITLE_CHANGED if those differ.

Across firms, a LEFT at firm A and a JOINED at firm B are paired when the
normalised names match: in the same run, or with an unmatched event from the
last 4 weeks (firms update their sites at different speeds).
- Auto match (high score and identical first and last names): one MOVED
  replaces this run's LEFT/JOINED.
- Close but uncertain: the LEFT and JOINED stay as facts, and a POSSIBLE_MOVE
  flagged needs_review links them. A fuzzy match is never silently asserted.
The same normalised name at two firms in one run is flagged LISTED_AT_TWO_FIRMS.

Rules from PLAN.md: each firm is compared with its last OK snapshot (not just
the previous run), a firm that isn't OK this run produces no events, and a
firm's first OK snapshot is a baseline that produces no change events. It
still counts for the listed-at-two-firms check.

Known limitation: matching is by name, so a partner whose name changes while
moving (e.g. after marriage) shows as LEFT + JOINED, not MOVED.
"""

import sqlite3

from rapidfuzz import fuzz

from orbit import store
from orbit.models import Event, PartnerRecord
from orbit.normalise import normalise_name

AUTO_MATCH_SCORE = 92
REVIEW_MATCH_SCORE = 85
# How far back an unmatched LEFT/JOINED can still pair into a move.
MATCH_WINDOW_DAYS = 28


def diff_firm(
    firm_id: str, old: list[PartnerRecord], new: list[PartnerRecord]
) -> list[Event]:
    """Events for one firm between its last OK snapshot (old) and this run's (new)."""
    old_by_key = {record.person_key: record for record in old}
    new_by_key = {record.person_key: record for record in new}
    events = []
    for key in sorted(new_by_key.keys() - old_by_key.keys()):
        person = new_by_key[key]
        events.append(
            Event(
                type="JOINED",
                name=person.name,
                to_firm=firm_id,
                details=_person(person),
            )
        )
    for key in sorted(old_by_key.keys() - new_by_key.keys()):
        person = old_by_key[key]
        events.append(
            Event(
                type="LEFT",
                name=person.name,
                from_firm=firm_id,
                details=_person(person),
            )
        )
    for key in sorted(old_by_key.keys() & new_by_key.keys()):
        events.extend(_changes(firm_id, old_by_key[key], new_by_key[key]))
    return events


def _person(record: PartnerRecord) -> dict:
    """What an event remembers about a person (enough for the report and for MOVED)."""
    return {
        "person_key": record.person_key,
        "profile_url": record.profile_url,
        "title": record.title,
        "offices": record.offices,
    }


def _changes(firm_id: str, before: PartnerRecord, after: PartnerRecord) -> list[Event]:
    """OFFICE_CHANGED / TITLE_CHANGED for someone present in both snapshots."""
    events = []
    # Offices are compared as sets: the site re-ordering them isn't a change.
    if sorted(before.offices) != sorted(after.offices):
        details = {
            **_person(after),
            "old_offices": before.offices,
            "new_offices": after.offices,
        }
        events.append(
            Event(
                type="OFFICE_CHANGED",
                name=after.name,
                from_firm=firm_id,
                to_firm=firm_id,
                details=details,
            )
        )
    if before.title != after.title:
        details = {
            **_person(after),
            "old_title": before.title,
            "new_title": after.title,
        }
        events.append(
            Event(
                type="TITLE_CHANGED",
                name=after.name,
                from_firm=firm_id,
                to_firm=firm_id,
                details=details,
            )
        )
    return events


def name_score(name_a: str, name_b: str) -> float:
    """Similarity (0-100) of two names after normalisation.

    token_set_ratio so a middle name shown at one firm but not the other
    ("Michael Patrick Daly" vs "Michael Daly") doesn't block a match.
    """
    a, b = normalise_name(name_a), normalise_name(name_b)
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b)


def is_auto_match(name_a: str, name_b: str, score: float) -> bool:
    """A high score alone isn't enough: first and last names must also be identical.

    Short names score high on small differences ("Jane Doe" vs "Janet Doe" is ~94),
    so those are left for a human to review.
    """
    a, b = normalise_name(name_a).split(), normalise_name(name_b).split()
    return score >= AUTO_MATCH_SCORE and a[0] == b[0] and a[-1] == b[-1]


def match_moves(events: list[Event], earlier: list[Event] | None = None) -> list[Event]:
    """Pair LEFT (firm A) with JOINED (firm B) for the same person.

    events: this run's events. earlier: unmatched LEFT/JOINED events from recent
    runs (already stored); they can pair with this run's events, not each other.
    Best-scoring pairs go first and each event is used at most once.
    - Auto match: one MOVED replaces this run's LEFT/JOINED. An earlier event
      stays stored as it was; the MOVED records its id as matched_event_id.
    - Close but uncertain: the LEFT and JOINED stay as facts, and a
      POSSIBLE_MOVE (needs_review) links them.
    """
    pool = events + (earlier or [])
    is_earlier = [False] * len(events) + [True] * len(earlier or [])
    used: set[int] = set()
    replaced: set[int] = set()
    links = []
    for negative_score, i, j in _move_candidates(pool, is_earlier):
        if i in used or j in used:
            continue
        used.update((i, j))
        left, joined, score = pool[i], pool[j], -negative_score
        if is_auto_match(left.name, joined.name, score):
            links.append(_link("MOVED", left, joined, score))
            replaced.update(k for k in (i, j) if not is_earlier[k])
        else:
            links.append(_link("POSSIBLE_MOVE", left, joined, score))
    remaining = [event for index, event in enumerate(events) if index not in replaced]
    return remaining + links


def _move_candidates(
    pool: list[Event], is_earlier: list[bool]
) -> list[tuple[float, int, int]]:
    """(-score, left index, joined index) for every close-enough pair, best first."""
    candidates = []
    for i, left in enumerate(pool):
        if left.type != "LEFT":
            continue
        for j, joined in enumerate(pool):
            if joined.type != "JOINED" or joined.to_firm == left.from_firm:
                continue
            if is_earlier[i] and is_earlier[j]:
                continue  # both already in the past: not this run's business
            score = name_score(left.name, joined.name)
            if score >= REVIEW_MATCH_SCORE:
                candidates.append((-score, i, j))
    return sorted(candidates)


def _link(event_type: str, left: Event, joined: Event, score: float) -> Event:
    """A MOVED or POSSIBLE_MOVE joining a LEFT and a JOINED (both sides kept in details)."""
    details = {"from": {"name": left.name, **left.details}, "to": joined.details}
    earlier_id = left.id if left.id is not None else joined.id
    if earlier_id is not None:
        details["matched_event_id"] = earlier_id
    return Event(
        type=event_type,
        name=joined.name,
        from_firm=left.from_firm,
        to_firm=joined.to_firm,
        details=details,
        confidence=round(score, 1),
        needs_review=event_type == "POSSIBLE_MOVE",
    )


def find_dual_listings(snapshots: dict[str, list[PartnerRecord]]) -> list[Event]:
    """Flag a normalised name listed at two or more firms in the same run.

    Usually someone who moved while the old firm hasn't removed their profile
    yet; occasionally two different people with the same name. Either way a
    human should look. Exact normalised-name equality only.
    """
    by_name: dict[str, list[PartnerRecord]] = {}
    for records in snapshots.values():
        for record in records:
            norm = normalise_name(record.name)
            if norm:
                by_name.setdefault(norm, []).append(record)
    flags = []
    for norm in sorted(by_name):
        people = by_name[norm]
        firms = sorted({person.firm_id for person in people})
        if len(firms) < 2:
            continue
        flags.append(
            Event(
                type="LISTED_AT_TWO_FIRMS",
                name=people[0].name,
                from_firm=firms[0],
                to_firm=firms[1],
                details={
                    "firms": firms,
                    "profile_urls": [p.profile_url for p in people],
                },
                needs_review=True,
            )
        )
    return flags


def unmatched_recent_events(conn: sqlite3.Connection, run_id: int) -> list[Event]:
    """LEFT/JOINED events from the last MATCH_WINDOW_DAYS not already part of a MOVED."""
    recent = store.get_recent_events(conn, run_id, MATCH_WINDOW_DAYS)
    matched = {
        event.details.get("matched_event_id")
        for event in recent
        if event.type == "MOVED"
    }
    return [
        event
        for event in recent
        if event.type in ("LEFT", "JOINED") and event.id not in matched
    ]


def diff_run(conn: sqlite3.Connection, run_id: int) -> list[Event]:
    """All events for one run.

    Per-firm diffs for OK firms, then cross-firm move matching (this run plus
    the last 4 weeks), then the listed-at-two-firms check.
    """
    events = []
    snapshots = {}
    for firm_run in store.get_firm_runs(conn, run_id):
        if firm_run["status"] != "OK":
            continue  # SUSPECT / FAILED / BLOCKED / SKIPPED: no events this run
        firm_id = firm_run["firm_id"]
        snapshots[firm_id] = store.get_observations(conn, run_id, firm_id)
        previous = store.get_last_ok_run_id(conn, firm_id, before_run_id=run_id)
        if previous is None:
            continue  # first OK snapshot: baseline only
        old = store.get_observations(conn, previous, firm_id)
        events.extend(diff_firm(firm_id, old, snapshots[firm_id]))
    events = match_moves(events, unmatched_recent_events(conn, run_id))
    return events + find_dual_listings(snapshots)
