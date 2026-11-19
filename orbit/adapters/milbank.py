"""Milbank adapter: reads the site's search endpoint, which returns HTML cards.

Milbank runs the same search platform as Cravath (`/_site/search`, `v=attorney`,
offset `f`), but here the endpoint answers with `json_wrapped_html`: a JSON
wrapper holding `totals.ALL` and the rendered result cards (`li.card--attorney`).

Repeated practice filters (`p=`) are combined as AND, so we query one practice
at a time and merge the results by profile URL. The cards carry no practice, so
each person gets the practices whose query returned them. Each query's unique
count must match its `totals.ALL`, or the firm fails rather than looking short.

From each card we read only name, title, office and profile link. Cards also
hold an email address, a phone number and a photo: never read.
"""

import logging
from typing import Any
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser

from orbit.adapters.base import Adapter, AdapterError
from orbit.models import PartnerRecord

logger = logging.getLogger(__name__)

PROFILE_PREFIX = "/en/professionals/"
HTML_MODE = "json_wrapped_html"


class MilbankAdapter(Adapter):
    """Fetches Milbank's partners in the configured practices, one practice at a time."""

    def fetch_partners(self) -> list[PartnerRecord]:
        """Query each practice, merge by profile URL, and record who was in which practice."""
        by_url: dict[str, PartnerRecord] = {}
        practices: dict[str, list[str]] = {}
        for practice_id, practice_name in self.firm.source["practices"].items():
            for record in self._fetch_practice(str(practice_id)):
                by_url.setdefault(record.profile_url, record)
                practices.setdefault(record.profile_url, []).append(practice_name)
        if not by_url:
            # Zero partners means the site or our query changed, not that everyone left.
            raise AdapterError("Milbank returned no partners")
        return [
            record.model_copy(update={"practices": practices[url]})
            for url, record in by_url.items()
        ]

    def _fetch_practice(self, practice_id: str) -> list[PartnerRecord]:
        """Page through one practice with the offset until totals.ALL people are collected."""
        page_size = int(self.firm.source["page_size"])
        by_url: dict[str, PartnerRecord] = {}
        offset = 0
        while True:
            payload = self._fetch_page(practice_id, offset, page_size)
            total, records = parse_search_response(
                payload, self.firm.source["profile_base_url"], self.firm.id
            )
            new_on_page = 0
            for record in records:
                if record.profile_url not in by_url:
                    by_url[record.profile_url] = record
                    new_on_page += 1
            offset += page_size
            # A short page is the last one: asking for the next would be a wasted request.
            if len(by_url) >= total or new_on_page == 0 or len(records) < page_size:
                break
        if len(by_url) != total:
            # A partial list would later look like partners leaving, so fail instead.
            raise AdapterError(
                f"Milbank practice {practice_id} reported {total} people "
                f"but {len(by_url)} unique were collected"
            )
        return list(by_url.values())

    def _fetch_page(self, practice_id: str, offset: int, page_size: int) -> dict:
        """GET one page of one practice's results as parsed JSON."""
        source = self.firm.source
        params = {
            "v": "attorney",
            "t": source["position_type"],
            f"{source['position_type']}_name": source["position_name"],
            "p": practice_id,
            "f": offset,
            "s": page_size,
        }
        # `html` is a bare flag (no value) that asks for rendered cards; built by
        # hand because encoding it as a parameter would send `html=`.
        url = f"{source['search_url']}?{urlencode(params)}&html"
        response = self.fetcher.get(self.firm.id, url)
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError(
                f"Milbank search did not return JSON: {error}"
            ) from error


def parse_search_response(
    payload: dict[str, Any], base_url: str, firm_id: str
) -> tuple[int, list[PartnerRecord]]:
    """Turn one page of search JSON into (totals.ALL, records on this page).

    Pure function (no network), so it can be tested on a saved fixture.
    """
    try:
        if payload["mode"] != HTML_MODE:
            raise ValueError(f"mode is {payload['mode']!r}, expected {HTML_MODE!r}")
        total = int(payload["totals"]["ALL"])
        html = payload["rendered_view"]
    except (KeyError, TypeError, ValueError) as error:
        raise AdapterError(f"unexpected Milbank search shape: {error!r}") from error
    cards = LexborHTMLParser(html or "<ul></ul>").css("li.card--attorney")
    return total, [card_to_record(card, base_url, firm_id) for card in cards]


def card_to_record(card, base_url: str, firm_id: str) -> PartnerRecord:
    """Read one attorney card into a PartnerRecord (name, title, offices, URL).

    The card's practices are filled in by the adapter from the query that found it.
    """
    name = card.css_first(".card__first-name")
    link = card.css_first(".card__first-line a")
    title = card.css_first(".card__second-line")
    href = link.attributes.get("href") if link else None
    if not name or not title or not href or not href.startswith(PROFILE_PREFIX):
        raise AdapterError(
            f"Milbank card missing name, title or profile link: {href!r}"
        )
    slug = href.removeprefix(PROFILE_PREFIX).removesuffix(".html").strip("/")
    if not slug:
        raise AdapterError(f"Milbank profile link has no slug: {href!r}")
    return PartnerRecord(
        name=name.text(strip=True),
        firm_id=firm_id,
        title=title.text(strip=True),
        practices=[],
        offices=[a.text(strip=True) for a in card.css(".card__office-name a")],
        profile_url=base_url + href,
        person_key=slug,
    )
