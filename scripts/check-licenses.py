"""License inventory for the packaged service (all runtime dependencies, transitively).

DataForge is an academic graduation project, so copyleft licenses (GPL, AGPL, LGPL, MPL) are allowed
(DATAFORGE_MASTER_PLAN.md, principle 6). By default this prints the inventory for the thesis software
citation appendix and exits 0. `--strict` restores the old permissive-only gate.

    python scripts/check-licenses.py [--strict] [--markdown]
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = re.compile(r"\b(MIT|BSD|Apache|PSF|Python Software Foundation|ISC|MPL|Mozilla Public License|Zope Public|ZPL|W3C|Unlicense|CC0|Public Domain|HPND|Historical Permission)\b", re.I)
# Packages with a choice of licenses where DataForge uses the permissive option.
DUAL_LICENSED = {"tld": "MPL-1.1 (chosen from MPL-1.1 OR GPL-2.0 OR LGPL-2.1)", "text-unidecode": "Artistic License (chosen from Artistic OR GPL)"}
# Pulled in only as optional extras and excluded from the PyInstaller build (packaging/dataforge-service.spec).
EXCLUDED_FROM_BUNDLE = {"rfc3987": "GPL-3.0-or-later; optional jsonschema format checker, excluded from the build"}
KNOWN = {"pypdfium2": "Apache-2.0 OR BSD-3-Clause", "pdfminer.six": "MIT", "twisted": "MIT", "incremental": "MIT", "automat": "MIT", "constantly": "MIT", "hyperlink": "MIT"}


def requirement_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.append(re.split(r"[<>=!~\[;]", line, maxsplit=1)[0].strip())
    return names


def closure(names: list[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(names)
    while stack:
        name = stack.pop().lower().replace("_", "-")
        if name in seen:
            continue
        try:
            requires = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            continue
        seen.add(name)
        for requirement in requires:
            if "extra ==" in requirement and "format" not in requirement:
                continue
            marker = requirement.split(";", 1)
            if len(marker) == 2 and ("python_version < '3.14'" not in marker[1] and "implementation_name != 'pypy'" not in marker[1] and "platform_python_implementation == 'CPython'" not in marker[1] and "sys_platform == \"win32\"" not in marker[1] and "extra ==" not in marker[1]):
                continue
            stack.append(re.split(r"[<>=!~\[;( ]", requirement.strip(), maxsplit=1)[0])
    return seen


def license_of(name: str) -> str:
    meta = metadata.metadata(name)
    parts = [meta.get("License-Expression") or "", (meta.get("License") or "")[:120]]
    parts += [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License")]
    return " | ".join(p for p in parts if p)


def main() -> int:
    strict = "--strict" in sys.argv
    markdown = "--markdown" in sys.argv
    if markdown:
        print("| Package | Version | License |")
        print("| --- | --- | --- |")
        for name in sorted(closure(requirement_names(ROOT / "requirements.txt"))):
            print(f"| {name} | {metadata.version(name)} | {DUAL_LICENSED.get(name) or KNOWN.get(name) or license_of(name).split(' | ')[0] or 'unknown'} |")
        return 0
    failures = []
    for name in sorted(closure(requirement_names(ROOT / "requirements.txt"))):
        if name in DUAL_LICENSED or name in EXCLUDED_FROM_BUNDLE:
            print(f"note  {name}: {DUAL_LICENSED.get(name) or EXCLUDED_FROM_BUNDLE[name]}")
            continue
        text = KNOWN.get(name) or license_of(name)
        copyleft = re.search(r"\b(A?GPL|LGPL|GNU)\b", text) and not ALLOWED.search(text.split("|")[0])
        if copyleft or not ALLOWED.search(text):
            failures.append(f"{name}: {text or 'no license metadata'}")
    for failure in failures:
        print(("FAIL  " if strict else "copyleft or unknown  ") + failure)
    if not strict:
        print(f"license inventory: {len(failures)} copyleft or unclassified package(s); allowed by the academic licensing policy")
        return 0
    print(f"{'license check failed' if failures else 'license check passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
