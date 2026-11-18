"""Weil adapter tests: run against a saved listing response (no network).

The fixture keeps the exact structure of the 18 Nov response (JSON groups of
HTML snippets) with fictional people. "Jordan Fictional" appears in both the
Leadership group and the A-Z groups, as real leaders do. The phone line is
left out of the fixture; the photo and bio are fictional and must be ignored.
"""

import json
from pathlib import Path

import pytest

from orbit.adapters.base import AdapterError
from orbit.adapters.weil import WeilAdapter, parse_listing_response, snippet_to_record
from orbit.models import load_config

FIXTURE = Path(__file__).parent / "fixtures" / "weil" / "listing_partner_ma_pe.json"
CONFIG = Path(__file__).parent.parent / "config" / "firms.yaml"
BASE_URL = "https://www.weil.com"


@pytest.fixture
def firm():
    """Weil's real entry from config/firms.yaml, so tests also catch config mistakes."""
    return next(f for f in load_config(CONFIG).firms if f.id == "weil")


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def all_snippets(payload):
    return [s for g in payload["SearchGroups"] for s in g["SearchResultItemsAsHtml"]]


def make_page(snippets, total, total_pages=1):
    """A listing page in the endpoint's real shape, with every snippet in one group."""
    return {
        "TotalCount": total,
        "TotalPages": total_pages,
        "SearchGroups": [{"Title": "A", "SearchResultItemsAsHtml": snippets}],
    }


# --- Parsing ---------------------------------------------------------------


def test_parses_every_snippet_including_repeats(payload):
    total, total_pages, records = parse_listing_response(payload, BASE_URL, "weil")
    assert (total, total_pages) == (3, 1)
    assert len(records) == 4  # Jordan appears in Leadership and under J


def test_maps_fields(payload):
    _, _, records = parse_listing_response(payload, BASE_URL, "weil")
    jordan = records[0]
    assert jordan.name == "Jordan Fictional"
    assert jordan.firm_id == "weil"
    assert jordan.title == "Partner"
    assert jordan.offices == ["New York", "London"]
    assert jordan.practices == ["Mergers & Acquisitions", "Private Equity"]
    assert jordan.profile_url == "https://www.weil.com/people/jordan-fictional"
    assert jordan.person_key == "jordan-fictional"


def test_keeps_title_variants(payload):
    _, _, records = parse_listing_response(payload, BASE_URL, "weil")
    titles = {r.person_key: r.title for r in records}
    assert titles["sam-sample"] == "Executive Partner"


def test_never_reads_bio_or_photo(payload):
    _, _, records = parse_listing_response(payload, BASE_URL, "weil")
    stored_text = " ".join(
        str(value) for r in records for value in r.model_dump().values()
    )
    assert "fictional person" not in stored_text
    assert "/-/media/" not in stored_text


def test_snippet_without_profile_link_raises():
    with pytest.raises(AdapterError):
        snippet_to_record("<div><h3>No link</h3></div>", BASE_URL, "weil")


def test_unexpected_shape_raises():
    with pytest.raises(AdapterError):
        parse_listing_response({"TotalCount": 3}, BASE_URL, "weil")


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


def test_one_request_dedupes_leadership(firm, payload):
    fetcher = FakeFetcher([payload])
    records = WeilAdapter(firm, fetcher).fetch_partners()
    assert sorted(r.person_key for r in records) == [
        "avery-example",
        "jordan-fictional",
        "sam-sample",
    ]
    assert len(fetcher.calls) == 1
    params = fetcher.calls[0]
    assert params["pagenum"] == 1 and params["pagesize"] == 100
    assert params["itemId"] == "{2186BD5B-A7D8-40E1-87CD-9246016A7816}"
    assert params["param_role_s"] == "{9B7FDA9B-B476-422F-9F30-68DC43AC4126}"
    practice_param = (
        "param_practices_sm|primary_practice_s|additional_primary_practices_sm"
    )
    assert params[practice_param] == (
        "{2F2727CC-4E6C-443B-8777-5FE134DFDDB5},{C4F4F120-2CEF-4831-B05F-CE442ECB0789}"
    )


def test_pages_until_total_reached(firm, payload):
    jordan, avery, _, sam = all_snippets(payload)
    pages = [
        make_page([jordan, avery], 3, total_pages=2),
        make_page([sam], 3, total_pages=2),
    ]
    fetcher = FakeFetcher(pages)
    records = WeilAdapter(firm, fetcher).fetch_partners()
    assert len(records) == 3
    assert [call["pagenum"] for call in fetcher.calls] == [1, 2]


def test_fails_when_unique_count_differs_from_total(firm, payload):
    jordan, avery, _, _ = all_snippets(payload)
    with pytest.raises(AdapterError):
        WeilAdapter(
            firm, FakeFetcher([make_page([jordan, avery, jordan], 3)])
        ).fetch_partners()


def test_never_returns_empty_list(firm):
    with pytest.raises(AdapterError):
        WeilAdapter(firm, FakeFetcher([make_page([], 0)])).fetch_partners()
