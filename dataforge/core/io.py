"""Reading and writing the tabular formats the platform supports."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from dataforge.logging import get_logger

logger = get_logger(__name__)

READABLE_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".parquet", ".pdf"}
WRITABLE_FORMATS = {"csv", "xlsx", "json", "parquet", "pdf"}


class UnsupportedFormatError(ValueError):
    """Raised when a path's extension has no reader or writer."""


def load_table(path: str | Path, **read_kwargs: object) -> pd.DataFrame:
    """Load a tabular file into a DataFrame, dispatching on its extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in READABLE_SUFFIXES:
        raise UnsupportedFormatError(
            f"Cannot read {path.name}: expected one of {sorted(READABLE_SUFFIXES)}"
        )

    logger.debug("Loading %s", path)
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, **read_kwargs)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, **read_kwargs)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, **read_kwargs).fillna("")
    if suffix == ".json":
        return pd.read_json(path, dtype=str, **read_kwargs).fillna("")
    if suffix == ".parquet":
        return pd.read_parquet(path, **read_kwargs).fillna("")
    return _read_pdf(path)


def _read_pdf(path: Path) -> pd.DataFrame:
    """Extract and stack every table found in a PDF.

    ``tabula`` is an optional dependency because it needs a JVM; the import is
    deferred so that PDF support stays opt-in.
    """
    try:
        import tabula
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise UnsupportedFormatError(
            "Reading PDFs requires the 'pdf' extra: pip install 'dataforge[pdf]'"
        ) from exc

    tables = tabula.read_pdf(str(path), pages="all", multiple_tables=True)
    if not tables:
        raise ValueError(f"No tables found in PDF: {path}")
    return pd.concat(tables, ignore_index=True).fillna("")


def save_table(frame: pd.DataFrame, path: str | Path, fmt: str | None = None) -> Path:
    """Write a DataFrame, inferring the format from the path when not given."""
    path = Path(path)
    resolved = (fmt or path.suffix.lstrip(".")).lower()
    if resolved not in WRITABLE_FORMATS:
        raise UnsupportedFormatError(
            f"Cannot write '{resolved}': expected one of {sorted(WRITABLE_FORMATS)}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Writing %d rows to %s", len(frame), path)

    if resolved == "csv":
        frame.to_csv(path, index=False)
    elif resolved == "xlsx":
        frame.to_excel(path, index=False)
    elif resolved == "json":
        frame.to_json(path, orient="records", indent=2)
    elif resolved == "parquet":
        frame.to_parquet(path, index=False)
    else:
        _write_pdf(frame, path)
    return path


def _write_pdf(frame: pd.DataFrame, path: Path) -> None:
    """Render a DataFrame as a simple gridded PDF table."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise UnsupportedFormatError(
            "Writing PDFs requires the 'pdf' extra: pip install 'dataforge[pdf]'"
        ) from exc

    col_widths = [max(len(str(col)) * 0.15 * inch, inch) for col in frame.columns]
    doc = SimpleDocTemplate(str(path), pagesize=(sum(col_widths), letter[1]))
    rows = [frame.columns.tolist()] + frame.astype(str).values.tolist()
    table = Table(rows, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )
    doc.build([table])
