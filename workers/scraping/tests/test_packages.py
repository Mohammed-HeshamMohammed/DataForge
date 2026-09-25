from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import httpx

from dataforge_scraping.extraction import PolicyViolation, api_auth_headers, extract_document, extract_html_pages
from dataforge_scraping.packages import generate_key, run_health_check, sign_package, verify_package
from dataforge_scraping.presets import validate_preset

PRESETS = Path(__file__).parents[3] / "packages" / "presets"


def load(name: str) -> dict:
    return json.loads((PRESETS / name).read_text(encoding="utf-8-sig"))


def fixtures_for(preset: dict) -> dict[str, str]:
    return {p: (PRESETS / p).read_text(encoding="utf-8") for p in preset["health"]["fixture_tests"] if (PRESETS / p).exists()}


@pytest.mark.parametrize("name", sorted(p.name for p in PRESETS.glob("*.json")))
def test_every_bundled_preset_passes_its_fixture_health_check(name: str) -> None:
    preset = load(name)
    assert validate_preset(preset) == []
    result = run_health_check(preset, fixtures_for(preset))
    assert result["status"] == "passed", result


def test_changed_layout_fixture_degrades_health() -> None:
    preset = load("generic.html_list@1.0.0.json")
    changed = {preset["health"]["fixture_tests"][0]: "<html><div class='card'><span>moved</span></div></html>"}
    result = run_health_check(preset, changed)
    assert result["status"] == "failed" and "expected at least" in result["failures"][0]


def test_wikipedia_fixture_extracts_typed_clean_records() -> None:
    preset = load("wikipedia.search_api@1.0.0.json")
    records, rejected, _ = extract_document(fixtures_for(preset)["fixtures/wikipedia/search.json"], "https://en.wikipedia.org/w/api.php", preset)
    assert rejected == [] and records[0]["snippet"] == "A fixture snippet" and records[0]["word_count"] == 210


def test_signed_package_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    key = generate_key(tmp_path / "keys" / "presets.pem")
    package = {"name": "fixture-pack", "version": "1.0.0", "min_app_version": "0.1.0", "presets": [load("hackernews.search_api@1.0.0.json")], "fixtures": {}}
    signed = sign_package(package, (tmp_path / "keys" / "presets.pem").read_bytes())
    trusted = {key["key_id"]: key["public_key_pem"]}
    assert verify_package(signed, trusted)["name"] == "fixture-pack"

    tampered = copy.deepcopy(signed)
    tampered["package"]["presets"][0]["url_scope"]["allowed_hosts"].append("evil.example")
    with pytest.raises(ValueError, match="signature is invalid"):
        verify_package(tampered, trusted)
    with pytest.raises(ValueError, match="untrusted key"):
        verify_package(signed, {})


def test_api_credentials_only_for_declared_api_integrations() -> None:
    preset = load("generic.json_api@1.0.0.json")
    assert api_auth_headers(preset, "secret") == {}
    preset["strategy"]["api_integration"] = {"auth": "header", "header_name": "X-Api-Key"}
    assert validate_preset(preset) == []
    assert api_auth_headers(preset, "secret") == {"X-Api-Key": "secret"}
    with pytest.raises(PolicyViolation, match="credential"):
        api_auth_headers(preset, None)
    preset["strategy"]["api_integration"] = {"auth": "basic"}
    assert api_auth_headers(preset, "reader:p@ss") == {"Authorization": "Basic cmVhZGVyOnBAc3M="}
    assert api_auth_headers(preset, '{"username":"reader","password":"p@ss"}') == {"Authorization": "Basic cmVhZGVyOnBAc3M="}
    preset["url_scope"] = {"allowed_hosts": ["api.example.test"], "allowed_path_patterns": []}
    preset["strategy"]["api_integration"] = {"auth": "oauth2_client_credentials", "token_url": "https://api.example.test/oauth/token"}
    assert validate_preset(preset) == []
    assert api_auth_headers(preset, '{"client_id":"dataforge","client_secret":"secret"}') == {}
    preset["strategy"]["api_integration"] = {"auth": "header", "header_name": "Cookie"}
    assert any("header_name" in e for e in validate_preset(preset))


def test_oauth_client_credentials_are_exchanged_in_memory() -> None:
    preset = {
        "id": "internal.oauth_api", "version": "1.0.0", "display_name": "Internal OAuth API", "category": "internal", "page_type": "api_collection", "status": "active",
        "policy": {"requires_user_authorization_acknowledgement": True, "robots_policy": "respect", "authentication": "forbidden", "captcha_or_access_challenge": "stop", "paywall_or_rate_limit": "stop"},
        "request_limits": {"max_concurrency": 1, "min_delay_ms": 0, "max_pages_default": 1, "max_records_default": 10, "max_duration_seconds": 60},
        "url_scope": {"allowed_hosts": ["api.internal.test"], "allowed_path_patterns": []},
        "strategy": {"preferred": "api", "allowed": ["api"], "api_integration": {"auth": "oauth2_client_credentials", "token_url": "https://api.internal.test/oauth/token", "scope": "read"}},
        "request": {"method": "GET", "url_template": "https://api.internal.test/items"},
        "pagination": {"type": "none"}, "validation": {"unique_by": ["id"]},
        "extraction": {"item_path": "items", "fields": [{"key": "id", "path": "id", "required": True}]},
    }
    assert validate_preset(preset) == []
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/oauth/token":
            assert b"client_id=dataforge" in request.content and b"client_secret=secret" in request.content and b"scope=read" in request.content
            return httpx.Response(200, json={"access_token": "short-lived-token", "token_type": "Bearer"})
        if request.url.path == "/items":
            return httpx.Response(200, json={"items": [{"id": "A-1"}]})
        return httpx.Response(404, text="not found")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = extract_html_pages("", preset, credential='{"client_id":"dataforge","client_secret":"secret"}', client=client, purpose="internal_analysis")
    assert result.records[0]["id"] == "A-1"
    assert ("/items", "Bearer short-lived-token") in seen
    assert "short-lived-token" not in json.dumps(result.records)
