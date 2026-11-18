"""Cravath adapter tests: run against a saved, trimmed search response (no network).

The fixture keeps the exact structure of the 18 Nov response, cut down to the
fields the adapter reads, but the people are fictional: no real names, IDs,
emails or phone numbers. Between them the 3 hits cover all three real office
IDs and a partner whose `title` is empty.
"""

import json
from pathlib import Path

import pytest

from orbit.adapters.base import AdapterError
from orbit.adapters.cravath import (
    CravathAdapter,
    hit_to_record,
    keep_partners,
    parse_search_response,
)
from orbit.models import load_config

FIXTURE = (
    Path(__file__).parent / "fixtures" / "cravath" / "search_partner_corporate.json"
)
CONFIG = Path(__file__).parent.parent / "config" / "firms.yaml"


@pytest.fixture
def firm():
    """Cravath's real entry from config/firms.yaml, so tests also catch config mistakes."""
    return next(f for f in load_config(CONFIG).firms if f.id == "cravath")


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def by_key(records):
    return {record.person_key: record for record in records}


def make_hit(person_id, title="Partner", office=5413):
    """A minimal hit in the endpoint's real shape."""
    return {
        "_id": person_id,
        "name": f"Test Person {person_id}",
        "title": title,
        "url": f"/people/test-person-{person_id}.html",
        "offices_info": [
            {"type": "office_information", "office_information": {"office": office}}
        ],
    }


def make_page(hits, total):
    return {"mode": "raw_json", "hits": {"5499": {"total": total, "hits": hits}}}


# --- Parsing ---------------------------------------------------------------


def test_parses_every_hit(firm, payload):
    total, records = parse_search_response(payload, firm.source, firm.id)
    assert total == 3
    assert len(records) == 3


def test_maps_fields(firm, payload):
    _, records = parse_search_response(payload, firm.source, firm.id)
    sample = by_key(records)["90002"]
    assert sample.name == "John Sample"
    assert sample.firm_id == "cravath"
    assert sample.title == "Partner"
    assert sample.practices == ["Corporate"]
    assert sample.offices == ["London"]
    assert sample.profile_url == "https://www.cravath.com/people/john-sample.html"


def test_maps_every_office_id(firm, payload):
    _, records = parse_search_response(payload, firm.source, firm.id)
    offices = {record.offices[0] for record in records}
    assert offices == {"New York", "London", "Washington, D.C."}


def test_missing_title_falls_back_to_position(firm, payload):
    _, records = parse_search_response(payload, firm.source, firm.id)
    assert by_key(records)["90001"].title == "Partner"


def test_unknown_office_is_flagged_not_dropped(firm):
    record = hit_to_record(make_hit("1", office=99999), firm.source, firm.id)
    assert record.offices == ["Unknown office (99999)"]


def test_unexpected_shape_raises(firm):
    with pytest.raises(AdapterError):
        parse_search_response({"mode": "raw_json", "hits": {}}, firm.source, firm.id)


def test_hit_missing_name_raises(firm):
    hit = make_hit("1")
    del hit["name"]
    with pytest.raises(AdapterError):
        hit_to_record(hit, firm.source, firm.id)


# --- Partner-only filter ---------------------------------------------------


def test_keeps_only_partners(firm):
    hits = [make_hit("1"), make_hit("2", title="Of Counsel"), make_hit("3")]
    _, records = parse_search_response(make_page(hits, 3), firm.source, firm.id)
    partners = keep_partners(records, "Partner")
    assert sorted(r.person_key for r in partners) == ["1", "3"]


# --- Fetching (fake fetcher, no network) -----------------------------------


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeFetcher:
    """Stands in for the polite fetcher: returns canned pages and records each request."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, firm_id, url, params=None):
        self.calls.append(params)
        return FakeResponse(self.pages[len(self.calls) - 1])


def split_into_pages(payload, page_size):
    """Rebuild the fixture as several pages, the way the endpoint returns them."""
    bucket = payload["hits"]["5499"]
    hits = bucket["hits"]
    return [
        make_page(hits[i : i + page_size], bucket["total"])
        for i in range(0, len(hits), page_size)
    ]


def test_one_request_when_everything_fits(firm, payload):
    fetcher = FakeFetcher([payload])
    records = CravathAdapter(firm, fetcher).fetch_partners()
    assert len(records) == 3
    assert fetcher.calls == [
        {"po": "5499", "pr": "3275", "s": 100, "f": 0, "v": "attorney"}
    ]


def test_pages_when_total_exceeds_returned(firm, payload):
    firm.source["page_size"] = 2
    fetcher = FakeFetcher(split_into_pages(payload, 2))
    records = CravathAdapter(firm, fetcher).fetch_partners()
    assert len(records) == 3
    assert [call["f"] for call in fetcher.calls] == [0, 2]


def test_fails_on_incomplete_list(firm, payload):
    firm.source["page_size"] = 2
    pages = split_into_pages(payload, 2)[:1] + [make_page([], 3)]
    with pytest.raises(AdapterError):
        CravathAdapter(firm, FakeFetcher(pages)).fetch_partners()


def test_never_returns_empty_list(firm):
    with pytest.raises(AdapterError):
        CravathAdapter(firm, FakeFetcher([make_page([], 0)])).fetch_partners()


def test_fails_when_nobody_is_a_partner(firm):
    page = make_page([make_hit("1", title="Of Counsel")], 1)
    with pytest.raises(AdapterError):
        CravathAdapter(firm, FakeFetcher([page])).fetch_partners()
