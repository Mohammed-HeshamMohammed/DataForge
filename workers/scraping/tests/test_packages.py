from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from dataforge_scraping.extraction import PolicyViolation, api_auth_headers, extract_document
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
    preset["strategy"]["api_integration"] = {"auth": "header", "header_name": "Cookie"}
    assert any("header_name" in e for e in validate_preset(preset))
