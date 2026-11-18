"""The adapter interface: what every firm adapter must look like.

An adapter knows how ONE firm's website publishes its partner list, and nothing
else. It fetches (always through the shared Fetcher) and parses, then returns a
list of PartnerRecord. Normalising, health checks, storing, diffing and
reporting are shared code and never firm-specific.

Firm-specific values (URLs, practice IDs...) come from the firm's `source`
section in config/firms.yaml, available as self.firm.source.

Shared parsing helpers go here only once two adapters actually need them.
"""

from abc import ABC, abstractmethod

from orbit.fetch import Fetcher
from orbit.models import FirmConfig, PartnerRecord


class AdapterError(Exception):
    """The site answered, but not in the shape the adapter expects.

    For example, Davis Polk's API key can't be found in the page. The run marks
    the firm FAILED instead of reporting zero partners.
    """


class Adapter(ABC):
    """Base class for firm adapters. Subclasses implement fetch_partners()."""

    def __init__(self, firm: FirmConfig, fetcher: Fetcher) -> None:
        self.firm = firm
        self.fetcher = fetcher

    @abstractmethod
    def fetch_partners(self) -> list[PartnerRecord]:
        """Fetch and parse this firm's in-scope partners.

        Raise FetchBlocked / FetchFailed (from orbit.fetch) or AdapterError
        rather than returning an empty list when something goes wrong.
        """
