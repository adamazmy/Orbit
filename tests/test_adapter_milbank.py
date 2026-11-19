"""Milbank adapter tests: run against a saved search response (no network).

The fixture keeps the structure of the 19 Nov response (a JSON wrapper around
rendered `li.card--attorney` cards) with fictional people. The email and phone
blocks are left out of the fixture; the photo path is fictional and must be ignored.
"""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from orbit.adapters.base import AdapterError
from orbit.adapters.milbank import MilbankAdapter, parse_search_response
from orbit.models import load_config

FIXTURE = (
    Path(__file__).parent / "fixtures" / "milbank" / "search_partner_practice.json"
)
CONFIG = Path(__file__).parent.parent / "config" / "firms.yaml"
BASE_URL = "https://www.milbank.com"


@pytest.fixture
def firm():
    """Milbank's real entry from config/firms.yaml, so tests also catch config mistakes."""
    return next(f for f in load_config(CONFIG).firms if f.id == "milbank")


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def cards_of(payload):
    """The fixture's cards as HTML strings, to build other pages from."""
    html = payload["rendered_view"]
    return ["<li" + part.split("</li>")[0] + "</li>" for part in html.split("<li")[1:]]


def make_page(cards, total):
    """A search page in the endpoint's real shape."""
    return {
        "mode": "json_wrapped_html",
        "totals": {"ALL": total},
        "rendered_view": "<ul>" + "".join(cards) + "</ul>",
    }


# --- Parsing ---------------------------------------------------------------


def test_parses_total_and_cards(payload):
    total, records = parse_search_response(payload, BASE_URL, "milbank")
    assert total == 3
    assert [r.person_key for r in records] == [
        "riley-q-placeholder",
        "morgan-example",
        "casey-sample",
    ]


def test_maps_fields(payload):
    _, records = parse_search_response(payload, BASE_URL, "milbank")
    riley = records[0]
    assert riley.name == "Riley Q. Placeholder"
    assert riley.firm_id == "milbank"
    assert riley.title == "Partner"
    assert riley.offices == ["New York"]
    assert riley.profile_url == (
        "https://www.milbank.com/en/professionals/riley-q-placeholder.html"
    )


def test_never_reads_photo(payload):
    _, records = parse_search_response(payload, BASE_URL, "milbank")
    stored_text = " ".join(
        str(value) for r in records for value in r.model_dump().values()
    )
    assert "/a/web/" not in stored_text


def test_raw_json_mode_raises():
    # Without the `html` flag the endpoint answers in another shape.
    with pytest.raises(AdapterError):
        parse_search_response({"mode": "raw_json", "hits": {}}, BASE_URL, "milbank")


def test_card_without_profile_link_raises():
    page = make_page(['<li class="card--attorney"><span>No link</span></li>'], 1)
    with pytest.raises(AdapterError):
        parse_search_response(page, BASE_URL, "milbank")


# --- Fetching (fake fetcher, no network) -----------------------------------


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeFetcher:
    """Stands in for the polite fetcher: returns canned pages and records each URL."""

    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, firm_id, url, params=None):
        assert params is None  # the adapter builds the whole URL itself
        self.urls.append(url)
        return FakeResponse(self.pages[len(self.urls) - 1])


def query_of(url):
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_one_query_per_practice_with_partner_filter(firm, payload):
    riley, morgan, casey = cards_of(payload)
    pages = [
        make_page([riley, morgan], 2),
        make_page([riley], 1),
        make_page([casey], 1),
    ]
    fetcher = FakeFetcher(pages)
    MilbankAdapter(firm, fetcher).fetch_partners()

    assert [query_of(u)["p"] for u in fetcher.urls] == [["305"], ["306"], ["823"]]
    first = query_of(fetcher.urls[0])
    assert first["v"] == ["attorney"]
    assert first["t"] == ["20012"]
    assert first["20012_name"] == ["Partner"]
    assert first["f"] == ["0"] and first["s"] == ["100"]
    assert "q" not in first
    assert fetcher.urls[0].endswith("&html")  # bare flag, not html=


def test_merges_practices_by_profile(firm, payload):
    riley, morgan, casey = cards_of(payload)
    pages = [
        make_page([riley, morgan], 2),
        make_page([riley], 1),
        make_page([casey], 1),
    ]
    records = MilbankAdapter(firm, FakeFetcher(pages)).fetch_partners()
    practices = {r.person_key: r.practices for r in records}
    assert practices == {
        "riley-q-placeholder": ["Private Equity", "Mergers and Acquisitions"],
        "morgan-example": ["Private Equity"],
        "casey-sample": ["Distressed M&A"],
    }


def test_pages_with_offset_until_total_reached(firm, payload):
    firm.source["page_size"] = 2
    riley, morgan, casey = cards_of(payload)
    pages = [
        make_page([riley, morgan], 3),
        make_page([casey], 3),
        make_page([riley], 1),
        make_page([morgan], 1),
    ]
    fetcher = FakeFetcher(pages)
    records = MilbankAdapter(firm, fetcher).fetch_partners()
    assert len(records) == 3
    assert [query_of(u)["f"] for u in fetcher.urls[:2]] == [["0"], ["2"]]


def test_fails_when_unique_count_differs_from_total(firm, payload):
    riley, morgan, _ = cards_of(payload)
    pages = [make_page([riley, morgan, riley], 3)]
    with pytest.raises(AdapterError):
        MilbankAdapter(firm, FakeFetcher(pages)).fetch_partners()


def test_never_returns_empty_list(firm):
    pages = [make_page([], 0)] * 3
    with pytest.raises(AdapterError):
        MilbankAdapter(firm, FakeFetcher(pages)).fetch_partners()
