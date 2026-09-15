-- Durable scrape run metadata, errors, and result summaries.
CREATE TABLE IF NOT EXISTS scrape_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    preset_id TEXT NOT NULL,
    preset_version TEXT NOT NULL,
    strategy_used TEXT NOT NULL,
    start_url TEXT NOT NULL,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    records_extracted INTEGER NOT NULL DEFAULT 0,
    records_rejected INTEGER NOT NULL DEFAULT 0,
    records_duplicate INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS scrape_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scrape_run_id TEXT NOT NULL REFERENCES scrape_runs(id),
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_job_id ON scrape_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_scrape_errors_run_id ON scrape_errors(scrape_run_id, id);
