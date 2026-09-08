"""Declarative data-entry recipes.

A recipe describes a form-filling flow in YAML so that adding a new target
system means writing data, not code:

.. code-block:: yaml

    name: crm-lead-entry
    url: https://crm.example.com/leads/new
    steps:
      - action: type
        selector: "#first_name"
        value: "{Owner 1 First Name}"
      - action: type
        selector: "#phone"
        value: "{Phone 1}"
      - action: click
        selector: "button[type=submit]"
      - action: wait_for
        selector: ".flash-success"

``value`` fields are ``str.format``-style templates filled from the record
being entered, so one recipe serves every row of a table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Actions a runner knows how to perform.
SUPPORTED_ACTIONS = {"navigate", "type", "click", "select", "wait_for", "screenshot"}


class RecipeError(ValueError):
    """Raised when a recipe is malformed."""


@dataclass
class Step:
    """One action in a recipe."""

    action: str
    selector: str = ""
    value: str = ""
    by: str = "css"
    optional: bool = False

    def __post_init__(self) -> None:
        if self.action not in SUPPORTED_ACTIONS:
            raise RecipeError(
                f"Unknown action '{self.action}'; expected one of {sorted(SUPPORTED_ACTIONS)}"
            )
        if self.action in {"type", "click", "select", "wait_for"} and not self.selector:
            raise RecipeError(f"Action '{self.action}' requires a selector")

    def render_value(self, record: dict[str, Any]) -> str:
        """Fill this step's value template from ``record``.

        Missing keys render as an empty string rather than raising, so a recipe
        keeps working against tables with optional columns.
        """
        if not self.value:
            return ""
        return self.value.format_map(_Blank(record))


class _Blank(dict):
    """Mapping that yields '' for absent keys, for use with ``format_map``."""

    def __missing__(self, key: str) -> str:
        return ""


@dataclass
class Recipe:
    """A named sequence of steps run once per record."""

    name: str
    url: str = ""
    steps: list[Step] = field(default_factory=list)
    #: Columns that must be non-empty for a record to be entered.
    required_fields: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Recipe:
        if "name" not in payload:
            raise RecipeError("Recipe is missing 'name'")
        raw_steps = payload.get("steps") or []
        if not raw_steps:
            raise RecipeError(f"Recipe '{payload['name']}' has no steps")
        return cls(
            name=payload["name"],
            url=payload.get("url", ""),
            steps=[Step(**step) for step in raw_steps],
            required_fields=payload.get("required_fields", []),
        )

    def validate_record(self, record: dict[str, Any]) -> list[str]:
        """Return the names of required fields this record is missing."""
        return [f for f in self.required_fields if not str(record.get(f, "")).strip()]


def load_recipe(path: str | Path) -> Recipe:
    """Load and validate a recipe from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No recipe at {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RecipeError(f"{path} does not contain a recipe mapping")
    return Recipe.from_dict(payload)
