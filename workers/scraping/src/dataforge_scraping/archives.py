"""Web archives and bulk corpora: Wayback Machine and Common Crawl CDX indexes, WARC byte-range reads
(warcio), Web Data Commons schema.org N-Quads subsets, and opt-in WARC capture of fetched responses.

The CDX protocol is implemented over DataForge's policy client rather than cdx-toolkit's own `requests`
session, so archive queries obey the same scheduler, robots checks, trust store, and stop rules as
live collection. Archives let users collect historical data without loading the original sites.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote, urlencode

WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
COMMON_CRAWL_INDEX = "https://index.commoncrawl.org"
COMMON_CRAWL_DATA = "https://data.commoncrawl.org"
ARCHIVE_HOSTS = ("web.archive.org", "index.commoncrawl.org", "data.commoncrawl.org")
_STRIP_HEADERS = {"set-cookie", "cookie", "authorization", "proxy-authorization"}


@dataclass
class Capture:
    url: str
    timestamp: str
    status: str | None
    mime: str | None
    digest: str | None
    archive: str
    filename: str | None = None
    offset: int | None = None
    length: int | None = None
    crawl: str | None = None

    def provenance(self) -> dict:
        return {"archive": self.archive, "archive_capture_time": self.timestamp, "archive_digest": self.digest, "archive_crawl": self.crawl}


def wayback_query_url(url_pattern: str, limit: int, from_date: str | None = None, to_date: str | None = None, collapse_digest: bool = True) -> str:
    params: list[tuple[str, str]] = [("url", url_pattern), ("output", "json"), ("limit", str(int(limit))), ("filter", "statuscode:200")]
    if from_date:
        params.append(("from", re.sub(r"\D", "", from_date)[:14]))
    if to_date:
        params.append(("to", re.sub(r"\D", "", to_date)[:14]))
    if collapse_digest:
        params.append(("collapse", "digest"))
    return f"{WAYBACK_CDX}?{urlencode(params)}"


def parse_wayback_cdx(text: str) -> list[Capture]:
    text = text.strip()
    if not text:
        return []
    rows = json.loads(text)
    if not rows:
        return []
    header = rows[0]
    captures = []
    for row in rows[1:]:
        item = dict(zip(header, row))
        captures.append(Capture(item.get("original", ""), item.get("timestamp", ""), item.get("statuscode"), item.get("mimetype"), item.get("digest"), "wayback"))
    return captures


def wayback_snapshot_url(capture: Capture) -> str:
    # "id_" returns the archived bytes without the Wayback toolbar or rewritten links.
    return f"https://web.archive.org/web/{capture.timestamp}id_/{quote(capture.url, safe=':/?&=%#+,;@')}"


def parse_collinfo(text: str) -> list[dict]:
    return [{"id": item["id"], "name": item.get("name"), "cdx_api": item.get("cdx-api")} for item in json.loads(text)]


def common_crawl_query_url(cdx_api: str, url_pattern: str, limit: int) -> str:
    return f"{cdx_api}?{urlencode([('url', url_pattern), ('output', 'json'), ('limit', str(int(limit))), ('filter', 'status:200')])}"


def parse_common_crawl_cdx(text: str, crawl: str) -> list[Capture]:
    captures = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        item = json.loads(line)
        captures.append(Capture(item.get("url", ""), item.get("timestamp", ""), item.get("status"), item.get("mime-detected") or item.get("mime"), item.get("digest"),
                                "common_crawl", item.get("filename"), int(item["offset"]) if item.get("offset") else None, int(item["length"]) if item.get("length") else None, crawl))
    return captures


def warc_range(capture: Capture) -> tuple[str, dict[str, str]]:
    if capture.filename is None or capture.offset is None or capture.length is None:
        raise ValueError("Capture has no WARC location")
    return f"{COMMON_CRAWL_DATA}/{capture.filename}", {"Range": f"bytes={capture.offset}-{capture.offset + capture.length - 1}"}


def read_warc_record(content: bytes) -> tuple[bytes, dict[str, str]]:
    """Payload and HTTP headers of the first response record in a (gzip member) WARC slice."""
    from warcio.archiveiterator import ArchiveIterator

    for record in ArchiveIterator(io.BytesIO(content)):
        if record.rec_type == "response":
            headers = {k.lower(): v for k, v in (record.http_headers.headers if record.http_headers else [])}
            return record.content_stream().read(), headers
    raise ValueError("No response record in WARC slice")


# --- Web Data Commons N-Quads ---------------------------------------------------------------------

_NQUAD = re.compile(r'^(<[^>]*>|_:\S+)\s+<([^>]*)>\s+(<[^>]*>|_:\S+|"(?:[^"\\]|\\.)*"(?:@[\w-]+|\^\^<[^>]*>)?)\s+(<[^>]*>|_:\S+)\s*\.\s*$')
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def _literal(term: str) -> str:
    if term.startswith("<"):
        return term[1:-1]
    if term.startswith('"'):
        body = term[1: term.rindex('"')]
        return body.encode("latin-1", "backslashescape").decode("unicode_escape", "replace") if "\\" in body else body
    return term


def _local(iri: str) -> str:
    return re.split(r"[/#]", iri.rstrip("/"))[-1]


def _page_entities(nodes: dict[str, dict], graph: str) -> list[dict]:
    def build(node_id: str, depth: int = 0) -> dict:
        node = nodes[node_id]
        data: dict[str, object] = {"@type": node["types"]}
        for key, values in node["props"].items():
            resolved = [build(v, depth + 1) if v in nodes and depth < 4 and v != node_id else v for v in values]
            data[key] = resolved[0] if len(resolved) == 1 else resolved
        return data

    referenced = {v for node in nodes.values() for values in node["props"].values() for v in values if v in nodes}
    entities = []
    for node_id, node in nodes.items():
        if node["types"] and node_id not in referenced:
            data = build(node_id)
            entities.append({"syntax": "wdc-nquads", "types": node["types"], "data": {**data, "url": data.get("url") or graph}, "page": graph})
    return entities


def iter_nquad_pages(lines: Iterator[bytes | str], max_line_bytes: int = 100_000) -> Iterator[tuple[str, list[dict]]]:
    """Group quads by page graph (WDC files are ordered by page) and yield (page_url, entities)."""
    current_graph, nodes = None, {}
    for raw in lines:
        if isinstance(raw, bytes):
            if len(raw) > max_line_bytes:
                continue
            raw = raw.decode("utf-8", "replace")
        match = _NQUAD.match(raw.strip())
        if not match:
            continue
        subject, predicate, obj, graph = match.groups()
        graph = _literal(graph)
        if graph != current_graph:
            if current_graph is not None and nodes:
                yield current_graph, _page_entities(nodes, current_graph)
            current_graph, nodes = graph, {}
        node = nodes.setdefault(subject, {"types": [], "props": {}})
        if predicate == _RDF_TYPE:
            node["types"].append(_local(_literal(obj)))
        else:
            node["props"].setdefault(_local(predicate), []).append(obj if obj.startswith("_:") else _literal(obj))
    if current_graph is not None and nodes:
        yield current_graph, _page_entities(nodes, current_graph)


def open_lines(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as probe:
        compressed = probe.read(2) == b"\x1f\x8b"
    with (gzip.open(path, "rb") if compressed else open(path, "rb")) as handle:
        yield from handle


# --- opt-in WARC capture (Track J) ----------------------------------------------------------------

class WarcCapture:
    """Writes fetched responses to a per-job WARC file, only when the user opts in. Cookies and
    authorization headers are stripped before writing."""

    def __init__(self, path: Path) -> None:
        from warcio.warcwriter import WARCWriter

        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = open(path, "ab")
        self._writer = WARCWriter(self._handle, gzip=True)
        self.records = 0

    def record(self, url: str, status_code: int, reason: str, headers: dict[str, str], body: bytes) -> None:
        from warcio.statusandheaders import StatusAndHeaders

        http_headers = StatusAndHeaders(f"{status_code} {reason}", [(k, v) for k, v in headers.items() if k.lower() not in _STRIP_HEADERS], protocol="HTTP/1.1")
        record = self._writer.create_warc_record(url, "response", payload=io.BytesIO(body), http_headers=http_headers,
                                                 warc_headers_dict={"WARC-Date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        self._writer.write_record(record)
        self.records += 1

    def close(self) -> None:
        self._handle.close()


def replay_warc(path: Path) -> Iterator[tuple[str, bytes, dict[str, str]]]:
    """(url, body, headers) for each response in a capture: reproducible re-extraction and fixture creation."""
    from warcio.archiveiterator import ArchiveIterator

    with open(path, "rb") as handle:
        for record in ArchiveIterator(handle):
            if record.rec_type == "response":
                headers = {k.lower(): v for k, v in (record.http_headers.headers if record.http_headers else [])}
                yield record.rec_headers.get_header("WARC-Target-URI"), record.content_stream().read(), headers


CaptureSink = Callable[[str, int, str, dict, bytes], None]
