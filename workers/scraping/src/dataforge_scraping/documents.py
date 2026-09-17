"""Documents: tables and text from PDFs (pdfplumber by default; camelot and Docling when installed as
add-ons), plus type detection for linked CSV/XLSX/JSON files handed to the dataset importer.

File types are decided by magic bytes, never by extension. Each row keeps page and table provenance.
"""

from __future__ import annotations

import io
import re

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
FILE_LINK_TYPES = ("pdf", "csv", "xlsx", "json")


def sniff_type(content: bytes) -> str | None:
    head = content[:8]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "xlsx" if b"xl/" in content[:4096] or b"[Content_Types].xml" in content[:4096] else "zip"
    text = content[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
    if text[:1] in (b"{", b"["):
        return "json"
    if text[:1] == b"<":
        return "html"
    try:
        sample = text.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = [line for line in sample.splitlines()[:5] if line.strip()]
    if len(lines) >= 2 and all(line.count(",") >= 1 or line.count("\t") >= 1 for line in lines):
        return "csv"
    return "text" if sample.isprintable() or "\n" in sample else None


def check_document(content: bytes, expected: tuple[str, ...] = FILE_LINK_TYPES) -> str:
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds the 50 MB size cap")
    kind = sniff_type(content)
    if kind not in expected:
        raise ValueError(f"Downloaded file is {kind or 'an unknown type'}, not one of {', '.join(expected)}")
    return kind


def _clean(cell: object) -> str | None:
    if cell is None:
        return None
    text = re.sub(r"\s+", " ", str(cell)).strip()
    return text or None


def _headers(row: list[object], width: int) -> list[str]:
    names, seen = [], {}
    for index in range(width):
        name = _clean(row[index]) if index < len(row) else None
        name = name or f"column_{index + 1}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return names


def pdf_tables(content: bytes, source_url: str, extraction: dict | None = None) -> tuple[list[dict], list[str]]:
    """Rows from every table. Header row = first row of each table unless `header_row: false`.
    Tables continuing across pages with an identical header are joined under one table index."""
    extraction = extraction or {}
    engine = extraction.get("engine", "pdfplumber")
    if engine == "camelot":
        return _camelot_tables(content, source_url, extraction)
    if engine == "docling":
        return _docling_tables(content, source_url)
    import pdfplumber

    strategy = extraction.get("table_strategy", "auto")  # auto | lines | text
    pages_wanted = _page_filter(extraction.get("pages"))
    rows: list[dict] = []
    warnings: list[str] = []
    table_index, last_header = 0, None
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            if pages_wanted and page_number not in pages_wanted:
                continue
            tables = []
            if strategy in ("auto", "lines"):
                tables = page.extract_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines"})
            if not tables and strategy in ("auto", "text"):
                tables = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"})
            for raw in tables:
                raw = [row for row in raw if any(_clean(cell) for cell in row)]
                if len(raw) < 2 and extraction.get("header_row", True):
                    continue
                width = max(len(row) for row in raw)
                if extraction.get("header_row", True):
                    header = _headers(raw[0], width)
                    body = raw[1:]
                    if header == last_header:
                        pass  # continuation of the previous table on a new page
                    else:
                        table_index += 1
                        last_header = header
                else:
                    header, body = [f"column_{i + 1}" for i in range(width)], raw
                    table_index += 1
                for row_number, row in enumerate(body, start=1):
                    record = {header[i]: _clean(row[i]) if i < len(row) else None for i in range(width)}
                    if not any(record.values()):
                        continue
                    record.update(source_url=source_url, source_page=page_number, source_table=table_index, source_row=row_number)
                    rows.append(record)
    if not rows:
        warnings.append("no tables found; scanned PDFs need the optional OCR or Docling add-on")
    return rows, warnings


def pdf_text(content: bytes, source_url: str = "", ocr: bool = False) -> tuple[list[dict], list[str]]:
    """One record per page with its text layer. With `ocr`, pages without text are rendered and read by
    Tesseract when the optional OCR add-on (pytesseract plus the Tesseract program) is installed."""
    import pdfplumber

    records, warnings, needs_ocr = [], [], []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                needs_ocr.append(number)
            records.append({"source_url": source_url, "source_page": number, "text": text, "text_source": "pdf" if text else None})
    if needs_ocr and ocr:
        reader = _ocr_reader()
        if reader is None:
            warnings.append("Some pages are scanned images. Install the OCR add-on (pip install pytesseract, plus the Tesseract program) to read them.")
        else:
            import pypdfium2

            document = pypdfium2.PdfDocument(content)
            for number in needs_ocr:
                image = document[number - 1].render(scale=2).to_pil()
                records[number - 1].update(text=reader(image).strip(), text_source="ocr")
    elif needs_ocr:
        warnings.append(f"{len(needs_ocr)} page(s) have no text layer (scanned); enable OCR in the preset to read them")
    return [r for r in records if r["text"]], warnings


def _ocr_reader():
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001 - missing package or missing Tesseract program
        return None
    return lambda image: pytesseract.image_to_string(image)


def _page_filter(value: object) -> set[int]:
    pages: set[int] = set()
    for part in str(value or "").split(","):
        part = part.strip()
        if "-" in part:
            start, _, end = part.partition("-")
            if start.isdigit() and end.isdigit():
                pages.update(range(int(start), int(end) + 1))
        elif part.isdigit():
            pages.add(int(part))
    return pages


def _camelot_tables(content: bytes, source_url: str, extraction: dict) -> tuple[list[dict], list[str]]:
    try:
        import camelot  # optional add-on: pulls numpy, pandas, and OpenCV
    except ImportError as error:
        raise ValueError("The camelot table engine is an optional add-on that is not installed") from error
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "document.pdf"
        path.write_bytes(content)
        tables = camelot.read_pdf(str(path), pages=str(extraction.get("pages") or "all"), flavor=extraction.get("flavor", "lattice"))
        rows = []
        for index, table in enumerate(tables, start=1):
            frame = table.df
            header = _headers(list(frame.iloc[0]), frame.shape[1])
            for row_number, (_, row) in enumerate(frame.iloc[1:].iterrows(), start=1):
                rows.append({**{header[i]: _clean(row.iloc[i]) for i in range(frame.shape[1])}, "source_url": source_url, "source_page": int(table.page), "source_table": index, "source_row": row_number})
    return rows, []


def _docling_tables(content: bytes, source_url: str) -> tuple[list[dict], list[str]]:
    try:
        from docling.datamodel.base_models import DocumentStream
        from docling.document_converter import DocumentConverter
    except ImportError as error:
        raise ValueError("The Docling engine is an optional add-on that is not installed") from error
    result = DocumentConverter().convert(DocumentStream(name="document.pdf", stream=io.BytesIO(content)))
    rows = []
    for index, table in enumerate(result.document.tables, start=1):
        frame = table.export_to_dataframe()
        page = table.prov[0].page_no if table.prov else None
        for row_number, (_, row) in enumerate(frame.iterrows(), start=1):
            rows.append({**{str(k): _clean(v) for k, v in row.items()}, "source_url": source_url, "source_page": page, "source_table": index, "source_row": row_number})
    return rows, []
