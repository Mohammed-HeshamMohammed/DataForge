"""Table reading and writing."""

from __future__ import annotations

import pandas as pd
import pytest

from dataforge.core.io import UnsupportedFormatError, load_table, save_table


def test_csv_round_trip(tmp_path, leads_frame):
    path = save_table(leads_frame, tmp_path / "out.csv")
    assert path.exists()
    assert len(load_table(path)) == len(leads_frame)


def test_json_round_trip(tmp_path):
    frame = pd.DataFrame([{"a": "1", "b": "2"}])
    path = save_table(frame, tmp_path / "out.json")
    assert load_table(path).iloc[0]["a"] == "1"


def test_explicit_format_overrides_the_suffix(tmp_path):
    frame = pd.DataFrame([{"a": "1"}])
    path = save_table(frame, tmp_path / "data.txt", fmt="csv")
    assert path.read_text().startswith("a")


def test_unknown_read_suffix_is_rejected(tmp_path):
    target = tmp_path / "notes.docx"
    target.write_text("x")
    with pytest.raises(UnsupportedFormatError):
        load_table(target)


def test_unknown_write_format_is_rejected(tmp_path):
    with pytest.raises(UnsupportedFormatError):
        save_table(pd.DataFrame([{"a": 1}]), tmp_path / "out.docx")
