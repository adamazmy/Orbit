"""Tests for the shared partner filter and name normaliser (no network, fictional names)."""

import pytest

from orbit.models import PartnerRecord
from orbit.normalise import filter_partners, is_partner, normalise_name


@pytest.mark.parametrize(
    "title",
    [
        "Partner",
        "partner",
        "Executive Partner",
        "Managing Partner",
        "Presiding Partner",
        "Senior Partner",
        "Co-Managing Partner",
        "Partner ",
    ],
)
def test_partner_titles_count(title):
    assert is_partner(title)


@pytest.mark.parametrize(
    "title",
    [
        "Counsel",
        "Of Counsel",
        "Senior Counsel",
        "Special Counsel",
        "Partner & Counsel",
        "Associate",
        "Senior Associate",
        "Retired Partner",
        "Former Partner",
        "Partner Emeritus",
        "Senior Advisor",
        "",
    ],
)
def test_non_partner_titles_do_not_count(title):
    assert not is_partner(title)


def make_record(name, title):
    return PartnerRecord(
        name=name,
        firm_id="firm-a",
        title=title,
        profile_url=f"https://example.test/{name}",
        person_key=name,
    )


def test_filter_partners_keeps_only_partners():
    records = [
        make_record("Jane Example", "Partner"),
        make_record("John Sample", "Of Counsel"),
        make_record("Alex Placeholder", "Executive Partner"),
    ]
    assert [r.name for r in filter_partners(records)] == [
        "Jane Example",
        "Alex Placeholder",
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jane Example", "jane example"),
        ("  JANE   EXAMPLE ", "jane example"),
        ("José Núñez", "jose nunez"),
        ("William J. Smith", "william smith"),
        ("C. Daniel Fictional", "daniel fictional"),
        ("John Sample Jr.", "john sample"),
        ("John Sample, III", "john sample"),
        ("Dr. Alex Placeholder", "alex placeholder"),
        ("Ana (née Ruiz) López", "ana lopez"),
        ("Robert (Bob) Example", "robert example"),
        ("Bill Smith", "william smith"),
        ("Bob Example", "robert example"),
        ("Liz O'Fiction", "elizabeth ofiction"),
        ("Mary-Jane Example", "mary jane example"),
    ],
)
def test_normalise_name(raw, expected):
    assert normalise_name(raw) == expected


def test_name_variants_normalise_the_same():
    assert normalise_name("William J. Smith") == normalise_name("Bill Smith")


def test_nickname_only_applies_to_first_name():
    # A surname that happens to be a nickname must not be rewritten.
    assert normalise_name("Jane Bill") == "jane bill"
