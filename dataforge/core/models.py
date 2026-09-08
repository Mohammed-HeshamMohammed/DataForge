"""Shared value types passed between the core, ML and pipeline layers."""

from __future__ import annotations

from dataclasses import dataclass, field

#: The roles a column can be classified into, in the order they are reported.
ROLE_ORDER = ("identifier", "address", "region", "name", "phone", "email", "other")


@dataclass(frozen=True)
class MatchCandidate:
    """A single pair of row indices proposed as a possible duplicate."""

    left: int
    right: int
    score: float = 0.0

    def as_tuple(self) -> tuple[int, int]:
        return (self.left, self.right)


@dataclass
class ColumnRoles:
    """Semantic roles detected in a table's columns.

    The deduplication engine works against roles rather than literal column
    names so that files exported by different vendors compare cleanly.

    ``address`` is deliberately street-level only; coarse geography lives in
    ``region``. The split matters because a city or state makes a useless
    blocking key — every row in a county-wide export shares it — while a street
    address is selective enough to group candidates by.
    """

    identifier: list[str] = field(default_factory=list)
    address: list[str] = field(default_factory=list)
    region: list[str] = field(default_factory=list)
    name: list[str] = field(default_factory=list)
    phone: list[str] = field(default_factory=list)
    email: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    def all_columns(self) -> list[str]:
        """Every classified column, in a stable role-grouped order."""
        return [column for role in ROLE_ORDER for column in getattr(self, role)]

    def comparison_columns(self) -> list[str]:
        """Columns that carry identity signal and are worth comparing."""
        return [*self.address, *self.region, *self.name, *self.phone, *self.email]
