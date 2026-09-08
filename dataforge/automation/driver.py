"""WebDriver construction, kept in one place so every runner shares defaults.

Selenium is imported lazily: the package installs fine, and the rest of the
platform runs, on a machine with no browser.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from dataforge.config import get_settings
from dataforge.logging import get_logger

logger = get_logger(__name__)


class DriverUnavailableError(RuntimeError):
    """Raised when Selenium or a browser binary is not installed."""


def build_driver(browser: str | None = None, headless: bool | None = None) -> Any:
    """Create a configured WebDriver for the requested browser.

    When ``selenium_remote_url`` is set the driver connects to a remote Grid or
    standalone container instead of launching a local browser.
    """
    settings = get_settings()
    browser = (browser or settings.selenium_browser).lower()
    headless = settings.selenium_headless if headless is None else headless

    try:
        from selenium import webdriver
    except ImportError as exc:
        raise DriverUnavailableError(
            "Browser automation requires the 'automation' extra: "
            "pip install 'dataforge[automation]'"
        ) from exc

    if browser == "chrome":
        options = webdriver.ChromeOptions()
    elif browser == "firefox":
        options = webdriver.FirefoxOptions()
    else:
        raise ValueError(f"Unsupported browser '{browser}'; expected chrome or firefox")

    if headless:
        options.add_argument("--headless=new" if browser == "chrome" else "-headless")
    if browser == "chrome":
        # Required when running as root inside a container.
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

    if settings.selenium_remote_url:
        logger.info("Connecting to remote WebDriver at %s", settings.selenium_remote_url)
        driver = webdriver.Remote(command_executor=settings.selenium_remote_url, options=options)
    else:
        driver = getattr(webdriver, browser.capitalize())(options=options)

    driver.set_page_load_timeout(settings.selenium_timeout_seconds)
    return driver


@contextmanager
def managed_driver(browser: str | None = None, headless: bool | None = None) -> Iterator[Any]:
    """Yield a driver and guarantee it is quit, even if the caller raises."""
    driver = build_driver(browser=browser, headless=headless)
    try:
        yield driver
    finally:
        driver.quit()
