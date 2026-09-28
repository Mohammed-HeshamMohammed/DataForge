-- Reusable, row-driven browser workflows. Secrets and row values are never stored here.
CREATE TABLE IF NOT EXISTS automation_workflows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    start_url TEXT NOT NULL,
    allowed_host TEXT NOT NULL,
    dataset_id TEXT REFERENCES datasets(id) ON DELETE SET NULL,
    steps_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_automation_workflows_project
    ON automation_workflows(project_id, updated_at DESC);
