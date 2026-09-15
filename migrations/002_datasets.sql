-- Immutable imported dataset metadata and source rows.
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    source_artifact_hash TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    column_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_rows (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    source_row_number INTEGER NOT NULL,
    raw_values_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(dataset_id, source_row_number)
);

CREATE INDEX IF NOT EXISTS idx_source_rows_dataset_id ON source_rows(dataset_id, source_row_number);
