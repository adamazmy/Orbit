"""Weil, Gotshal & Manges adapter: reads the JSON listing endpoint behind weil.com/people.

The directory page fills its results by JavaScript from
`/WeilPeopleListing/Execute`. We ask it for the Partner role in the M&A and
Private Equity practices (IDs in firms.yaml). It answers with JSON whose
`SearchGroups` (a "Leadership" group plus A-Z groups) each hold HTML snippets,
one per person. See RECON.md section 6.

The Leadership group repeats people who also appear in the A-Z groups, so
people are deduplicated by profile URL, and the unique count must match the
endpoint's TotalCount.

From each snippet we read only name, title, offices, practices and profile
link. Photos, phone numbers and bio extracts are in the snippet but never read.
"""

import logging
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from orbit.adapters.base import Adapter, AdapterError
from orbit.models import PartnerRecord

logger = logging.getLogger(__name__)

PROFILE_PREFIX = "/people/"
OFFICE_PREFIX = "/locations/"


class WeilAdapter(Adapter):
    """Fetches Weil's M&A and Private Equity partners from the listing endpoint."""

    def fetch_partners(self) -> list[PartnerRecord]:
        """Fetch every page, dedupe by profile URL, and check the count against TotalCount."""
        total, partners = self._fetch_all(int(self.firm.source["page_size"]))
        if len(partners) != total:
            # A partial list would later look like partners leaving, so fail instead.
            raise AdapterError(
                f"Weil reported {total} partners but {len(partners)} unique were collected"
            )
        if not partners:
            # Zero partners means the site or our query changed, not that everyone left.
            raise AdapterError("Weil returned no partners")
        return partners

    def _fetch_all(self, page_size: int) -> tuple[int, list[PartnerRecord]]:
        """Request page 1 with page_size; keep paging with pagenum until TotalCount is reached."""
        by_url: dict[str, PartnerRecord] = {}
        page_number = 1
        while True:
            payload = self._fetch_page(page_number, page_size)
            total, total_pages, records = parse_listing_response(
                payload, self.firm.source["profile_base_url"], self.firm.id
            )
            new_on_page = 0
            for record in records:
                # Leadership repeats people from the A-Z groups: keep each profile once.
                if record.profile_url not in by_url:
                    by_url[record.profile_url] = record
                    new_on_page += 1
            if len(by_url) >= total or page_number >= total_pages or new_on_page == 0:
                return total, list(by_url.values())
            page_number += 1

    def _fetch_page(self, page_number: int, page_size: int) -> dict[str, Any]:
        """GET one page of the listing as parsed JSON."""
        source = self.firm.source
        params = {
            "pagenum": page_number,
            "pagesize": page_size,
            "itemId": source["item_id"],
            source["practice_param"]: ",".join(source["practice_ids"]),
            source["role_param"]: source["role_id"],
        }
        response = self.fetcher.get(self.firm.id, source["listing_url"], params=params)
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError(f"Weil listing did not return JSON: {error}") from error


def parse_listing_response(
    payload: dict[str, Any], base_url: str, firm_id: str
) -> tuple[int, int, list[PartnerRecord]]:
    """Turn one page of listing JSON into (TotalCount, TotalPages, records).

    Records can repeat (Leadership plus A-Z); the caller dedupes.
    Pure function (no network), so it can be tested on a saved fixture.
    """
    try:
        total = int(payload["TotalCount"])
        total_pages = int(payload["TotalPages"])
        snippets = [
            snippet
            for group in payload["SearchGroups"]
            for snippet in group["SearchResultItemsAsHtml"]
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise AdapterError(f"unexpected Weil listing shape: {error!r}") from error
    records = [snippet_to_record(snippet, base_url, firm_id) for snippet in snippets]
    return total, total_pages, records


def snippet_to_record(snippet: str, base_url: str, firm_id: str) -> PartnerRecord:
    """Read one person's HTML snippet into a PartnerRecord (name, title, offices, practices, URL)."""
    tree = LexborHTMLParser(snippet)
    link = tree.css_first("h3 a")
    title = tree.css_first("p.ppl-item-status b")
    href = link.attributes.get("href") if link else None
    if not link or not title or not href or not href.startswith(PROFILE_PREFIX):
        raise AdapterError(
            f"Weil snippet missing name, title or profile link: {href!r}"
        )
    slug = href.removeprefix(PROFILE_PREFIX).strip("/")
    if not slug:
        raise AdapterError(f"Weil profile link has no slug: {href!r}")
    return PartnerRecord(
        name=link.text(strip=True),
        firm_id=firm_id,
        title=title.text(strip=True),
        practices=[a.text(strip=True) for a in tree.css("p.ppl-item-categories a")],
        offices=[
            a.text(strip=True)
            for a in tree.css("p.ppl-item-status a")
            if (a.attributes.get("href") or "").startswith(OFFICE_PREFIX)
        ],
        profile_url=base_url + href,
        person_key=slug,
    )
