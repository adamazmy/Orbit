"""Data shapes shared across Orbit, plus loading of config/firms.yaml.

PartnerRecord is the one thing every adapter returns, so the rest of the
pipeline (normalise, health, store, diff, report) never needs to know which
firm or website a partner came from.

Event is one change the diff found between two snapshots (joined, left,
moved firm, changed office or title).

FirmConfig and OrbitConfig validate config/firms.yaml with pydantic, so a typo
in the config fails loudly at startup instead of halfway through a run.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

FirmStatus = Literal["ACTIVE", "BLOCKED", "OUT_OF_SCOPE", "NOT_ASSESSED"]
EventType = Literal[
    "JOINED",
    "LEFT",
    "MOVED",
    "OFFICE_CHANGED",
    "TITLE_CHANGED",
    # Review flags (needs_review=True), kept beside the facts they're about:
    "POSSIBLE_MOVE",  # a LEFT and a JOINED whose names are close but not certain
    "LISTED_AT_TWO_FIRMS",  # the same normalised name at two firms in one run
]


class PartnerRecord(BaseModel):
    """One partner as seen on one firm's website during one run.

    Only public professional facts are kept (data minimisation): no emails,
    phone numbers, photos or bio text.
    """

    name: str
    firm_id: str
    title: str
    practices: list[str] = Field(default_factory=list)
    offices: list[str] = Field(default_factory=list)
    profile_url: str
    # Stable within a firm across runs (profile URL or the site's own ID), so the
    # diff can tell "same person, new office" apart from "one left, one joined".
    person_key: str


class Event(BaseModel):
    """One change found by the diff. Mirrors a row of the events table.

    from_firm / to_firm: LEFT has only from_firm, JOINED only to_firm, MOVED and
    POSSIBLE_MOVE both, OFFICE_CHANGED / TITLE_CHANGED the same firm in both, and
    LISTED_AT_TWO_FIRMS the two firms.
    """

    # The events table's id: set when read back from the database, None before saving.
    id: int | None = None
    type: EventType
    name: str
    from_firm: str | None = None
    to_firm: str | None = None
    # Old/new values and profile links, e.g. {"old_offices": [...], "new_offices": [...]}.
    details: dict[str, Any] = Field(default_factory=dict)
    # MOVED only: fuzzy name-match score (0-100), and whether a human should check it.
    confidence: float | None = None
    needs_review: bool = False


class FirmConfig(BaseModel):
    """One firm entry from config/firms.yaml."""

    id: str
    name: str
    enabled: bool = False
    status: FirmStatus
    reason: str | None = None
    directory_url: str | None = None
    # Link to the evidence for a BLOCKED status, shown in the report's data health section.
    evidence_url: str | None = None
    # Adapter-specific settings (URLs, practice IDs...). Only that firm's adapter reads them.
    source: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_status_is_consistent(self) -> "FirmConfig":
        """Reject entries that would silently scrape a blocked firm or hide why a firm is off."""
        if self.enabled and self.status != "ACTIVE":
            raise ValueError(
                f"firm '{self.id}' is enabled but its status is {self.status}"
            )
        if self.status != "ACTIVE" and not self.reason:
            raise ValueError(f"firm '{self.id}' has status {self.status} but no reason")
        return self


class OrbitConfig(BaseModel):
    """The whole of config/firms.yaml."""

    user_agent: str
    firms: list[FirmConfig]

    @model_validator(mode="after")
    def check_firm_ids_are_unique(self) -> "OrbitConfig":
        """Firm IDs are database keys, so a duplicate would merge two firms' data."""
        seen: set[str] = set()
        for firm in self.firms:
            if firm.id in seen:
                raise ValueError(f"duplicate firm id '{firm.id}' in config")
            seen.add(firm.id)
        return self

    def enabled_firms(self) -> list[FirmConfig]:
        """Firms that should be scraped this run."""
        return [firm for firm in self.firms if firm.enabled]


def load_config(path: Path) -> OrbitConfig:
    """Read and validate config/firms.yaml."""
    with path.open(encoding="utf-8") as file:
        raw = yaml.safe_load(file)
    return OrbitConfig.model_validate(raw)
