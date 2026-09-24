"""Keeps the DataForge version identical in every manifest and computes release versions.

The version lives in tauri.conf.json (what the installer and the updater report) and is mirrored into the npm,
Cargo, and Python manifests, the lockfiles, and the service package. The Release workflow runs this script, so a
manual release always moves every file together.

    python scripts/bump-version.py current                 # print the version
    python scripts/bump-version.py check [--tag v1.2.3]    # every file agrees (and matches the tag); exit 1 if not
    python scripts/bump-version.py next --part minor       # print the next version without writing
    python scripts/bump-version.py bump --part patch [--channel beta]   # write the next version, print it
    python scripts/bump-version.py set 1.4.0               # write an exact version, print it

Channels: `stable` bumps MAJOR.MINOR.PATCH (a beta of the same patch is promoted by `--part patch`); `beta` produces
X.Y.Z-beta.N, counting N up while the base stays the same.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-beta\.([1-9]\d*))?$")


def _json_version(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def _set_json_version(path: Path, version: str, lockfile: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    # Only the first top-level "version" (and, in package-lock.json, the root package entry) change; dependency
    # versions stay untouched, and the file keeps its formatting.
    text = re.sub(r'^(  "version": ")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1, flags=re.M)
    if lockfile:
        text = re.sub(r'(\n    "": \{\n(?:      .*\n)*?      "version": ")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1)
    path.write_text(text, encoding="utf-8")


def _toml_version(path: Path, table: str) -> str:
    text = path.read_text(encoding="utf-8")
    section = re.search(rf"^\[{re.escape(table)}\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    match = re.search(r'^version = "([^"]+)"', section.group(1), re.M) if section else None
    if not match:
        raise ValueError(f"{path}: no version in [{table}]")
    return match.group(1)


def _set_toml_version(path: Path, table: str, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    section = re.search(rf"^\[{re.escape(table)}\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    body = re.sub(r'^version = "[^"]+"', f'version = "{version}"', section.group(1), count=1, flags=re.M)
    path.write_text(text[: section.start(1)] + body + text[section.end(1):], encoding="utf-8")


def _cargo_lock_version(path: Path) -> str:
    match = re.search(r'\[\[package\]\]\nname = "dataforge-desktop"\nversion = "([^"]+)"', path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"{path}: no dataforge-desktop package")
    return match.group(1)


def _set_cargo_lock_version(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r'(\[\[package\]\]\nname = "dataforge-desktop"\nversion = ")[^"]+(")', rf"\g<1>{version}\g<2>", text, count=1), encoding="utf-8")


def _python_version(path: Path) -> str:
    match = re.search(r'^__version__ = "([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    if not match:
        raise ValueError(f"{path}: no __version__")
    return match.group(1)


def _set_python_version(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r'^__version__ = "[^"]+"', f'__version__ = "{version}"', text, count=1, flags=re.M), encoding="utf-8")


def files(root: Path) -> list[tuple[str, callable, callable]]:
    """(relative path, read, write) for every place the version lives. tauri.conf.json comes first: it is the source."""
    desktop = root / "apps" / "desktop"
    entries = [
        ("apps/desktop/src-tauri/tauri.conf.json", lambda: _json_version(desktop / "src-tauri" / "tauri.conf.json"),
         lambda v: _set_json_version(desktop / "src-tauri" / "tauri.conf.json", v)),
        ("package.json", lambda: _json_version(root / "package.json"), lambda v: _set_json_version(root / "package.json", v)),
        ("apps/desktop/package.json", lambda: _json_version(desktop / "package.json"), lambda v: _set_json_version(desktop / "package.json", v)),
        ("apps/desktop/package-lock.json", lambda: _json_version(desktop / "package-lock.json"),
         lambda v: _set_json_version(desktop / "package-lock.json", v, lockfile=True)),
        ("apps/desktop/package-lock.json (root package)", lambda: json.loads((desktop / "package-lock.json").read_text(encoding="utf-8"))["packages"][""]["version"],
         lambda v: None),
        ("apps/desktop/src-tauri/Cargo.toml", lambda: _toml_version(desktop / "src-tauri" / "Cargo.toml", "package"),
         lambda v: _set_toml_version(desktop / "src-tauri" / "Cargo.toml", "package", v)),
        ("apps/desktop/src-tauri/Cargo.lock", lambda: _cargo_lock_version(desktop / "src-tauri" / "Cargo.lock"),
         lambda v: _set_cargo_lock_version(desktop / "src-tauri" / "Cargo.lock", v)),
        ("services/application/src/dataforge_application/__init__.py", lambda: _python_version(root / "services/application/src/dataforge_application/__init__.py"),
         lambda v: _set_python_version(root / "services/application/src/dataforge_application/__init__.py", v)),
    ]
    for package in ("services/application", "workers/scraping", "workers/matching"):
        path = root / package / "pyproject.toml"
        entries.append((f"{package}/pyproject.toml", lambda p=path: _toml_version(p, "project"), lambda v, p=path: _set_toml_version(p, "project", v)))
    return entries


def parse(version: str) -> tuple[int, int, int, int | None]:
    match = SEMVER.match(version)
    if not match:
        raise ValueError(f"{version!r} is not a release version (MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-beta.N)")
    major, minor, patch, beta = match.groups()
    return int(major), int(minor), int(patch), int(beta) if beta else None


def next_version(current: str, part: str = "patch", channel: str = "stable") -> str:
    major, minor, patch, beta = parse(current)
    if part not in ("patch", "minor", "major") or channel not in ("stable", "beta"):
        raise ValueError("part must be patch, minor, or major; channel must be stable or beta")

    def bumped() -> tuple[int, int, int]:
        if part == "major":
            return major + 1, 0, 0
        if part == "minor":
            return major, minor + 1, 0
        return major, minor, patch + 1

    if channel == "stable":
        if beta is not None and part == "patch":
            return f"{major}.{minor}.{patch}"  # promote the beta that was being tested
        return "{}.{}.{}".format(*bumped())
    if beta is not None and part == "patch":
        return f"{major}.{minor}.{patch}-beta.{beta + 1}"  # another beta of the same upcoming release
    return "{}.{}.{}-beta.1".format(*bumped())


def read_all(root: Path = ROOT) -> dict[str, str]:
    return {name: read() for name, read, _ in files(root)}


def current(root: Path = ROOT) -> str:
    return files(root)[0][1]()


def write_all(version: str, root: Path = ROOT) -> None:
    parse(version)
    for _, _, write in files(root):
        write(version)
    found = read_all(root)
    wrong = {name: value for name, value in found.items() if value != version}
    if wrong:
        raise RuntimeError(f"version files did not update: {wrong}")


def problems(root: Path = ROOT, tag: str | None = None) -> list[str]:
    found = read_all(root)
    expected = found[files(root)[0][0]]
    issues = [f"{name} has {value}, expected {expected} (from tauri.conf.json)" for name, value in found.items() if value != expected]
    try:
        parse(expected)
    except ValueError as error:
        issues.append(str(error))
    if tag is not None and tag.removeprefix("v") != expected:
        issues.append(f"tag {tag} does not match the app version {expected}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("current")
    check = commands.add_parser("check")
    check.add_argument("--tag")
    for name in ("next", "bump"):
        command = commands.add_parser(name)
        command.add_argument("--part", choices=("patch", "minor", "major"), default="patch")
        command.add_argument("--channel", choices=("stable", "beta"), default="stable")
    exact = commands.add_parser("set")
    exact.add_argument("version")
    args = parser.parse_args(argv)
    try:
        if args.command == "current":
            print(current(args.root))
        elif args.command == "check":
            issues = problems(args.root, args.tag)
            for issue in issues:
                print(f"::error::{issue}" if "GITHUB_ACTIONS" in __import__("os").environ else issue, file=sys.stderr)
            if issues:
                return 1
            print(f"All version files agree on {current(args.root)}")
        elif args.command == "next":
            print(next_version(current(args.root), args.part, args.channel))
        elif args.command == "bump":
            version = next_version(current(args.root), args.part, args.channel)
            write_all(version, args.root)
            print(version)
        else:
            version = args.version.removeprefix("v")
            parse(version)
            write_all(version, args.root)
            print(version)
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
