"""Fixtures for tests that run against the real exports in ``Data/``.

These are the vendor files the matching engine was built for. They exercise
things a synthetic fixture cannot: real column naming from two different
vendors, real address formatting, and enough volume to catch a performance
regression.

The whole module skips when ``Data/`` is absent, so a checkout without it (or
one where the exports have been moved outside the repository) still runs the
rest of the suite.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "Data"

#: A property export: address and owner columns, no contact details.
PROPERTY_EXPORT = "ca 1.csv"

#: A skip-traced export: carries Phone / Alt. Phone / Email.
SKIPTRACED_EXPORT = (
    "2024-11-06_0am_Results_for_infoakcallers_com-Tucson2"
    "_670044fce345b_670ec146b146c_6711533c027ed_6712d3cc25923"
    "_67165abf2ced5_671bdbbd35b58_6722655958fe7.csv"
)

#: The largest export, used for the performance guard.
LARGE_EXPORT = "SAHS Texas 40k.csv"

#: Rows read for the correctness tests. Enough to be representative, small
#: enough that the suite stays quick.
SAMPLE_ROWS = 3000


@lru_cache(maxsize=8)
def _read(name: str, nrows: int | None) -> pd.DataFrame:
    """Read an export once per session and cache it."""
    path = DATA_DIR / name
    if not path.exists():
        pytest.skip(f"{path.name} is not present in Data/")
    return pd.read_csv(path, dtype=str, keep_default_na=False, nrows=nrows)


def load_export(name: str, nrows: int | None = None) -> pd.DataFrame:
    """Return a copy of a cached export, so a test cannot mutate the cache."""
    return _read(name, nrows).copy()


@pytest.fixture(scope="session", autouse=True)
def require_data_dir() -> None:
    if not DATA_DIR.is_dir():
        pytest.skip("Data/ is not present in this checkout", allow_module_level=True)


@pytest.fixture
def property_export() -> pd.DataFrame:
    """A slice of a real property export."""
    return load_export(PROPERTY_EXPORT, SAMPLE_ROWS)


@pytest.fixture
def skiptraced_export() -> pd.DataFrame:
    """A slice of a real skip-traced export, which has phone and email columns."""
    return load_export(SKIPTRACED_EXPORT, SAMPLE_ROWS)
