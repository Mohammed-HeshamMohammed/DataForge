import pytest


@pytest.fixture(autouse=True)
def _no_log_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never write log files into the real app-data folder unless a test opts in."""
    monkeypatch.setenv("DATAFORGE_LOG_FILE", "0")
