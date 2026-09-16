-- Cross-dataset comparison scope, reviewer-chosen canonical values, bad-mapping flags, export exclusions.
ALTER TABLE match_runs ADD COLUMN compare_dataset_id TEXT;
ALTER TABLE mapping_versions ADD COLUMN export_exclude_json TEXT NOT NULL DEFAULT '[]';

-- A reviewer's choice of which member row supplies a canonical field. Applies to whichever group
-- contains the chosen row, so it survives re-clustering and future runs of the same dataset.
CREATE TABLE canonical_overrides (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    column_name TEXT NOT NULL,
    row_id TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    review_action_id TEXT REFERENCES review_actions(id),
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX idx_canonical_overrides_dataset ON canonical_overrides(dataset_id, revoked_at);

CREATE TABLE mapping_flags (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    mapping_version_id TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    decision_id TEXT,
    column_name TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_mapping_flags_dataset ON mapping_flags(dataset_id, resolved_at);
