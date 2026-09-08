"""How two records are compared, shared by the rule scorer and the ML features.

Roles fall into two kinds, and conflating them is what makes naive matchers
produce false positives:

*Interchangeable* roles (phone, email) hold a set of equivalent values. Which
column a number sits in carries no meaning, so a record with its primary and
alternate phone swapped must still match. These compare as sets.

*Positional* roles (address, region, name) hold columns that mean different
things. A property address and an owner's mailing address are both "address"
columns, but comparing them as one bag makes two unrelated properties look
similar whenever they share a managing agent. These compare column to column.
"""

from __future__ import annotations

import re

import pandas as pd
from rapidfuzz import fuzz

from dataforge.core.models import ColumnRoles

#: Roles whose columns hold interchangeable values and compare as sets.
INTERCHANGEABLE_ROLES = frozenset({"phone", "email"})

#: Roles that, on an exact match, are strong enough to identify a record alone.
STRONG_ROLES = ("phone", "email")

#: Address similarity at or above this is treated as strong evidence.
STRONG_ADDRESS_SIMILARITY = 0.9

#: A street address that opens with a house number, e.g. "144 e 68th st".
_HOUSE_NUMBER = re.compile(r"^(\d+)\s+(.+)$")


def address_similarity(left: str, right: str) -> float:
    """Compare two street addresses, treating the house number as exact.

    "144 E 68th St" and "140 E 68th St" differ in one character and score ~0.97
    under a plain fuzzy ratio, but they are two different houses. Where both
    values open with a house number, the numbers must agree before the street
    name is compared at all.
    """
    left_match, right_match = _HOUSE_NUMBER.match(left), _HOUSE_NUMBER.match(right)
    if left_match and right_match:
        if left_match.group(1) != right_match.group(1):
            return 0.0
        return fuzz.token_sort_ratio(left_match.group(2), right_match.group(2)) / 100.0
    return fuzz.token_sort_ratio(left, right) / 100.0


#: Comparators for roles that need more than a plain fuzzy ratio.
ROLE_COMPARATORS = {"address": address_similarity}


def _fuzzy_ratio(left: str, right: str) -> float:
    """Order-insensitive fuzzy similarity, scaled to ``[0, 1]``."""
    return fuzz.token_sort_ratio(left, right) / 100.0


def role_similarity(
    frame: pd.DataFrame, roles: ColumnRoles, left: int, right: int, role: str
) -> float | None:
    """Similarity of two rows for one role, or ``None`` when there is no evidence.

    Returning ``None`` rather than 0.0 matters: a role both records leave blank
    is an absence of information, not a disagreement, and must be dropped from
    the average instead of dragging it down.
    """
    columns = getattr(roles, role, [])
    if not columns:
        return None

    if role in INTERCHANGEABLE_ROLES:
        left_values = {v for v in (frame.at[left, c] for c in columns) if v}
        right_values = {v for v in (frame.at[right, c] for c in columns) if v}
        if not left_values or not right_values:
            return None
        return 1.0 if left_values & right_values else 0.0

    comparator = ROLE_COMPARATORS.get(role, _fuzzy_ratio)
    similarities = []
    for column in columns:
        left_value, right_value = frame.at[left, column], frame.at[right, column]
        if not left_value or not right_value:
            continue
        similarities.append(comparator(str(left_value), str(right_value)))
    if not similarities:
        return None
    return sum(similarities) / len(similarities)


def has_strong_evidence(frame: pd.DataFrame, roles: ColumnRoles, left: int, right: int) -> bool:
    """Whether the pair shares at least one strongly identifying signal.

    Without this gate a pair can clear the threshold on agreement between weak
    fields alone — a shared city and a shared owner name, say — which matches
    every property in a county held by the same municipal owner.
    """
    for role in STRONG_ROLES:
        if role_similarity(frame, roles, left, right, role) == 1.0:
            return True
    address = role_similarity(frame, roles, left, right, "address")
    return address is not None and address >= STRONG_ADDRESS_SIMILARITY
