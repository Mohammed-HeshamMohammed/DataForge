"""Policy-enforcing HTTP layer shared by every collection mode.

- TLS uses the operating system trust store (truststore), so machines with a corporate or antivirus
  TLS inspection root behave like the user's browser. Certificate checks are never disabled.
- Optional RFC 9111 cache (hishel) per project: re-runs revalidate with ETag/Last-Modified.
- A per-host scheduler spaces requests by the largest of the preset delay, robots.txt Crawl-delay,
  and the declared requests-per-second.
- Access, challenge, and rate-limit responses stop collection. Retry-After is honoured only for
  presets that declare documented back-off, and only for a bounded wait.
"""

from __future__ import annotations

import re
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .errors import PolicyViolation

try:  # truststore is bundled; fall back to certifi only if it is missing
    import truststore

    _SSL_CONTEXT: ssl.SSLContext | bool = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:  # pragma: no cover
    _SSL_CONTEXT = True

APP_USER_AGENT = "DataForge/0.2 (local desktop; permitted collection)"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
ACCESS_STOP_CODES = {401, 402, 403, 407, 408, 425, 429, 451}
CHALLENGE_MARKERS = ("g-recaptcha", "h-captcha", "cf-challenge", "/cdn-cgi/challenge-platform", "captcha-delivery")
MAX_RETRY_AFTER_SECONDS = 60
MAX_BODY_BYTES = 50 * 1024 * 1024
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def user_agent(preset: dict | None = None, contact: dict | None = None) -> str:
    """The contact identity is added only for presets that require it (for example SEC EDGAR, Wikidata)."""
    if preset and preset.get("requires_contact_user_agent"):
        contact = contact or {}
        organization, email = str(contact.get("organization", "")).strip(), str(contact.get("email", "")).strip()
        if not organization or not _EMAIL.match(email):
            raise PolicyViolation("This source requires a contact identity (organization and email); add one in Settings → Contact identity")
        return f"{organization} {email} {APP_USER_AGENT}"
    return APP_USER_AGENT


def make_client(
    preset: dict | None = None,
    contact: dict | None = None,
    headers: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 30.0,
) -> httpx.Client:
    base = transport or httpx.HTTPTransport(verify=_SSL_CONTEXT, retries=0)
    if cache_dir is not None:
        import hishel
        from hishel.httpx import SyncCacheTransport

        cache_dir.mkdir(parents=True, exist_ok=True)
        base = SyncCacheTransport(
            base,
            storage=hishel.SyncSqliteStorage(database_path=str(cache_dir / "http-cache.sqlite3"), default_ttl=30 * 24 * 3600),
            policy=hishel.SpecificationPolicy(cache_options=hishel.CacheOptions(shared=False)),
        )
    return httpx.Client(transport=base, timeout=timeout, headers={"User-Agent": user_agent(preset, contact), **(headers or {})})


class HostScheduler:
    """One politeness clock per host. Thread-safe so parallel jobs share a host budget."""

    def __init__(self, min_delay_seconds: float = 0.0, requests_per_second: float | None = None, sleep=time.sleep, clock=time.monotonic) -> None:
        self.min_delay = max(0.0, float(min_delay_seconds))
        if requests_per_second:
            self.min_delay = max(self.min_delay, 1.0 / float(requests_per_second))
        self._crawl_delay: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._sleep, self._clock = sleep, clock
        self.waits: list[float] = []

    def set_crawl_delay(self, host: str, seconds: float | None) -> None:
        if seconds:
            self._crawl_delay[host] = float(seconds)

    def delay_for(self, host: str) -> float:
        return max(self.min_delay, self._crawl_delay.get(host, 0.0))

    def wait(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        with self._lock:
            now = self._clock()
            last = self._last.get(host)
            pause = 0.0 if last is None else max(0.0, last + self.delay_for(host) - now)
            self._last[host] = now + pause
        if pause:
            self.waits.append(pause)
            self._sleep(pause)


def scheduler_for(preset: dict) -> HostScheduler:
    limits = preset.get("request_limits") if isinstance(preset.get("request_limits"), dict) else {}
    return HostScheduler(int(limits.get("min_delay_ms", 0)) / 1000, limits.get("max_requests_per_second"))


def fetch(
    client: httpx.Client,
    url: str,
    preset: dict,
    validate_url,
    scheduler: HostScheduler | None = None,
    method: str = "GET",
    content: bytes | str | None = None,
    headers: dict[str, str] | None = None,
    check_challenge: bool = True,
    sleep=time.sleep,
) -> httpx.Response:
    """One in-scope request. Redirects are followed manually so every hop is scope-checked."""
    limits = preset.get("request_limits") if isinstance(preset.get("request_limits"), dict) else {}
    retries_left = 3 if limits.get("respect_retry_after") else 0
    hops = 0
    while True:
        if scheduler is not None:
            scheduler.wait(url)
        response = client.request(method, url, content=content, headers=headers, follow_redirects=False)
        if response.status_code in (429, 503) and retries_left:
            wait = _retry_after_seconds(response.headers.get("retry-after"))
            if wait is not None and wait <= MAX_RETRY_AFTER_SECONDS:
                retries_left -= 1
                sleep(wait)
                continue
        if response.status_code in ACCESS_STOP_CODES:
            raise PolicyViolation(f"Collection stopped on access or rate-limit response: {response.status_code}")
        if response.is_redirect:
            hops += 1
            if hops > 5:
                raise PolicyViolation("Collection stopped: too many redirects")
            url = urljoin(url, response.headers.get("location", ""))
            validate_url(url, preset)  # never follow a redirect out of scope
            method, content = ("GET", None) if response.status_code in (301, 302, 303) else (method, content)
            continue
        if response.status_code == 304:
            return response
        response.raise_for_status()
        if len(response.content) > MAX_BODY_BYTES:
            raise PolicyViolation("Collection stopped: response exceeds the 50 MB size cap")
        content_type = response.headers.get("content-type", "")
        if check_challenge and ("html" in content_type or not content_type):
            lowered = response.text[:200_000].lower()
            if any(marker in lowered for marker in CHALLENGE_MARKERS):
                raise PolicyViolation("Collection stopped: the page presented an access challenge (CAPTCHA/bot check)")
        return response


def from_cache(response: httpx.Response) -> bool:
    return bool(response.extensions.get("hishel_from_cache"))


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone

        return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None
