"""Stamp one version into every file that carries the app version, or verify they all agree.

    python scripts/set-version.py 1.4.0          # write the version everywhere
    python scripts/set-version.py 1.4.0-beta.1   # prereleases are allowed (beta channel)
    python scripts/set-version.py --check        # exit 1 unless every file has the same version
    python scripts/set-version.py --print        # print the version in apps/desktop/src-tauri/tauri.conf.json

The release workflow calls this with the requested version before building, so a release never
depends on version numbers having been bumped and committed by hand.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")

JSON_FILES = ["package.json", "apps/desktop/package.json", "apps/desktop/src-tauri/tauri.conf.json"]
TOML_FILES = [
    "apps/desktop/src-tauri/Cargo.toml",
    "services/application/pyproject.toml",
    "workers/matching/pyproject.toml",
    "workers/scraping/pyproject.toml",
]
PACKAGE_LOCK = "apps/desktop/package-lock.json"
CARGO_LOCK = "apps/desktop/src-tauri/Cargo.lock"
CARGO_PACKAGE = "dataforge-desktop"
TOML_VERSION = re.compile(r'(?m)^(version\s*=\s*)"[^"]*"')


def read(rel: str) -> str:
    # newline="" keeps CRLF files CRLF so a version bump never rewrites line endings.
    with open(ROOT / rel, encoding="utf-8", newline="") as handle:
        return handle.read()


def write(rel: str, text: str) -> None:
    # newline="" keeps whatever line endings the file already uses.
    with open(ROOT / rel, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def json_version(rel: str) -> str:
    return json.loads(read(rel))["version"]


def toml_version(rel: str) -> str:
    match = TOML_VERSION.search(read(rel))
    if not match:
        raise SystemExit(f"{rel}: no top-level version key")
    return re.search(r'"([^"]*)"', match.group(0)).group(1)


def current_versions() -> dict[str, str]:
    found = {rel: json_version(rel) for rel in JSON_FILES}
    found.update({rel: toml_version(rel) for rel in TOML_FILES})
    lock = json.loads(read(PACKAGE_LOCK))
    found[f"{PACKAGE_LOCK} (root)"] = lock["version"]
    found[f"{PACKAGE_LOCK} (packages[''])"] = lock["packages"][""]["version"]
    cargo = re.search(rf'(?s)name = "{CARGO_PACKAGE}"\r?\nversion = "([^"]*)"', read(CARGO_LOCK))
    if cargo:
        found[f"{CARGO_LOCK} ({CARGO_PACKAGE})"] = cargo.group(1)
    return found


def set_json(rel: str, version: str) -> None:
    text = read(rel)
    # Replace only the first top-level "version" so formatting and key order are untouched.
    new, count = re.subn(r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', text, count=1)
    if count != 1:
        raise SystemExit(f"{rel}: no version field")
    write(rel, new)


def set_toml(rel: str, version: str) -> None:
    new, count = TOML_VERSION.subn(rf'\g<1>"{version}"', read(rel), count=1)
    if count != 1:
        raise SystemExit(f"{rel}: no top-level version key")
    write(rel, new)


def set_package_lock(version: str) -> None:
    text = read(PACKAGE_LOCK)
    text, count = re.subn(r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', text, count=1)
    text, inner = re.subn(r'(?s)("packages"\s*:\s*\{\s*""\s*:\s*\{[^{}]*?"version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', text, count=1)
    if count != 1 or inner != 1:
        raise SystemExit(f"{PACKAGE_LOCK}: could not locate the root version fields")
    write(PACKAGE_LOCK, text)


def set_cargo_lock(version: str) -> None:
    text = read(CARGO_LOCK)
    pattern = rf'(?s)(name = "{CARGO_PACKAGE}"\r?\nversion = )"[^"]*"'
    new, count = re.subn(pattern, rf'\g<1>"{version}"', text, count=1)
    if count:
        write(CARGO_LOCK, new)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    arg = argv[0]
    if arg == "--print":
        print(json_version("apps/desktop/src-tauri/tauri.conf.json"))
        return 0
    if arg == "--check":
        versions = current_versions()
        distinct = set(versions.values())
        if len(distinct) == 1:
            print(f"All {len(versions)} version fields agree: {distinct.pop()}")
            return 0
        print("Version fields disagree:")
        for rel, value in sorted(versions.items()):
            print(f"  {value:<16} {rel}")
        return 1
    version = arg.removeprefix("v")
    if not SEMVER.match(version):
        print(f"'{arg}' is not a valid version (expected X.Y.Z or X.Y.Z-prerelease)")
        return 2
    for rel in JSON_FILES:
        set_json(rel, version)
    for rel in TOML_FILES:
        set_toml(rel, version)
    set_package_lock(version)
    set_cargo_lock(version)
    print(f"Version set to {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
