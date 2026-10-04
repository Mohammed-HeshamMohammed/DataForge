from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rapidfuzz import fuzz

from .storage import ProjectStore

FUZZY_HEADER_THRESHOLD = 92
MAX_IMPORT_BYTES = 50 * 1024 * 1024
MAX_IMPORT_ROWS = 1_000_000
TABULAR_SUFFIXES = (".csv", ".json", ".jsonl", ".ndjson", ".xlsx", ".xml", ".parquet", ".docx")


@dataclass(frozen=True)
class DatasetImport:
    dataset_id: str
    source_artifact_hash: str
    row_count: int
    column_count: int


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    null_count: int
    distinct_count: int
    sample_values: tuple[str, ...]

    @property
    def null_rate(self) -> float:
        return self.null_count / self._row_count if self._row_count else 0.0

    _row_count: int = 0


@dataclass(frozen=True)
class FieldMappingProposal:
    source_field: str
    role: str | None
    confidence: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class MappingVersion:
    mapping_id: str
    dataset_id: str
    version: int
    mapping: dict[str, str]


def _register_rows(
    store: ProjectStore,
    project_id: str,
    source_path: Path,
    source_bytes: bytes,
    headers: list[str],
    rows: list[dict[str, object]],
    name: str | None,
    source_row_start: int = 2,
    kind: str = "import",
    row_numbers: list[int] | None = None,
) -> DatasetImport:
    numbers = row_numbers if row_numbers is not None else range(source_row_start, source_row_start + len(rows))
    timestamp = datetime.now(timezone.utc).isoformat()
    dataset_id = str(uuid4())
    dataset_name = name or source_path.stem
    _store_artifact(store, source_bytes, source_path.suffix.lower())
    store._connection.execute(
        "INSERT INTO datasets(id, project_id, name, source_artifact_hash, source_filename, row_count, column_count, created_at, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (dataset_id, project_id, dataset_name, hashlib.sha256(source_bytes).hexdigest(), source_path.name, len(rows), len(headers), timestamp, kind),
    )
    store._connection.executemany(
        "INSERT INTO source_rows(id, dataset_id, source_row_number, raw_values_json, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            (str(uuid4()), dataset_id, row_number, json.dumps(row, ensure_ascii=False, separators=(",", ":")), timestamp)
            for row_number, row in zip(numbers, rows)
        ],
    )
    store._connection.commit()
    return DatasetImport(dataset_id, hashlib.sha256(source_bytes).hexdigest(), len(rows), len(headers))


def import_csv(store: ProjectStore, project_id: str, source_path: Path, name: str | None = None) -> DatasetImport:
    source_bytes = source_path.read_bytes()

    with source_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.reader(source_file)
        raw_headers = next(reader, None)
        if not raw_headers:
            raise ValueError("CSV must contain a header row")
        headers = unique_headers(raw_headers)
        rows, row_numbers = [], []
        for row_number, values in enumerate(reader, start=2):
            if not values:
                continue  # blank line; later rows keep their original numbers
            if len(values) > len(headers):
                raise ValueError(f"CSV row {row_number} has more values than the header has columns")
            rows.append(dict(zip(headers, values + [""] * (len(headers) - len(values)))))
            row_numbers.append(row_number)
    return _register_rows(store, project_id, source_path, source_bytes, headers, rows, name, row_numbers=row_numbers)


def unique_headers(raw_headers: list[object]) -> list[str]:
    """Blank headers become column_<n>; repeated headers get a (2), (3) suffix so no column's values are dropped."""
    headers: list[str] = []
    for index, raw in enumerate(raw_headers, start=1):
        header = str(raw).strip() if raw is not None and str(raw).strip() else f"column_{index}"
        candidate, suffix = header, 2
        while candidate in headers:
            candidate, suffix = f"{header} ({suffix})", suffix + 1
        headers.append(candidate)
    return headers


