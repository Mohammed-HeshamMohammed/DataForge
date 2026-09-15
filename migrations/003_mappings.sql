-- Immutable confirmed dataset field mappings.
CREATE TABLE IF NOT EXISTS mapping_versions (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    version INTEGER NOT NULL,
    mapping_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(dataset_id, version)
);

CREATE INDEX IF NOT EXISTS idx_mapping_versions_dataset_id ON mapping_versions(dataset_id, version);
