-- Job parameters/results, match runs, review constraints, canonical output, exports, and custom presets.
ALTER TABLE jobs ADD COLUMN params_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE jobs ADD COLUMN result_json TEXT;
ALTER TABLE jobs ADD COLUMN error TEXT;
ALTER TABLE mapping_versions ADD COLUMN entity_type TEXT;
ALTER TABLE scrape_runs ADD COLUMN run_mode TEXT NOT NULL DEFAULT 'full';
ALTER TABLE datasets ADD COLUMN kind TEXT NOT NULL DEFAULT 'import';

CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at);

CREATE TABLE match_runs (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
    dataset_id TEXT NOT NULL,
    mapping_version_id TEXT NOT NULL,
    run_mode TEXT NOT NULL CHECK (run_mode IN ('preview', 'full')),
    config_hash TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    row_ids_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE match_decisions (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    id TEXT NOT NULL,
    left_row_id TEXT NOT NULL,
    right_row_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('match', 'possible_match', 'non_match', 'rejected')),
    score REAL NOT NULL,
    reason TEXT NOT NULL,
    block_ids_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    review_version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, id)
);
CREATE INDEX idx_match_decisions_kind ON match_decisions(job_id, decision, score DESC);

CREATE TABLE clusters (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    id TEXT NOT NULL,
    member_row_ids_json TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (job_id, id)
);

CREATE TABLE canonical_records (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    cluster_id TEXT NOT NULL,
    survivor_row_id TEXT NOT NULL,
    values_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    conflicts_json TEXT NOT NULL,
    PRIMARY KEY (job_id, cluster_id)
);

CREATE TABLE review_actions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    decision_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('merge', 'keep_separate')),
    created_at TEXT NOT NULL,
    reversed_at TEXT
);

-- Scoped to a dataset so decisions never leak into unrelated datasets.
CREATE TABLE match_constraints (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    left_row_id TEXT NOT NULL,
    right_row_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('must_link', 'must_not_link')),
    review_action_id TEXT REFERENCES review_actions(id),
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX idx_match_constraints_dataset ON match_constraints(dataset_id, revoked_at);

CREATE TABLE exports (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    is_final INTEGER NOT NULL,
    include_provenance INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE custom_presets (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    parent_preset_id TEXT,
    parent_preset_version TEXT,
    preset_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
