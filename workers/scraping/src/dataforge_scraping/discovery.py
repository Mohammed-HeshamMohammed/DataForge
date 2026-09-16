"""Source discovery: XML sitemaps (sitemaps.org protocol, in-house), RSS/Atom/JSON feeds (feedparser),
scoped crawls with a persistable frontier, and llms.txt hints.

Discovery never fetches anything itself: callers pass a `get(url) -> httpx.Response` that already enforces
scope, robots, signals, politeness, and challenge stops. Every discovered URL is scope-checked again.
"""

from __future__ import annotations

import gzip
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable, Iterator
from urllib.parse import urldefrag, urljoin, urlparse

from lxml import etree

MAX_SITEMAP_BYTES = 50 * 1024 * 1024  # protocol limit (uncompressed)
MAX_SITEMAP_URLS = 50_000
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True, remove_comments=True)


@dataclass
class DiscoveredUrl:
    url: str
    lastmod: str | None = None
    depth: int = 0
    source: str = ""
    entry: dict | None = None  # feed entry data when a feed is used as records


def _decompress(content: bytes) -> bytes:
    if content[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=__import__("io").BytesIO(content)) as handle:
            data = handle.read(MAX_SITEMAP_BYTES + 1)
        if len(data) > MAX_SITEMAP_BYTES:
            raise ValueError("Sitemap exceeds the 50 MB uncompressed limit")
        return data
    return content


def parse_sitemap(content: bytes) -> tuple[str, list[tuple[str, str | None]]]:
    """Returns ("urlset" | "sitemapindex" | "text" | "unknown", [(loc, lastmod)])."""
    data = _decompress(content)
    stripped = data.lstrip()
    if not stripped.startswith(b"<"):
        urls = [(line.strip(), None) for line in data.decode("utf-8", "replace").splitlines() if line.strip().startswith(("http://", "https://"))]
        return "text", urls[:MAX_SITEMAP_URLS]
    try:
        root = etree.fromstring(data, _PARSER)
    except etree.XMLSyntaxError:
        return "unknown", []
    if root is None:
        return "unknown", []
    kind = etree.QName(root).localname.lower()
    child = "sitemap" if kind == "sitemapindex" else "url"
    entries: list[tuple[str, str | None]] = []
    for node in root:
        if not isinstance(node.tag, str) or etree.QName(node).localname != child:
            continue
        loc = lastmod = None
        for part in node:
            if not isinstance(part.tag, str):
                continue
            name = etree.QName(part).localname
            if name == "loc" and part.text:
                loc = part.text.strip()
            elif name == "lastmod" and part.text:
                lastmod = part.text.strip()
        if loc:
            entries.append((loc, lastmod))
        if len(entries) >= MAX_SITEMAP_URLS:
            break
    return (kind if kind in ("urlset", "sitemapindex") else "unknown"), entries


def _after(lastmod: str | None, threshold: str | None) -> bool:
    if not threshold:
        return True
    if not lastmod:
        return False
    try:
        return datetime.fromisoformat(lastmod.replace("Z", "+00:00")).date() >= date.fromisoformat(threshold)
    except ValueError:
        return lastmod[:10] >= threshold


def iter_sitemap_urls(
    sitemap_urls: Iterable[str],
    get: Callable[[str], object],
    in_scope: Callable[[str], bool],
    config: dict,
    warnings: list[str],
    max_urls: int,
) -> Iterator[DiscoveredUrl]:
    pattern = re.compile(config["url_pattern"]) if config.get("url_pattern") else None
    max_sitemaps = int(config.get("max_sitemaps", 50))
    queue = deque(sitemap_urls)
    seen_maps: set[str] = set()
    yielded: set[str] = set()
    while queue and len(seen_maps) < max_sitemaps:
        sitemap_url = queue.popleft()
        if sitemap_url in seen_maps:
            continue
        seen_maps.add(sitemap_url)
        if not in_scope(sitemap_url):
            warnings.append("skipped a sitemap outside the preset scope")
            continue
        response = get(sitemap_url)
        if response is None:
            continue
        kind, entries = parse_sitemap(response.content)
        if kind == "unknown":
            warnings.append(f"could not parse sitemap {urlparse(sitemap_url).path}")
            continue
        for loc, lastmod in entries:
            if kind == "sitemapindex":
                if _after(lastmod, config.get("sitemap_lastmod_after")) or not lastmod:
                    queue.append(loc)
                continue
            if loc in yielded or not _after(lastmod, config.get("lastmod_after")):
                continue
            if pattern and not pattern.search(urlparse(loc).path):
                continue
            if not in_scope(loc):
                continue
            yielded.add(loc)
            yield DiscoveredUrl(loc, lastmod, source="sitemap")
            if len(yielded) >= max_urls:
                return
    if queue:
        warnings.append(f"sitemap index limit reached ({max_sitemaps} sitemaps)")


