from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rapidfuzz import fuzz

from .storage import ProjectStore

FUZZY_HEADER_THRESHOLD = 92


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
    importers = {".csv": import_csv, ".json": import_json, ".xlsx": import_xlsx}
    importer = importers.get(source_path.suffix.lower())
    if importer is None:
        raise ValueError("Supported import formats are CSV, JSON, and XLSX")
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
    "name": ("name", "full name", "company name", "business name"),
    "first_name": ("first name", "firstname", "given name"),
    "last_name": ("last name", "lastname", "surname", "family name"),
    "email": ("email", "email address", "e mail"),
    "phone": ("phone", "phone number", "mobile", "alt phone", "telephone", "cell"),
    "address": ("address", "street address", "property address"),
    "mailing_address": ("mailing address",),
    "city": ("city",),
    "region": ("state", "region", "province"),
    "postal_code": ("zip", "zip code", "postal", "postal code"),
    "identifier": ("id", "identifier", "record id", "property id", "apn", "listing id", "sku", "asin"),
    "url": ("url", "source url", "listing url", "product url", "website"),
}
# Roles that may be mapped to several columns (set comparison or column-namespaced identifiers).
MULTI_COLUMN_ROLES = frozenset({"phone", "email", "identifier", "other", "ignore"})
SENSITIVE_ROLES = frozenset({"phone", "email", "address", "mailing_address", "name", "first_name", "last_name"})


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