def import_json(store: ProjectStore, project_id: str, source_path: Path, name: str | None = None) -> DatasetImport:
    source_bytes = source_path.read_bytes()
    document = json.loads(source_bytes.decode("utf-8-sig"))
    rows = [document] if isinstance(document, dict) else document
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("JSON import must contain an object or an array of objects")
    headers = list(dict.fromkeys(key for row in rows for key in row))
    if not headers:
        raise ValueError("JSON import must contain at least one field")
    normalized_rows = [{header: row.get(header, "") for header in headers} for row in rows]
    return _register_rows(store, project_id, source_path, source_bytes, headers, normalized_rows, name)


def _normalized_rows(rows: list[dict[str, object]], kind: str) -> tuple[list[str], list[dict[str, object]]]:
    if not rows:
        raise ValueError(f"{kind} import contains no records")
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValueError(f"{kind} import exceeds the {MAX_IMPORT_ROWS:,} row safety limit")
    headers = list(dict.fromkeys(str(key) for row in rows for key in row))
    if not headers:
        raise ValueError(f"{kind} import contains no fields")
    return headers, [{header: _safe_value(row.get(header, "")) for header in headers} for row in rows]


def _safe_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _flat_xml(element: ET.Element, prefix: str = "", depth: int = 0) -> dict[str, object]:
    row: dict[str, object] = {}
    for key, value in element.attrib.items():
        row[f"{prefix}@{_local_xml_name(key)}"] = value
    for child in list(element):
        key = f"{prefix}{_local_xml_name(child.tag)}"
        if list(child) and depth < 3:
            row.update(_flat_xml(child, key + ".", depth + 1))
        else:
            text = " ".join("".join(child.itertext()).split())
            if text:
                row[key] = f"{row[key]}, {text}" if key in row else text
        if len(row) >= 200:
            break
    return row


def _xml_rows(content: bytes) -> list[dict[str, object]]:
    lowered = content[:100_000].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("XML with DTD or entity declarations is not supported")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"Invalid XML file: {error}") from error
    counts: dict[str, int] = {}
    for parent in root.iter():
        for child in list(parent):
            name = _local_xml_name(child.tag)
            counts[name] = counts.get(name, 0) + 1
    repeated = [item for item in counts.items() if item[1] > 1]
    tag = max(repeated, key=lambda item: item[1])[0] if repeated else _local_xml_name(root.tag)
    return [_flat_xml(node) for node in root.iter() if _local_xml_name(node.tag) == tag]


