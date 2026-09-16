"""Site signals checked before collection: robots.txt (RFC 9309, via Protego), TDMRep, IETF AIPREF
Content-Usage, and ai.txt (advisory).

Signals are evaluated against the job's declared purpose. A signal that disallows the purpose stops the
job with an explanation; an unparseable signal becomes a warning, never a silent allow.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from urllib.parse import urlparse

import httpx
from protego import Protego

from .errors import PolicyViolation
from .fetch import APP_USER_AGENT, LOCAL_HOSTS

PURPOSES = ("internal_analysis", "lead_research", "dataset_building", "price_monitoring", "research", "archival", "search_indexing", "ai_training")
# AIPREF vocabulary categories that govern each purpose. "bots" (automated processing) covers everything.
_AIPREF_CATEGORIES = {
    "ai_training": ("bots", "train-ai", "train-genai"),
    "search_indexing": ("bots", "search"),
}
_DEFAULT_CATEGORIES = ("bots",)
_CONTENT_USAGE_LINE = re.compile(r"^\s*content-usage\s*:\s*(.+)$", re.I)


@dataclass
class HostSignals:
    host: str
    checked_at: str
    robots_status: str = "not_checked"  # ok | missing | unreachable | forbidden | local
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)
    tdm_reservation: int | None = None
    tdm_policy: str | None = None
    tdmrep_source: str | None = None
    content_usage: dict[str, list[dict]] = field(default_factory=dict)  # source -> [{path, prefs}]
    ai_txt: bool = False
    warnings: list[str] = field(default_factory=list)
    _robots_text: str | None = field(default=None, repr=False)
    _tdmrep_rules: list[dict] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}


def parse_content_usage(value: str) -> dict[str, str]:
    """`train-ai=n, search=y` (structured-field dictionary) -> {"train-ai": "n", "search": "y"}."""
    prefs: dict[str, str] = {}
    for part in value.split(","):
        key, _, pref = part.strip().partition("=")
        key, pref = key.strip().lower(), pref.strip().strip('"').lower()[:1]
        if key and pref in ("y", "n"):
            prefs[key] = pref
    return prefs


def parse_robots_content_usage(text: str) -> tuple[list[dict], list[str]]:
    rules, warnings = [], []
    for line in text.splitlines():
        match = _CONTENT_USAGE_LINE.match(line.split("#", 1)[0])
        if not match:
            continue
        body = match.group(1).strip()
        path = "/"
        if body.startswith("/"):
            path, _, body = body.partition(" ")
        prefs = parse_content_usage(body)
        if prefs:
            rules.append({"path": path, "prefs": prefs})
        else:
            warnings.append(f"unparseable Content-Usage rule in robots.txt: {body[:80]}")
    return rules, warnings


def parse_tdmrep(document: object) -> tuple[list[dict], list[str]]:
    if not isinstance(document, list):
        return [], ["tdmrep.json is not a JSON array"]
    rules, warnings = [], []
    for entry in document:
        if not isinstance(entry, dict) or "location" not in entry:
            warnings.append("tdmrep.json entry without a location")
            continue
        try:
            reservation = int(entry.get("tdm-reservation", 0))
        except (TypeError, ValueError):
            warnings.append("tdmrep.json entry with an invalid tdm-reservation")
            continue
        rules.append({"location": str(entry["location"]), "tdm-reservation": reservation, "tdm-policy": entry.get("tdm-policy")})
    return rules, warnings


def _tdmrep_matches(location: str, path: str) -> bool:
    pattern = location if location.startswith("/") or location.startswith("*") else "/" + location
    if "*" in pattern:
        return fnmatch(path, pattern)
    return pattern == "/" or path == pattern or path.startswith(pattern.rstrip("/") + "/")


class SignalChecker:
    """Caches signals per origin for one job. Local fixture hosts are exempt unless `include_local`."""

    def __init__(self, client: httpx.Client, purpose: str = "internal_analysis", robots_policy: str = "respect", include_local: bool = False, scheduler=None) -> None:
        if purpose not in PURPOSES:
            raise PolicyViolation(f"Unknown collection purpose {purpose!r}; choose one of {', '.join(PURPOSES)}")
        self.client, self.purpose, self.robots_policy = client, purpose, robots_policy
        self.include_local, self.scheduler = include_local, scheduler
        self.hosts: dict[str, HostSignals] = {}
        self._robots: dict[str, Protego | None] = {}
        self.checks: list[str] = []  # ordered log of URLs checked (politeness assertions in tests)

    def _get(self, url: str) -> httpx.Response | None:
        try:
            if self.scheduler is not None:
                self.scheduler.wait(url)
            return self.client.get(url, follow_redirects=True, timeout=15.0)
        except httpx.HTTPError:
            return None

    def for_url(self, url: str) -> HostSignals:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self.hosts:
            return self.hosts[origin]
        if self.client is None:
            raise PolicyViolation(f"Collection stopped: signals for {parsed.netloc} were not checked before the run")
        signals = HostSignals(host=parsed.netloc, checked_at=datetime.now(timezone.utc).isoformat())
        self.hosts[origin] = signals
        if parsed.hostname in LOCAL_HOSTS and not self.include_local:
            signals.robots_status = "local"
            self._robots[origin] = None
            return signals
        # robots.txt (RFC 9309 section 2.3.1: 4xx = no restrictions, 5xx/unreachable = complete disallow)
        response = self._get(origin + "/robots.txt")
        if response is None or response.status_code >= 500:
            signals.robots_status = "unreachable"
            signals._robots_text = "User-agent: *\nDisallow: /"
            self._robots[origin] = Protego.parse(signals._robots_text)
        elif response.status_code in (401, 403):
            signals.robots_status = "forbidden"
            signals._robots_text = "User-agent: *\nDisallow: /"
            self._robots[origin] = Protego.parse(signals._robots_text)
        elif response.status_code >= 400:
            signals.robots_status = "missing"
            self._robots[origin] = None
        else:
            text = response.text[:500_000]
            signals.robots_status = "ok"
            signals._robots_text = text
            robots = Protego.parse(text)
            self._robots[origin] = robots
            signals.crawl_delay = robots.crawl_delay(APP_USER_AGENT)
            signals.sitemaps = list(robots.sitemaps)
            rules, warnings = parse_robots_content_usage(text)
            if rules:
                signals.content_usage["robots.txt"] = rules
            signals.warnings.extend(warnings)
        # TDMRep well-known file
        response = self._get(origin + "/.well-known/tdmrep.json")
        if response is not None and response.status_code == 200:
            try:
                rules, warnings = parse_tdmrep(response.json())
                signals._tdmrep_rules = rules
                signals.warnings.extend(warnings)
                signals.tdmrep_source = "/.well-known/tdmrep.json"
            except (json.JSONDecodeError, ValueError):
                signals.warnings.append("tdmrep.json could not be parsed")
        # ai.txt is an individual Internet-Draft: recorded as advisory provenance only
        response = self._get(origin + "/ai.txt")
        signals.ai_txt = bool(response is not None and response.status_code == 200 and "text/plain" in response.headers.get("content-type", ""))
        if self.scheduler is not None:
            self.scheduler.set_crawl_delay(parsed.hostname or "", signals.crawl_delay)
        self.checks.append(origin)
        return signals

    def check_url(self, url: str) -> HostSignals:
        """Raise PolicyViolation if robots.txt, TDMRep, or robots Content-Usage disallows this URL for the purpose."""
        signals = self.for_url(url)
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        robots = self._robots.get(origin)
        if self.robots_policy == "respect" and robots is not None and not robots.can_fetch(url, APP_USER_AGENT):
            reason = {"unreachable": "robots.txt could not be retrieved (server error), which RFC 9309 treats as disallow-all",
                      "forbidden": "robots.txt access is forbidden, treated as disallow-all"}.get(signals.robots_status, "robots.txt disallows this URL")
            raise PolicyViolation(f"Collection stopped: {reason}")
        path = parsed.path or "/"
        for rule in signals._tdmrep_rules:
            if _tdmrep_matches(rule["location"], path):
                signals.tdm_reservation, signals.tdm_policy = rule["tdm-reservation"], rule["tdm-policy"]
                if rule["tdm-reservation"] == 1:
                    raise PolicyViolation(_tdm_message(rule.get("tdm-policy")))
        for rule in signals.content_usage.get("robots.txt", []):
            if path.startswith(rule["path"]):
                self._check_prefs(rule["prefs"], "robots.txt Content-Usage")
        return signals

    def check_response(self, url: str, response: httpx.Response, html: str | None = None) -> None:
        """Per-response signals: `tdm-reservation` header or meta tag, `Content-Usage` header."""
        signals = self.for_url(url)
        reservation = response.headers.get("tdm-reservation")
        policy = response.headers.get("tdm-policy")
        if reservation is None and html:
            match = re.search(r"<meta[^>]+name=[\"']tdm-reservation[\"'][^>]*content=[\"'](\d)[\"']", html[:100_000], re.I)
            reservation = match.group(1) if match else None
            policy_match = re.search(r"<meta[^>]+name=[\"']tdm-policy[\"'][^>]*content=[\"']([^\"']+)[\"']", html[:100_000], re.I)
            policy = policy or (policy_match.group(1) if policy_match else None)
        if reservation is not None:
            signals.tdm_reservation = int(reservation) if str(reservation).strip() in ("0", "1") else None
            signals.tdm_policy = policy or signals.tdm_policy
            if signals.tdm_reservation == 1:
                raise PolicyViolation(_tdm_message(policy))
        header = response.headers.get("content-usage")
        if header:
            prefs = parse_content_usage(header)
            signals.content_usage.setdefault("header", []).append({"path": urlparse(url).path or "/", "prefs": prefs})
            if not prefs:
                signals.warnings.append("unparseable Content-Usage header")
            self._check_prefs(prefs, "Content-Usage header")

    def _check_prefs(self, prefs: dict[str, str], source: str) -> None:
        for category in _AIPREF_CATEGORIES.get(self.purpose, _DEFAULT_CATEGORIES):
            if prefs.get(category) == "n":
                raise PolicyViolation(f"Collection stopped: the site's {source} disallows '{category}', which covers the declared purpose '{self.purpose}'")

    def export(self) -> dict:
        """Serializable per-origin state, so a child engine process applies identical decisions."""
        return {origin: {"signals": signals.to_dict(), "robots": signals._robots_text if self._robots.get(origin) is not None else None, "tdmrep": signals._tdmrep_rules}
                for origin, signals in self.hosts.items()}

    @classmethod
    def from_export(cls, data: dict, purpose: str, robots_policy: str = "respect") -> "SignalChecker":
        checker = cls(None, purpose, robots_policy)  # type: ignore[arg-type]
        for origin, item in data.items():
            info = item["signals"]
            signals = HostSignals(**{k: v for k, v in info.items() if k in HostSignals.__dataclass_fields__})
            signals._robots_text, signals._tdmrep_rules = item.get("robots"), item.get("tdmrep") or []
            checker.hosts[origin] = signals
            checker._robots[origin] = Protego.parse(item["robots"]) if item.get("robots") is not None else None
        return checker

    def summary(self) -> list[dict]:
        return [signals.to_dict() for signals in self.hosts.values()]


def _tdm_message(policy: str | None) -> str:
    suffix = f" Licensing terms: {policy}" if policy else ""
    return "Collection stopped: the site reserves text-and-data-mining rights (TDMRep tdm-reservation=1)." + suffix
