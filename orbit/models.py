"""Data shapes shared across Orbit, plus loading of config/firms.yaml.

PartnerRecord is the one thing every adapter returns, so the rest of the
pipeline (normalise, health, store, diff, report) never needs to know which
firm or website a partner came from.

FirmConfig and OrbitConfig validate config/firms.yaml with pydantic, so a typo
in the config fails loudly at startup instead of halfway through a run.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

FirmStatus = Literal["ACTIVE", "BLOCKED", "OUT_OF_SCOPE", "NOT_ASSESSED"]


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


class FirmConfig(BaseModel):
    """One firm entry from config/firms.yaml."""

    id: str
    name: str
    enabled: bool = False
    status: FirmStatus
    reason: str | None = None
    directory_url: str | None = None
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
