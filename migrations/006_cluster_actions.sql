-- Reviewer cluster actions (split, lock) scoped to a dataset so they apply to future runs too.
CREATE TABLE cluster_actions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    dataset_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('split', 'lock')),
    member_row_ids_json TEXT NOT NULL,
    separated_row_ids_json TEXT,
    created_at TEXT NOT NULL,
    reversed_at TEXT
);
CREATE INDEX idx_cluster_actions_dataset ON cluster_actions(dataset_id, action, reversed_at);

ALTER TABLE match_constraints ADD COLUMN cluster_action_id TEXT REFERENCES cluster_actions(id);

-- Review-queue ranking models trained only from human review decisions.
CREATE TABLE ranking_models (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    training_hash TEXT NOT NULL,
    label_count INTEGER NOT NULL,
    weights_json TEXT NOT NULL,
    evaluation_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
