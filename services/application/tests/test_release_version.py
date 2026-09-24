"""scripts/bump-version.py: release version arithmetic, and that every manifest moves together (and only its version)."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
spec = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts" / "bump-version.py")
bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump)

VERSION_FILES = [
    "apps/desktop/src-tauri/tauri.conf.json", "package.json", "apps/desktop/package.json", "apps/desktop/package-lock.json",
    "apps/desktop/src-tauri/Cargo.toml", "apps/desktop/src-tauri/Cargo.lock", "services/application/src/dataforge_application/__init__.py",
    "services/application/pyproject.toml", "workers/scraping/pyproject.toml", "workers/matching/pyproject.toml",
]


@pytest.mark.parametrize(("current", "part", "channel", "expected"), [
    ("0.2.0", "patch", "stable", "0.2.1"),
    ("0.2.9", "minor", "stable", "0.3.0"),
    ("0.9.4", "major", "stable", "1.0.0"),
    ("0.2.0", "patch", "beta", "0.2.1-beta.1"),
    ("0.2.1-beta.1", "patch", "beta", "0.2.1-beta.2"),
    ("0.2.1-beta.2", "patch", "stable", "0.2.1"),  # promote the beta
    ("0.2.1-beta.2", "minor", "stable", "0.3.0"),
    ("0.2.1-beta.2", "minor", "beta", "0.3.0-beta.1"),
])
def test_next_version(current: str, part: str, channel: str, expected: str) -> None:
    assert bump.next_version(current, part, channel) == expected


@pytest.mark.parametrize("bad", ["1.2", "01.2.3", "1.2.3-rc.1", "1.2.3-beta.0", "v1.2.3", "1.2.3.4"])
def test_rejects_versions_outside_the_release_scheme(bad: str) -> None:
    with pytest.raises(ValueError):
        bump.parse(bad)


def test_the_repository_versions_agree() -> None:
    assert bump.problems(ROOT) == []
    assert bump.current(ROOT) == __import__("dataforge_application").__version__


@pytest.fixture()
def copy(tmp_path: Path) -> Path:
    for relative in VERSION_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path


def test_bump_moves_every_file_and_only_its_version(copy: Path) -> None:
    before = {relative: (copy / relative).read_text(encoding="utf-8").splitlines() for relative in VERSION_FILES}
    current = bump.current(copy)
    assert bump.main(["--root", str(copy), "bump", "--part", "minor"]) == 0
    new = bump.next_version(current, "minor")
    assert bump.problems(copy) == [] and bump.current(copy) == new
    for relative in VERSION_FILES:
        after = (copy / relative).read_text(encoding="utf-8").splitlines()
        changed = [(old, line) for old, line in zip(before[relative], after) if old != line]
        assert len(after) == len(before[relative]), relative
        assert changed and all(current in old and new in line for old, line in changed), relative
        assert len(changed) <= 2, relative  # package-lock.json carries the root package's version twice
    assert bump.problems(copy, tag=f"v{new}") == []
    assert "does not match" in bump.problems(copy, tag="v9.9.9")[0]


def test_check_reports_drift_and_set_repairs_it(copy: Path, capsys: pytest.CaptureFixture) -> None:
    cargo = copy / "apps/desktop/src-tauri/Cargo.toml"
    cargo.write_text(cargo.read_text(encoding="utf-8").replace(f'version = "{bump.current(copy)}"', 'version = "9.9.9"', 1), encoding="utf-8")
    assert bump.main(["--root", str(copy), "check"]) == 1
    assert "Cargo.toml has 9.9.9" in capsys.readouterr().err
    assert bump.main(["--root", str(copy), "set", "v1.4.0-beta.3"]) == 0
    assert bump.problems(copy) == [] and bump.current(copy) == "1.4.0-beta.3"
    assert bump.main(["--root", str(copy), "set", "1.4"]) == 1
