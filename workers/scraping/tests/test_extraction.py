from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from dataforge_scraping.extraction import PolicyViolation, extract_html, extract_html_pages


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/blocked":
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/page2":
            body = b"<html><body><article class='card'><h2>Gamma</h2><a class='link' href='/gamma'>Open</a></article></body></html>"
        elif self.path == "/quality":
            body = b"""
            <html><body>
              <article class='card'><h2>Alpha</h2><a class='link' href='/same'>Open</a></article>
              <article class='card'><a class='link' href='/same'>Open</a></article>
            </body></html>
            """
        else:
            body = b"""
            <html><body>
              <article class='card'><h2>Alpha</h2><a class='link' href='/alpha'>Open</a></article>
              <article class='card'><h2>Beta</h2><a class='link' href='/beta'>Open</a></article>
              <a class='next' href='/page2'>Next</a>
            </body></html>
            """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def fixture_url() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/list"
    finally:
        server.shutdown()
        thread.join()


def preset() -> dict[str, object]:
    return {
        "id": "fixture.html_list",
        "version": "1.0.0",
        "url_scope": {"allowed_hosts": ["127.0.0.1"], "allowed_path_patterns": [r"/list"]},
        "policy": {"requires_user_authorization_acknowledgement": True},
        "strategy": {"preferred": "http"},
        "request_limits": {"max_records_default": 1},
        "extraction": {
            "record_root": {"css": "article.card"},
            "fields": [
                {"key": "title", "selectors": [{"css": "h2"}]},
                {"key": "link", "selectors": [{"css": "a.link", "attribute": "href"}]},
            ],
        },
    }


def test_extracts_bounded_records_and_provenance(fixture_url: str) -> None:
    result = extract_html(fixture_url, preset())

    assert len(result.records) == 1
    assert result.records[0]["title"] == "Alpha"
    assert result.records[0]["link"] == "/alpha"
    assert result.records[0]["preset_id"] == "fixture.html_list"
    assert result.records[0]["strategy_used"] == "http"


def test_access_response_stops_collection(fixture_url: str) -> None:
    blocked_preset = preset()
    blocked_preset["url_scope"] = {
        "allowed_hosts": ["127.0.0.1"],
        "allowed_path_patterns": [r"/blocked"],
    }
    with pytest.raises(PolicyViolation, match="access or rate-limit"):
        extract_html(fixture_url.replace("/list", "/blocked"), blocked_preset)


def test_missing_acknowledgement_is_rejected(fixture_url: str) -> None:
    unacknowledged = preset()
    unacknowledged["policy"] = {}
    with pytest.raises(PolicyViolation, match="acknowledgement"):
        extract_html(fixture_url, unacknowledged)


def test_next_link_pagination_stops_at_limits_and_tracks_pages(fixture_url: str) -> None:
    paginated = preset()
    paginated["url_scope"] = {
        "allowed_hosts": ["127.0.0.1"],
        "allowed_path_patterns": [r"/(list|page2)"],
    }
    paginated["request_limits"] = {"max_records_default": 10, "max_pages_default": 2}
    paginated["pagination"] = {"type": "next_link", "next": {"css": "a.next"}}

    result = extract_html_pages(fixture_url, paginated)

    assert [record["title"] for record in result.records] == ["Alpha", "Beta", "Gamma"]
    assert result.pages_fetched == 2
    assert result.records[-1]["source_url"].endswith("/page2")


def test_required_fields_reject_records_and_unique_keys_suppress_duplicates(fixture_url: str) -> None:
    quality_preset = preset()
    quality_preset["url_scope"] = {
        "allowed_hosts": ["127.0.0.1"],
        "allowed_path_patterns": [r"/quality"],
    }
    quality_preset["extraction"]["fields"][0]["required"] = True
    quality_preset["request_limits"] = {"max_records_default": 10}
    quality_preset["validation"] = {"unique_by": ["link"], "minimum_record_coverage": 0.75}

    result = extract_html(fixture_url.replace("/list", "/quality"), quality_preset)

    assert len(result.records) == 1
    assert result.rejected_records == 1
    assert result.duplicate_records == 0
    assert any("missing required field title" in warning for warning in result.warnings)


def test_duplicate_records_are_counted_and_suppressed(fixture_url: str) -> None:
    quality_preset = preset()
    quality_preset["url_scope"] = {
        "allowed_hosts": ["127.0.0.1"],
        "allowed_path_patterns": [r"/quality"],
    }
    quality_preset["request_limits"] = {"max_records_default": 10}
    quality_preset["validation"] = {"unique_by": ["link"]}

    result = extract_html(fixture_url.replace("/list", "/quality"), quality_preset)

    assert len(result.records) == 1
    assert result.duplicate_records == 1