def _docx_rows(content: bytes) -> list[dict[str, object]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        try:
            document = archive.read("word/document.xml")
        except KeyError as error:
            raise ValueError("DOCX file has no Word document body") from error
    if b"<!DOCTYPE" in document.upper() or b"<!ENTITY" in document.upper():
        raise ValueError("DOCX XML with DTD or entity declarations is not supported")
    root = ET.fromstring(document)
    tables = [node for node in root.iter() if _local_xml_name(node.tag) == "tbl"]
    rows: list[dict[str, object]] = []
    for table_index, table in enumerate(tables, start=1):
        matrix = []
        for row in (node for node in table if _local_xml_name(node.tag) == "tr"):
            matrix.append([" ".join((text.text or "").strip() for text in cell.iter() if _local_xml_name(text.tag) == "t").strip() for cell in row if _local_xml_name(cell.tag) == "tc"])
        if not matrix:
            continue
        headers = unique_headers(matrix[0])
        for row_number, values in enumerate(matrix[1:], start=2):
            rows.append({**dict(zip(headers, values + [""] * (len(headers) - len(values)))), "source_table": table_index, "source_row": row_number})
    if rows:
        return rows
    paragraphs = [" ".join((text.text or "").strip() for text in node.iter() if _local_xml_name(text.tag) == "t").strip() for node in root.iter() if _local_xml_name(node.tag) == "p"]
    return [{"paragraph": text, "source_paragraph": index} for index, text in enumerate(paragraphs, start=1) if text]


def _rows_from_bytes(filename: str, content: bytes) -> tuple[list[str], list[dict[str, object]]]:
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError("Imported data exceeds the 50 MB safety limit")
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        return _normalized_rows([dict(row) for row in reader], "CSV")
    if suffix == ".json":
        document = json.loads(content.decode("utf-8-sig"))
        rows = [document] if isinstance(document, dict) else document
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("JSON import must contain an object or an array of objects")
        return _normalized_rows(rows, "JSON")
    if suffix in (".jsonl", ".ndjson"):
        rows = []
        for number, line in enumerate(content.decode("utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSON Lines row {number} must be an object")
            rows.append(value)
        return _normalized_rows(rows, "JSON Lines")
    if suffix == ".xml":
        return _normalized_rows(_xml_rows(content), "XML")
    if suffix == ".docx":
        return _normalized_rows(_docx_rows(content), "DOCX")
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            values = workbook.active.iter_rows(values_only=True)
            raw_headers = next(values, None)
            if raw_headers is None:
                raise ValueError("XLSX sheet must contain a header row")
            headers = unique_headers(list(raw_headers))
            rows = [{header: _safe_value(value) for header, value in zip(headers, row)} for row in values if any(value is not None for value in row)]
            return headers, rows
        finally:
            workbook.close()
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as parquet
        except ImportError as error:
            raise RuntimeError("Parquet import requires the bundled pyarrow package") from error
        file = parquet.ParquetFile(io.BytesIO(content))
        if file.metadata.num_rows > MAX_IMPORT_ROWS:
            raise ValueError(f"Parquet import exceeds the {MAX_IMPORT_ROWS:,} row safety limit")
        return _normalized_rows(file.read().to_pylist(), "Parquet")
    raise ValueError(f"Unsupported data file inside archive: {suffix or 'no extension'}")


def import_tabular(store: ProjectStore, project_id: str, source_path: Path, name: str | None = None) -> DatasetImport:
    source_bytes = source_path.read_bytes()
    headers, rows = _rows_from_bytes(source_path.name, source_bytes)
    return _register_rows(store, project_id, source_path, source_bytes, headers, rows, name, source_row_start=1)


def import_archive(store: ProjectStore, project_id: str, source_path: Path, name: str | None = None) -> DatasetImport:
    source_bytes = source_path.read_bytes()
    if len(source_bytes) > MAX_IMPORT_BYTES:
        raise ValueError("Archive exceeds the 50 MB safety limit")
    if source_path.suffix.lower() == ".gz":
        inner_name = source_path.stem
        with gzip.GzipFile(fileobj=io.BytesIO(source_bytes)) as archive:
            content = archive.read(MAX_IMPORT_BYTES + 1)
        if len(content) > MAX_IMPORT_BYTES:
            raise ValueError("Expanded GZIP data exceeds the 50 MB safety limit")
    else:
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir() and Path(entry.filename).suffix.lower() in TABULAR_SUFFIXES]
            if len(entries) != 1:
                raise ValueError("ZIP import must contain exactly one supported data file")
            entry = entries[0]
            if Path(entry.filename).is_absolute() or ".." in Path(entry.filename).parts:
                raise ValueError("ZIP contains an unsafe file path")
            if entry.file_size > MAX_IMPORT_BYTES or (entry.compress_size and entry.file_size / entry.compress_size > 1000):
                raise ValueError("Expanded ZIP data exceeds the archive safety limits")
            inner_name, content = entry.filename, archive.read(entry)
    headers, rows = _rows_from_bytes(inner_name, content)
    return _register_rows(store, project_id, source_path, source_bytes, headers, rows, name, source_row_start=1)


def register_staged_rows(
    store: ProjectStore,
    project_id: str,
    records: list[dict[str, object]],
    source_name: str,
    name: str | None = None,
) -> DatasetImport:
    if not records:
        raise ValueError("Cannot register an empty staged dataset")
    headers = list(dict.fromkeys(key for record in records for key in record))
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    source_path = Path(source_name)
    return _register_rows(store, project_id, source_path, payload, headers, records, name, source_row_start=1, kind="scrape")


def import_file(store: ProjectStore, project_id: str, source_path: Path, name: str | None = None) -> DatasetImport:
    importers = {
        ".csv": import_csv, ".json": import_json, ".xlsx": import_xlsx,
        ".jsonl": import_tabular, ".ndjson": import_tabular, ".xml": import_tabular,
        ".parquet": import_tabular, ".docx": import_tabular, ".zip": import_archive, ".gz": import_archive,
    }
    importer = importers.get(source_path.suffix.lower())
    if importer is None:
        raise ValueError("Supported imports are CSV, JSON, JSONL, XLSX, XML, Parquet, DOCX, ZIP, and GZIP")
    if not source_path.is_file():
        raise ValueError("Import path does not point to a readable file")
    return importer(store, project_id, source_path, name)


def list_datasets(store: ProjectStore, project_id: str) -> list[dict[str, object]]:
    rows = store._connection.execute(
        """SELECT d.*, (SELECT MAX(version) FROM mapping_versions m WHERE m.dataset_id = d.id) AS mapping_version
           FROM datasets d WHERE project_id = ? ORDER BY created_at DESC""",
        (project_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def dataset_rows(store: ProjectStore, dataset_id: str, offset: int = 0, limit: int = 50) -> list[dict[str, object]]:
    rows = store._connection.execute(
        "SELECT id, source_row_number, raw_values_json FROM source_rows WHERE dataset_id = ? ORDER BY source_row_number LIMIT ? OFFSET ?",
        (dataset_id, limit, offset),
    ).fetchall()
    return [{"id": r["id"], "row_number": r["source_row_number"], "raw": json.loads(r["raw_values_json"])} for r in rows]


def latest_mapping(store: ProjectStore, dataset_id: str) -> sqlite3.Row | None:
    return store._connection.execute(
        "SELECT * FROM mapping_versions WHERE dataset_id = ? ORDER BY version DESC LIMIT 1", (dataset_id,)
    ).fetchone()


def delete_dataset(store: ProjectStore, dataset_id: str) -> None:
    """User-requested deletion of imported rows and mappings. Job history and exports stay until deleted separately."""
    with store._connection:
        store._connection.execute("DELETE FROM source_rows WHERE dataset_id = ?", (dataset_id,))
        store._connection.execute("DELETE FROM mapping_versions WHERE dataset_id = ?", (dataset_id,))
        store._connection.execute("DELETE FROM match_constraints WHERE dataset_id = ?", (dataset_id,))
        store._connection.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))


def import_xlsx(store: ProjectStore, project_id: str, source_path: Path, name: str | None = None) -> DatasetImport:
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise RuntimeError("XLSX import requires the openpyxl package") from error

    source_bytes = source_path.read_bytes()
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        raw_headers = next(rows, None)
        if raw_headers is None:
            raise ValueError("XLSX sheet must contain a header row")
        headers = unique_headers(list(raw_headers))
        normalized_rows, row_numbers = [], []
        for row_number, row in enumerate(rows, start=2):
            if all(value is None for value in row):
                continue
            normalized_rows.append({header: _json_cell_value(value) for header, value in zip(headers, row)})
            row_numbers.append(row_number)
    finally:
        workbook.close()
    return _register_rows(store, project_id, source_path, source_bytes, headers, normalized_rows, name, row_numbers=row_numbers)


def _json_cell_value(value: object) -> object:
    return value.isoformat() if hasattr(value, "isoformat") else ("" if value is None else value)


_ROLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "name": (
        "name", "full name", "person name", "contact name", "lead name", "owner name", "agent name",
        "company name", "business name", "organization name", "property name", "venue name",
        "product name", "item name", "job title", "position title", "article title", "publication title",
        "movie title", "video title", "repository name", "repo name", "package name", "project name",
    ),
    "first_name": ("first name", "firstname", "given name", "contact first name", "owner first name", "lead first name"),
    "last_name": ("last name", "lastname", "surname", "family name", "contact last name", "owner last name", "lead last name"),
    "email": ("email", "email address", "e mail", "work email", "business email", "contact email", "owner email", "agent email"),
    "phone": ("phone", "phone number", "mobile", "mobile phone", "alt phone", "telephone", "cell", "contact phone", "owner phone", "agent phone"),
    "address": ("address", "street address", "property address", "site address", "physical address", "office address", "business address", "venue address", "job address"),
    "mailing_address": ("mailing address", "postal address", "owner mailing address", "correspondence address"),
    "building_name": ("building", "building name", "property building", "development name", "complex name"),
    "house_number": ("house number", "street number", "building number", "premise number", "addr housenumber"),
    "street": ("street", "street name", "road", "road name", "thoroughfare", "address line 1"),
    "unit": ("unit", "unit number", "apartment", "apartment number", "apt", "suite", "suite number", "flat", "flat number", "address line 2"),
    "po_box": ("po box", "p o box", "post office box"),
    "neighborhood": ("neighborhood", "neighbourhood", "suburb", "subdivision", "local area"),
    "district": ("district", "borough", "ward", "administrative district"),
    "city": ("city", "town", "locality", "municipality"),
    "county": ("county", "county name", "parish", "prefecture"),
    "region": ("state", "state name", "region", "province", "territory", "governorate"),
    "country": ("country", "country name", "nation"),
    "country_code": ("country code", "iso country code", "country iso", "iso2", "iso3"),
    "postal_code": ("zip", "zip code", "zipcode", "postal", "postal code", "postcode"),
    "latitude": ("latitude", "lat", "y coordinate"),
    "longitude": ("longitude", "lon", "lng", "long", "x coordinate"),
    "identifier": (
        "id", "identifier", "record id", "source id", "external id", "lead id", "contact id", "customer id",
        "company id", "account id", "place id", "property id", "parcel id", "parcel number", "apn", "mls id",
        "listing id", "job id", "vacancy id", "application id", "sku", "upc", "ean", "gtin", "mpn", "asin",
        "doi", "isbn", "issn", "imdb id", "video id", "repository id", "repo id", "package id", "release id",
    ),
    "url": (
        "url", "source url", "profile url", "listing url", "property url", "product url", "job url", "article url",
        "video url", "website", "company website", "business website", "linkedin url", "github url", "repository url",
        "repo url", "package url", "canonical url",
    ),
}
# Columns the scrapers add to every record. They describe how a row was collected, so values such as
# source_url repeat across every row from one page and must never be proposed as match evidence.
SCRAPE_PROVENANCE_COLUMNS = frozenset({
    "source_url", "source_retrieved_at", "preset_id", "preset_version", "strategy_used", "extraction_mode",
    "structured_syntax",
})
# Roles that may be mapped to several columns (set comparison or column-namespaced identifiers).
MULTI_COLUMN_ROLES = frozenset({"phone", "email", "identifier", "other", "ignore"})
SENSITIVE_ROLES = frozenset({
    "phone", "email", "address", "mailing_address", "building_name", "house_number", "street", "unit", "po_box",
    "neighborhood", "district", "city", "county", "region", "country", "country_code", "postal_code", "latitude",
    "longitude", "name", "first_name", "last_name",
})


