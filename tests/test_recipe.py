"""Data-entry recipe parsing and dry-run execution."""

from __future__ import annotations

import pytest

from dataforge.automation.recipe import Recipe, RecipeError, Step, load_recipe
from dataforge.automation.runner import RecipeRunner


def test_step_rejects_an_unknown_action():
    with pytest.raises(RecipeError):
        Step(action="teleport", selector="#x")


def test_step_requires_a_selector_where_one_is_needed():
    with pytest.raises(RecipeError):
        Step(action="click")


def test_value_templates_fill_from_the_record():
    step = Step(action="type", selector="#name", value="{First} {Last}")
    assert step.render_value({"First": "Jane", "Last": "Doe"}) == "Jane Doe"


def test_missing_template_keys_render_empty_rather_than_raising():
    step = Step(action="type", selector="#phone", value="{Phone 1}")
    assert step.render_value({"First": "Jane"}) == ""


def test_recipe_without_steps_is_rejected():
    with pytest.raises(RecipeError):
        Recipe.from_dict({"name": "empty"})


def test_load_recipe_reads_the_bundled_example():
    recipe = load_recipe("recipes/example-crm.yml")
    assert recipe.name == "example-crm-lead"
    assert recipe.steps


def test_dry_run_skips_records_missing_required_fields():
    recipe = Recipe(
        name="t",
        steps=[Step(action="type", selector="#a", value="{A}")],
        required_fields=["A"],
    )
    result = RecipeRunner(recipe, dry_run=True).run([{"A": "x"}, {"A": ""}, {}])
    assert result.summary() == {"submitted": 1, "skipped": 2, "failed": 0}
