from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

import pytest

from dataforge_scraping.extraction import PolicyViolation, extract_html_pages
from dataforge_scraping.presets import resolve_for_url, validate_preset

PRESETS = Path(__file__).parents[3] / "packages" / "presets"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        status, content_type, body = 200, "text/html", b""
        if url.path == "/products":
            page = int(query.get("page", ["1"])[0])
            cards = {1: [("Widget", "$1,299.50", "4.5 out of 5", "1,204 ratings")], 2: [("Gadget", "$5", "3 out of 5", "7 ratings")]}.get(page, [])
            body = "".join(
                f"<div class='item'><h2> {t}  </h2><span class='p'>{p}</span><i class='r'>{r}</i><b class='c'>{c}</b><a href='/p/{t}'>x</a></div>"
                for t, p, r, c in cards
            ).encode()
        elif url.path == "/api":
            cursor = query.get("cursor", [""])[0]
            document = {"items": [{"id": "1", "name": " One "}], "next_cursor": "b"} if not cursor else {"items": [{"id": "2", "name": "Two"}], "next_cursor": None}
            content_type, body = "application/json", json.dumps(document).encode()
        elif url.path == "/challenge":
            body = b"<html><div class='g-recaptcha'></div></html>"
        elif url.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "https://evil.example/")
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture()
def base() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def load(name: str) -> dict:
    return json.loads((PRESETS / name).read_text(encoding="utf-8-sig"))


def test_bundled_presets_are_schema_valid() -> None:
    for path in PRESETS.glob("*.json"):
        assert validate_preset(json.loads(path.read_text(encoding="utf-8-sig"))) == [], path.name


def test_invalid_preset_reports_actionable_errors() -> None:
    preset = load("generic.html_list@1.0.0.json")
    preset["policy"]["authentication"] = "required"
    preset["extraction"]["fields"][0]["transforms"] = ["run_script"]
    errors = validate_preset(preset)
    assert "policy.authentication must be forbidden" in errors
    assert any("unknown transforms" in error for error in errors)


def test_generic_scope_pins_to_user_host_only() -> None:
    resolved = resolve_for_url(load("generic.html_list@1.0.0.json"), "https://example.org/list")
    assert resolved["url_scope"]["allowed_hosts"] == ["example.org"]


def test_page_parameter_pagination_transforms_and_types(base: str) -> None:
    preset = load("generic.html_list@1.0.0.json")
    preset["request_limits"]["min_delay_ms"] = 0
    preset["extraction"] = {
        "record_root": {"css": "div.item"},
        "fields": [
            {"key": "title", "required": True, "selectors": [{"css": "h2::text"}], "transforms": ["trim", "collapse_whitespace"]},
            {"key": "price", "type": "decimal", "selectors": [{"css": ".p"}], "transforms": ["parse_currency_amount"]},
            {"key": "rating", "type": "decimal", "selectors": [{"css": ".r"}], "transforms": ["parse_rating"], "validate": {"minimum": 0, "maximum": 5}},
            {"key": "reviews", "type": "integer", "selectors": [{"css": ".c"}], "transforms": ["parse_integer"]},
            {"key": "url", "type": "url", "selectors": [{"css": "a::attr(href)"}], "transforms": ["to_absolute_url"]},
        ],
    }
    preset["pagination"] = {"type": "page_parameter", "parameter": "page", "start": 1}
    preset["validation"] = {"unique_by": ["url"]}
    events = []
    result = extract_html_pages(f"{base}/products?page=1", preset, on_page=events.append)
    assert [r["title"] for r in result.records] == ["Widget", "Gadget"]
    assert result.records[0]["price"] == 1299.5
    assert result.records[0]["rating"] == 4.5
    assert result.records[0]["reviews"] == 1204
    assert result.records[0]["url"].endswith("/p/Widget")
    assert result.stop_reason == "no_new_records"
    assert [e["page"] for e in events] == [1, 2, 3]


def test_json_api_with_cursor_pagination(base: str) -> None:
    preset = load("generic.json_api@1.0.0.json")
    preset["request_limits"]["min_delay_ms"] = 0
    result = extract_html_pages(f"{base}/api", preset)
    assert [(r["id"], r["name"]) for r in result.records] == [("1", "One"), ("2", "Two")]
    assert result.strategy_used == "api"


def test_access_challenge_stops_collection(base: str) -> None:
    with pytest.raises(PolicyViolation, match="access challenge"):
        extract_html_pages(f"{base}/challenge", load("generic.html_list@1.0.0.json"))


def test_redirect_outside_scope_is_refused(base: str) -> None:
    with pytest.raises(PolicyViolation, match="HTTPS|allow-list"):
        extract_html_pages(f"{base}/redirect", load("generic.html_list@1.0.0.json"))


def test_cancellation_stops_before_next_request(base: str) -> None:
    preset = load("generic.html_list@1.0.0.json")
    preset["extraction"]["record_root"] = {"css": "div.item"}
    result = extract_html_pages(f"{base}/products", preset, should_stop=lambda: True)
    assert result.records == () and result.stop_reason == "cancelled"