def _store_artifact(store: ProjectStore, source_bytes: bytes, suffix: str) -> str:
    """Content-addressed, write-once copy of the source so the import stays reproducible."""
    digest = hashlib.sha256(source_bytes).hexdigest()
    target = store.project_root / "artifacts" / "sha256" / digest[:2] / f"{digest}{suffix}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(source_bytes)
        temporary.replace(target)
    return digest


def propose_field_mappings(headers: list[str]) -> tuple[FieldMappingProposal, ...]:
    proposals: list[FieldMappingProposal] = []
    for header in headers:
        if header in SCRAPE_PROVENANCE_COLUMNS:
            proposals.append(FieldMappingProposal(header, "ignore", "proposed", ("ignore",)))
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", header.casefold()).strip()
        exact_candidates = tuple(
            role for role, patterns in _ROLE_PATTERNS.items() if normalized in patterns
        )
        candidates = exact_candidates or tuple(
            role for role, patterns in _ROLE_PATTERNS.items()
            if any(re.search(rf"\b{re.escape(pattern)}\b", normalized) for pattern in patterns)
        )
        if not candidates:
            # Strict fuzzy pass for typos ("Phnoe Number"); still only a suggestion that must be confirmed.
            candidates = tuple(
                role for role, patterns in _ROLE_PATTERNS.items()
                if any(len(p) >= 4 and fuzz.ratio(normalized, p) >= FUZZY_HEADER_THRESHOLD for p in patterns)
            )
        # Only a whole-header match is proposed; partial matches ("Owner Mailing Zip") need a human decision.
        if len(exact_candidates) == 1:
            confidence = "proposed"
        else:
            confidence = "ambiguous" if candidates else "unmapped"
        proposals.append(FieldMappingProposal(header, exact_candidates[0] if confidence == "proposed" else None, confidence, candidates))
    return tuple(proposals)


