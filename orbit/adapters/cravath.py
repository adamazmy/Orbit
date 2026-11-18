"""Cravath, Swaine & Moore adapter: reads the site's own JSON search endpoint.

Cravath's directory page is rendered by JavaScript, but the data comes from a
plain GET endpoint (`/_site/search`) that is not disallowed by robots.txt. We
ask it for one position (Partner) in one practice (Corporate) and page through
the results. See RECON.md section 3.

Each hit carries far more than we keep (emails, phone numbers, photos, bios);
only name, title, office, profile URL and the stable `_id` are read. Only people
titled Partner are returned.

Known gap: Cravath has no M&A sub-practice, so "Corporate" also includes
capital markets and finance partners (documented in RECON.md).
"""

import logging
from typing import Any

from orbit.adapters.base import Adapter, AdapterError
from orbit.models import PartnerRecord

logger = logging.getLogger(__name__)


class CravathAdapter(Adapter):
    """Fetches Cravath's Corporate partners from the JSON search endpoint."""

    def fetch_partners(self) -> list[PartnerRecord]:
        """Fetch every hit the endpoint reports, then keep only people titled Partner."""
        source = self.firm.source
        total, records = self._fetch_all_hits(int(source["page_size"]))
        if len(records) != total:
            # A partial list would later look like partners leaving, so fail instead.
            raise AdapterError(
                f"Cravath reported {total} people but {len(records)} were collected"
            )
        partners = keep_partners(records, source["position_title"])
        if not partners:
            # Zero partners means the site or our query changed, not that everyone left.
            raise AdapterError("Cravath returned no partners")
        return partners

    def _fetch_all_hits(self, page_size: int) -> tuple[int, list[PartnerRecord]]:
        """One request of page_size; if the total is larger, page on with the offset."""
        records: dict[str, PartnerRecord] = {}
        offset = 0
        while True:
            payload = self._fetch_page(offset, page_size)
            total, page_records = parse_search_response(
                payload, self.firm.source, self.firm.id
            )
            # Keyed by person_key, so a person repeated across pages counts once.
            for record in page_records:
                records[record.person_key] = record
            offset += page_size
            if not page_records or len(records) >= total:
                return total, list(records.values())

    def _fetch_page(self, offset: int, page_size: int) -> dict[str, Any]:
        """GET one page of search results as parsed JSON."""
        source = self.firm.source
        params = {
            "po": source["position_id"],
            "pr": source["practice_id"],
            "s": page_size,
            "f": offset,
            "v": "attorney",
        }
        response = self.fetcher.get(self.firm.id, source["search_url"], params=params)
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError(
                f"Cravath search did not return JSON: {error}"
            ) from error


def keep_partners(
    records: list[PartnerRecord], partner_title: str
) -> list[PartnerRecord]:
    """Keep only records whose title is exactly the partner title (scope rule: Partner only)."""
    partners = [r for r in records if r.title.casefold() == partner_title.casefold()]
    dropped = len(records) - len(partners)
    if dropped:
        logger.info("Cravath: dropped %d people not titled %s", dropped, partner_title)
    return partners


def parse_search_response(
    payload: dict[str, Any], source: dict[str, Any], firm_id: str
) -> tuple[int, list[PartnerRecord]]:
    """Turn one page of search JSON into (total partners reported, records on this page).

    Pure function (no network), so it can be tested on a saved fixture.
    """
    try:
        bucket = payload["hits"][str(source["position_id"])]
        total = int(bucket["total"])
        hits = bucket["hits"]
    except (KeyError, TypeError, ValueError) as error:
        raise AdapterError(
            f"unexpected Cravath search response shape: {error!r}"
        ) from error
    records = [hit_to_record(hit, source, firm_id) for hit in hits]
    return total, records


def hit_to_record(
    hit: dict[str, Any], source: dict[str, Any], firm_id: str
) -> PartnerRecord:
    """Convert one search hit into a PartnerRecord, keeping only the fields we're allowed to."""
    try:
        person_id = str(hit["_id"])
        name = hit["name"].strip()
        relative_url = hit["url"]
    except (KeyError, AttributeError) as error:
        raise AdapterError(
            f"Cravath hit missing a required field: {error!r}"
        ) from error
    return PartnerRecord(
        name=name,
        firm_id=firm_id,
        title=(hit.get("title") or source["position_title"]).strip(),
        practices=[source["practice_name"]],
        offices=office_names(hit, source),
        profile_url=source["profile_base_url"] + relative_url,
        person_key=person_id,
    )


def office_names(hit: dict[str, Any], source: dict[str, Any]) -> list[str]:
    """Map the hit's office IDs to names using the office_names table in firms.yaml."""
    known = {str(office_id): name for office_id, name in source["office_names"].items()}
    names = []
    for entry in hit.get("offices_info") or []:
        office_id = str((entry.get("office_information") or {}).get("office", ""))
        if not office_id:
            continue
        if office_id not in known:
            # A new office shouldn't fail the whole firm; flag it so the config gets updated.
            logger.warning(
                "Cravath office id %s is not in firms.yaml office_names", office_id
            )
            names.append(f"Unknown office ({office_id})")
            continue
        names.append(known[office_id])
    return names
