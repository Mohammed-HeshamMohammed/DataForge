"""Preset health checks against sanitized fixtures, and Ed25519-signed preset packages."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from .extraction import extract_document
from .presets import validate_preset

PACKAGE_SCHEMA_VERSION = 1


def run_health_check(preset: dict, fixtures: dict[str, str]) -> dict:
    """fixtures: fixture path -> document text. Never touches the network."""
    expected = (preset.get("health") or {}).get("expected", {})
    minimum_records = int(expected.get("minimum_records", 1))
    minimum_coverage = float(expected.get("required_field_coverage", 0.0))
    required = [f["key"] for f in preset["extraction"]["fields"] if f.get("required")]
    results, failures = [], []
    for path in (preset.get("health") or {}).get("fixture_tests", []):
        if path not in fixtures:
            failures.append(f"fixture missing: {path}")
            continue
        try:
            records, rejected, warnings = extract_document(fixtures[path], "https://fixture.invalid/", preset)
        except Exception as error:  # noqa: BLE001 - a broken selector or fixture is a health failure
            failures.append(f"{path}: {type(error).__name__}: {error}")
            continue
        candidates = len(records) + len(rejected)
        coverage = sum(all(r.get(k) not in (None, "") for k in required) for r in records) / candidates if candidates and required else 1.0
        results.append({"fixture": path, "records": len(records), "rejected": len(rejected), "required_field_coverage": round(coverage, 3)})
        if len(records) < minimum_records:
            failures.append(f"{path}: {len(records)} records, expected at least {minimum_records}")
        if coverage < minimum_coverage:
            failures.append(f"{path}: required field coverage {coverage:.2f} below {minimum_coverage:.2f}")
    if not results and not failures:
        failures.append("preset declares no fixture tests")
    return {"status": "passed" if not failures else "failed", "fixtures": results, "failures": failures}


def _canonical(package: dict) -> bytes:
    return json.dumps(package, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def public_key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode()[:16]


def generate_key(private_key_path: Path) -> dict:
    key = Ed25519PrivateKey.generate()
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    public = key.public_key()
    return {"key_id": public_key_id(public), "public_key_pem": public.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()}


def sign_package(package: dict, private_key_pem: bytes) -> dict:
    key = load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Preset packages must be signed with an Ed25519 key")
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "key_id": public_key_id(key.public_key()),
        "signature": base64.b64encode(key.sign(_canonical(package))).decode(),
        "package": package,
    }


def verify_package(document: dict, trusted_keys: dict[str, str]) -> dict:
    """Returns the verified package or raises ValueError. trusted_keys: key_id -> public key PEM."""
    if document.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ValueError("Unsupported preset package format")
    pem = trusted_keys.get(str(document.get("key_id")))
    if pem is None:
        raise ValueError("Preset package is signed by an untrusted key")
    public = load_pem_public_key(pem.encode())
    if not isinstance(public, Ed25519PublicKey):
        raise ValueError("Trusted key is not Ed25519")
    try:
        public.verify(base64.b64decode(document["signature"]), _canonical(document["package"]))
    except (InvalidSignature, KeyError, ValueError) as error:
        raise ValueError("Preset package signature is invalid; the package may have been tampered with") from error
    package = document["package"]
    for key in ("name", "version", "presets"):
        if key not in package:
            raise ValueError(f"Preset package is missing {key}")
    for preset in package["presets"]:
        errors = validate_preset(preset)
        if errors:
            raise ValueError(f"Preset {preset.get('id')} in package is invalid: {'; '.join(errors)}")
        if str(preset["id"]).startswith("custom."):
            raise ValueError("Signed packages cannot contain custom.* presets")
    return package
