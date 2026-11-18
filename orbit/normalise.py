"""Shared clean-up applied to every firm's records, whatever site they came from.

- is_partner / filter_partners: the scope rule "partners only". Firms word titles
  differently (Partner, Executive Partner, Presiding Partner...), so this lives
  in one place instead of in each adapter.
- normalise_name: a comparable form of a person's name, used to recognise the
  same person at two firms (MOVED events). "William J. Smith" and "Bill Smith"
  both become "william smith".
"""

import logging
import re
import unicodedata

from orbit.models import PartnerRecord

logger = logging.getLogger(__name__)

# Words that disqualify a title even if it contains "partner" (e.g. "Retired Partner").
NOT_PARTNER_WORDS = {"counsel", "associate", "retired", "former", "emeritus"}

HONORIFICS = {"dr", "mr", "mrs", "ms", "prof", "sir", "dame"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "esq"}

# Common English nicknames -> formal first name. Kept small and unambiguous on
# purpose: names like "Sam" or "Alex" could be several formal names, so they're left out.
NICKNAMES = {
    "bill": "william",
    "billy": "william",
    "will": "william",
    "bob": "robert",
    "bobby": "robert",
    "rob": "robert",
    "jim": "james",
    "jimmy": "james",
    "mike": "michael",
    "dick": "richard",
    "rick": "richard",
    "rich": "richard",
    "tom": "thomas",
    "tony": "anthony",
    "dave": "david",
    "dan": "daniel",
    "danny": "daniel",
    "chris": "christopher",
    "matt": "matthew",
    "nick": "nicholas",
    "steve": "stephen",
    "joe": "joseph",
    "jack": "john",
    "jon": "jonathan",
    "ben": "benjamin",
    "andy": "andrew",
    "greg": "gregory",
    "jeff": "jeffrey",
    "ken": "kenneth",
    "larry": "lawrence",
    "tim": "timothy",
    "ed": "edward",
    "kate": "katherine",
    "katie": "katherine",
    "liz": "elizabeth",
    "beth": "elizabeth",
    "jen": "jennifer",
    "jenny": "jennifer",
    "meg": "margaret",
    "peggy": "margaret",
    "sue": "susan",
}


def is_partner(title: str) -> bool:
    """True for partner titles (Partner, Executive/Managing/Presiding Partner...).

    False for counsel, associates and retired or former partners.
    """
    words = re.findall(r"[a-z]+", title.casefold())
    return "partner" in words and not NOT_PARTNER_WORDS.intersection(words)


def filter_partners(records: list[PartnerRecord]) -> list[PartnerRecord]:
    """Keep only partners, logging how many people were dropped."""
    partners = [record for record in records if is_partner(record.title)]
    dropped = len(records) - len(partners)
    if dropped:
        titles = sorted({r.title for r in records if not is_partner(r.title)})
        logger.info("Dropped %d non-partners (titles: %s)", dropped, ", ".join(titles))
    return partners


def normalise_name(name: str) -> str:
    """Comparable form of a name: lowercase, no accents, initials, suffixes or nicknames.

    "Dr. William J. Smith Jr." -> "william smith"; "Bill Smith" -> "william smith";
    "Ana (née Ruiz) López" -> "ana lopez".
    """
    # Bracketed parts are maiden names or nicknames ("(née Ruiz)", "(Bob)"), not the name used.
    text = re.sub(r"\([^)]*\)", " ", name)
    text = _strip_accents(text).casefold()
    text = text.replace("'", "").replace("’", "")  # O'Brien -> obrien
    tokens = re.findall(r"[a-z]+", text)
    tokens = [t for t in tokens if t not in HONORIFICS and t not in SUFFIXES]
    # Single letters are initials ("J." in "William J. Smith").
    tokens = [t for t in tokens if len(t) > 1]
    if tokens:
        tokens[0] = NICKNAMES.get(tokens[0], tokens[0])
    return " ".join(tokens)


def _strip_accents(text: str) -> str:
    """Remove accents: "José Núñez" -> "Jose Nunez"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