def confirm_mapping(store: ProjectStore, dataset_id: str, mapping: dict[str, str], entity_type: str | None = None, export_exclude: list[str] | None = None) -> MappingVersion:
    rows = store._connection.execute(
        "SELECT raw_values_json FROM source_rows WHERE dataset_id = ? LIMIT 1", (dataset_id,)
    ).fetchone()
    if rows is None:
        raise ValueError("Cannot map an empty or unknown dataset")
    source_fields = set(json.loads(rows["raw_values_json"]))
    unknown_fields = set(mapping) - source_fields
    if unknown_fields:
        raise ValueError(f"Mapping contains unknown source fields: {sorted(unknown_fields)}")
    roles = [role for role in mapping.values() if role not in MULTI_COLUMN_ROLES]
    if any(not role.strip() for role in mapping.values()) or len(roles) != len(set(roles)):
        raise ValueError("Mapping roles must be non-empty, and positional roles may be used by only one column")
    current = store._connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM mapping_versions WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()[0]
    version = current + 1
    mapping_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    excluded = sorted(set(export_exclude or []))
    if set(excluded) - source_fields:
        raise ValueError(f"Export exclusions contain unknown source fields: {sorted(set(excluded) - source_fields)}")
    store._connection.execute(
        "INSERT INTO mapping_versions(id, dataset_id, version, mapping_json, created_at, entity_type, export_exclude_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mapping_id, dataset_id, version, json.dumps(mapping, sort_keys=True, separators=(",", ":")), timestamp, entity_type, json.dumps(excluded)),
    )
    # A new mapping version answers any open "bad mapping" reports for this dataset.
    store._connection.execute("UPDATE mapping_flags SET resolved_at = ? WHERE dataset_id = ? AND resolved_at IS NULL", (timestamp, dataset_id))
    store._connection.commit()
    return MappingVersion(mapping_id, dataset_id, version, dict(mapping))


def profile_dataset(store: ProjectStore, dataset_id: str, sample_limit: int = 5) -> tuple[ColumnProfile, ...]:
    rows = store._connection.execute(
        "SELECT raw_values_json FROM source_rows WHERE dataset_id = ? ORDER BY source_row_number",
        (dataset_id,),
    ).fetchall()
    values = [json.loads(row["raw_values_json"]) for row in rows]
    headers = list(values[0]) if values else []
    profiles: list[ColumnProfile] = []
    for header in headers:
        column_values = [row.get(header, "") or "" for row in values]
        non_empty = [value for value in column_values if value != ""]
        profiles.append(
            ColumnProfile(
                name=header,
                null_count=len(column_values) - len(non_empty),
                distinct_count=len(set(non_empty)),
                sample_values=tuple(list(dict.fromkeys(non_empty))[:sample_limit]),
                _row_count=len(column_values),
            )
        )
    return tuple(profiles)