def parse_feed(content: bytes, base_url: str) -> list[dict]:
    import feedparser

    # feedparser is given bytes only: it never fetches URLs or resolves external entities itself.
    parsed = feedparser.parse(content, response_headers={"content-location": base_url})
    entries = []
    for entry in parsed.entries:
        link = entry.get("link") or next((l.get("href") for l in entry.get("links", []) if l.get("href")), None)
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        entries.append({
            "title": entry.get("title"), "link": urljoin(base_url, link) if link else None, "id": entry.get("id"),
            "published": datetime(*published[:6]).isoformat() if published else entry.get("published") or entry.get("updated"),
            "author": entry.get("author"), "summary": re.sub(r"<[^>]+>", " ", entry.get("summary", "") or "").strip()[:5000] or None,
            "categories": ", ".join(t.get("term", "") for t in entry.get("tags", []) if t.get("term")) or None,
        })
    return entries


def parse_llms_txt(text: str, base_url: str) -> list[dict]:
    """llms.txt: Markdown with H2 sections of `- [title](url): notes` links. A discovery hint only."""
    links, section = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        match = re.match(r"^\s*[-*]\s*\[([^\]]+)\]\(([^)\s]+)\)(?::\s*(.*))?", line)
        if match:
            links.append({"title": match.group(1), "url": urljoin(base_url, match.group(2)), "notes": match.group(3), "section": section, "optional": section == "Optional"})
    return links


def extract_links(html: str, base_url: str) -> list[str]:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    base = tree.css_first("base[href]")
    base_url = urljoin(base_url, base.attributes.get("href") or "") if base else base_url
    links = []
    for node in tree.css("a[href]"):
        rel = (node.attributes.get("rel") or "").lower()
        href = node.attributes.get("href") or ""
        if "nofollow" in rel or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append(urldefrag(urljoin(base_url, href.strip()))[0])
    return list(dict.fromkeys(links))


@dataclass
class Frontier:
    """Breadth-first crawl frontier. `on_change` persists additions and visits so crawls resume after restarts."""

    start_url: str
    max_depth: int = 2
    same_host_only: bool = True
    link_pattern: str | None = None
    exclude_pattern: str | None = None
    on_add: Callable[[str, int], None] = lambda url, depth: None
    on_visit: Callable[[str, str], None] = lambda url, status: None
    queue: deque = field(default_factory=deque)
    known: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._host = urlparse(self.start_url).hostname
        self._pattern = re.compile(self.link_pattern) if self.link_pattern else None
        self._exclude = re.compile(self.exclude_pattern) if self.exclude_pattern else None

    def restore(self, pending: list[tuple[str, int]], visited: list[str]) -> None:
        self.known.update(visited)
        for url, depth in pending:
            if url not in self.known:
                self.known.add(url)
                self.queue.append((url, depth))

    def seed(self) -> None:
        if not self.known:
            self.add(self.start_url, 0, force=True)

    @staticmethod
    def normalize(url: str) -> str:
        url = urldefrag(url)[0]
        parsed = urlparse(url)
        return parsed._replace(netloc=(parsed.netloc or "").lower(), path=parsed.path or "/").geturl()

    def add(self, url: str, depth: int, force: bool = False) -> bool:
        url = self.normalize(url)
        if url in self.known or depth > self.max_depth:
            return False
        parsed = urlparse(url)
        if not force:
            if parsed.scheme not in ("http", "https"):
                return False
            if self.same_host_only and parsed.hostname != self._host:
                return False
            if self._pattern and not self._pattern.search(parsed.path + (f"?{parsed.query}" if parsed.query else "")):
                return False
            if self._exclude and self._exclude.search(parsed.path):
                return False
        self.known.add(url)
        self.queue.append((url, depth))
        self.on_add(url, depth)
        return True

    def next(self) -> tuple[str, int] | None:
        return self.queue.popleft() if self.queue else None

    def visited(self, url: str, status: str = "done") -> None:
        self.on_visit(self.normalize(url), status)
