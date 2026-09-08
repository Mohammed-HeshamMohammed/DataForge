"""Browser automation layer: declarative data-entry recipes driven by Selenium."""

from dataforge.automation.driver import build_driver
from dataforge.automation.recipe import Recipe, Step, load_recipe
from dataforge.automation.runner import EntryResult, RecipeRunner

__all__ = [
    "EntryResult",
    "Recipe",
    "RecipeRunner",
    "Step",
    "build_driver",
    "load_recipe",
]
