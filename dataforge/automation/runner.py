"""Execute a :class:`~dataforge.automation.recipe.Recipe` against records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dataforge.automation.driver import managed_driver
from dataforge.automation.recipe import Recipe, Step
from dataforge.config import get_settings
from dataforge.logging import get_logger

logger = get_logger(__name__)


@dataclass
class EntryResult:
    """Per-record outcome of a data-entry run."""

    submitted: int = 0
    skipped: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "submitted": self.submitted,
            "skipped": self.skipped,
            "failed": len(self.failures),
        }


class RecipeRunner:
    """Drive a browser through a recipe once per record.

    Set ``dry_run`` to walk the recipe and validate every record without
    launching a browser — the default for the CLI, so that a mistyped selector
    or a missing column is caught before anything is submitted to a live system.
    """

    def __init__(self, recipe: Recipe, dry_run: bool = True) -> None:
        self.recipe = recipe
        self.dry_run = dry_run

    def run(self, records: Iterable[dict[str, Any]]) -> EntryResult:
        records = list(records)
        result = EntryResult()

        if self.dry_run:
            for record in records:
                missing = self.recipe.validate_record(record)
                if missing:
                    result.skipped += 1
                    logger.info("Would skip record: missing %s", ", ".join(missing))
                else:
                    result.submitted += 1
            logger.info("Dry run complete: %s", result.summary())
            return result

        with managed_driver() as driver:
            for index, record in enumerate(records):
                missing = self.recipe.validate_record(record)
                if missing:
                    result.skipped += 1
                    logger.warning("Skipping record %d: missing %s", index, ", ".join(missing))
                    continue
                try:
                    self._enter_record(driver, record)
                    result.submitted += 1
                except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
                    logger.exception("Record %d failed", index)
                    result.failures.append(
                        {"index": index, "error": f"{type(exc).__name__}: {exc}"}
                    )
        return result

    def _enter_record(self, driver: Any, record: dict[str, Any]) -> None:
        """Run every step of the recipe for one record."""
        if self.recipe.url:
            driver.get(self.recipe.url)
        for step in self.recipe.steps:
            self._execute_step(driver, step, record)

    def _execute_step(self, driver: Any, step: Step, record: dict[str, Any]) -> None:
        """Perform a single recipe step, honouring ``optional``."""
        from selenium.common.exceptions import NoSuchElementException, TimeoutException

        value = step.render_value(record)
        try:
            if step.action == "navigate":
                driver.get(value)
                return
            if step.action == "screenshot":
                settings = get_settings()
                settings.ensure_directories()
                target = Path(value) if value else settings.artifact_dir / f"{self.recipe.name}.png"
                driver.save_screenshot(str(target))
                return

            element = self._wait_for_element(driver, step)
            if step.action == "type":
                element.clear()
                element.send_keys(value)
            elif step.action == "click":
                element.click()
            elif step.action == "select":
                from selenium.webdriver.support.ui import Select

                Select(element).select_by_visible_text(value)
            # 'wait_for' is satisfied by _wait_for_element returning.
        except (NoSuchElementException, TimeoutException):
            if step.optional:
                logger.debug("Optional step skipped: %s %s", step.action, step.selector)
                return
            raise

    @staticmethod
    def _wait_for_element(driver: Any, step: Step) -> Any:
        """Wait for the step's element to be present and return it."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions
        from selenium.webdriver.support.ui import WebDriverWait

        locators = {"css": By.CSS_SELECTOR, "xpath": By.XPATH, "id": By.ID, "name": By.NAME}
        if step.by not in locators:
            raise ValueError(f"Unsupported locator '{step.by}'; expected one of {sorted(locators)}")

        timeout = get_settings().selenium_timeout_seconds
        return WebDriverWait(driver, timeout).until(
            expected_conditions.presence_of_element_located((locators[step.by], step.selector))
        )
