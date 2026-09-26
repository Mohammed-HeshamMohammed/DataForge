from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from dataforge_application import cli, logs


def test_cli_creates_project_imports_and_waits_for_the_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    source = tmp_path / "rows.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows([["Name", "Phone"], ["Ada", "5125550182"]])
    project = str(tmp_path / "project")

    assert cli.main(["--create", "--project", project, "--name", "CLI", "health.check"]) == 0
    capsys.readouterr()
    assert cli.main(["--project", project, "--wait", "dataset.import", json.dumps({"path": str(source)})]) == 0
    job = json.loads(capsys.readouterr().out)
    assert job["result"]["state"] == "completed" and job["result"]["result"]["row_count"] == 1

    assert cli.main(["--project", project, "dataset.import", json.dumps({"path": "relative.csv"})]) == 1
    assert "absolute path" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert cli.main(["--project", str(tmp_path / "missing"), "dataset.list"]) == 2


def test_logs_are_redacted_and_written_to_a_rotating_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("DATAFORGE_LOG_FILE", "1")
    monkeypatch.setattr(logs, "_file_logger", None)
    logs.log("error", "fixture.event", email="ada@example.test", url="https://x.test/a?token=abc", api_key="k", phone="512-555-0182")
    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["email"] == "[email]" and line["url"] == "https://x.test/a?[redacted]" and line["api_key"] == "[redacted]" and line["phone"] == "[phone]"
    for handler in logs._file_logger.handlers:
        handler.flush()
    written = (tmp_path / "appdata" / "DataForge" / "logs" / "service.log").read_text(encoding="utf-8")
    assert "fixture.event" in written and "ada@example.test" not in written
    for handler in list(logs._file_logger.handlers):
        handler.close()
        logs._file_logger.removeHandler(handler)
    monkeypatch.setattr(logs, "_file_logger", None)


def test_stdio_service_speaks_utf8_even_when_the_pipe_defaults_to_cp1252(tmp_path: Path) -> None:
    """Regression: the packaged service crashed on Windows because redirected pipes use the ANSI code page."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[3]
    sources = os.pathsep.join(str(root / rel / "src") for rel in ("services/application", "workers/matching", "workers/scraping"))
    env = {**os.environ, "PYTHONPATH": sources, "DATAFORGE_APP_DATA": str(tmp_path / "appdata"), "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    requests = [
        {"id": 1, "schema_version": 1, "command": "project.create", "payload": {"path": str(tmp_path / "project"), "name": "Encoding"}},
        {"id": 2, "schema_version": 1, "command": "preset.list", "payload": {}},
    ]
    result = subprocess.run(
        [sys.executable, "-c", "from dataforge_application.server import main; main()"],  # what packaging/service_entry.py does
        input="".join(json.dumps(request) + "\n" for request in requests).encode("utf-8"),
        capture_output=True, env=env, timeout=120, check=False,
    )
    lines = result.stdout.decode("utf-8").splitlines()
    assert lines, result.stderr.decode("utf-8", "replace")[-1500:]
    assert len(lines) == 2, result.stderr.decode("utf-8", "replace")[-1500:]
    assert json.loads(lines[0])["ok"] is True
    presets = json.loads(lines[1])
    assert presets["ok"] is True, presets
    assert any(ord(char) > 255 for char in lines[1]), "fixture no longer contains non-cp1252 text; pick another command"
