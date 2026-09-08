"""Normalisation of values and detection of the semantic role of columns.

The original processor lower-cased and stripped punctuation from *every*
column, which destroyed phone and email formatting before comparison. Here each
role gets a normaliser that preserves the part of the value that carries
identity: digits for phones, the local/domain split for emails, and
whitespace-collapsed alphanumerics for names and addresses.
"""

from __future__ import annotations

import re

import pandas as pd
from rapidfuzz import fuzz

from dataforge.core.models import ROLE_ORDER, ColumnRoles

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D")

#: Keywords that identify each role. A column matches a keyword when the
#: keyword's words appear as a contiguous run of the column's own words, so
#: "Subdivision" is not read as an "id" column and "Units Count" is not read as
#: a "County" one. Longer keywords win ties, which is what sends
#: "Owner Mailing Address" to ``address`` rather than ``name``.
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "identifier": ("id", "apn", "record id", "parcel number"),
    "address": ("address", "street"),
    "region": ("city", "state", "zip", "zipcode", "postal code", "county"),
    "name": (
        "first name",
        "last name",
        "full name",
        "contact name",
        "owner name",
        "middle name",
    ),
    "phone": ("phone", "mobile", "cell", "telephone", "landline"),
    "email": ("email", "e mail"),
}


def normalize_text(value: object) -> str:
    """Lower-case, strip punctuation and collapse whitespace."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = _NON_ALNUM.sub(" ", str(value).strip().lower())
    return _WHITESPACE.sub(" ", text).strip()


def normalize_phone(value: object) -> str:
    """Reduce a phone number to its digits, dropping a US country prefix."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    digits = _NON_DIGIT.sub("", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_email(value: object) -> str:
    """Lower-case and trim an email address, discarding anything malformed."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip().lower()
    return text if "@" in text else ""


#: Canonical forms for the street suffixes and directionals that vendors spell
#: inconsistently. Without this, "123 Main St" and "123 Main Street" score 0.78
#: against each other and the same property survives as two records.
ADDRESS_ABBREVIATIONS: dict[str, str] = {
    "street": "st",
    "avenue": "ave",
    "av": "ave",
    "road": "rd",
    "boulevard": "blvd",
    "blv": "blvd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "circle": "cir",
    "place": "pl",
    "terrace": "ter",
    "parkway": "pkwy",
    "highway": "hwy",
    "trail": "trl",
    "square": "sq",
    "suite": "ste",
    "apartment": "apt",
    "unit": "apt",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}


def normalize_address(value: object) -> str:
    """Normalise a street address and canonicalise its suffixes and directionals."""
    text = normalize_text(value)
    if not text:
        return ""
    return " ".join(ADDRESS_ABBREVIATIONS.get(word, word) for word in text.split())


#: Normaliser to apply to each detected role.
ROLE_NORMALIZERS = {
    "phone": normalize_phone,
    "email": normalize_email,
    "address": normalize_address,
}


def _words(text: str) -> list[str]:
    """Split a column name into lower-case alphanumeric words."""
    return _WHITESPACE.sub(" ", _NON_ALNUM.sub(" ", text.lower())).split()


def _keyword_matches(keyword: str, words: list[str]) -> bool:
    """True when the keyword's words appear contiguously inside ``words``."""
    target = keyword.split()
    span = len(target)
    return any(words[i : i + span] == target for i in range(len(words) - span + 1))


def detect_roles(columns: list[str], fuzzy_threshold: int = 92) -> ColumnRoles:
    """Classify column names into semantic roles.

    A column is first matched on whole-word keyword containment, which is exact
    and cheap. Only columns that match nothing that way fall back to a fuzzy
    comparison against the keywords, so a vendor's misspelling is still caught
    without the false positives that pure fuzzy matching produces.
    """
    roles = ColumnRoles()
    for column in columns:
        words = _words(column)
        best_role, best_length = None, 0
        for role, keywords in ROLE_KEYWORDS.items():
            for keyword in keywords:
                if _keyword_matches(keyword, words) and len(keyword) > best_length:
                    best_role, best_length = role, len(keyword)

        if best_role is None:
            best_role = _fuzzy_role(column, fuzzy_threshold)

        getattr(roles, best_role or "other").append(column)
    return roles


def _fuzzy_role(column: str, threshold: int) -> str | None:
    """Fall back to a fuzzy keyword match for a column no keyword contained."""
    best_role, best_score = None, 0.0
    for role, keywords in ROLE_KEYWORDS.items():
        score = max(fuzz.ratio(kw, column.lower()) for kw in keywords)
        if score > best_score:
            best_role, best_score = role, score
    return best_role if best_score >= threshold else None


def normalize_frame(frame: pd.DataFrame, roles: ColumnRoles) -> pd.DataFrame:
    """Return a copy of ``frame`` with each column normalised for its role.

    Columns are normalised vectorised via :meth:`pandas.Series.map`, which keeps
    the cost linear in the number of cells.
    """
    normalized = frame.copy()
    for role in ROLE_ORDER:
        normalizer = ROLE_NORMALIZERS.get(role, normalize_text)
        for column in getattr(roles, role):
            normalized[column] = frame[column].map(normalizer)
    return normalized
