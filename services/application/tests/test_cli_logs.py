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
