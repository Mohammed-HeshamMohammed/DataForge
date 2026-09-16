-- Scraping expansion: usage signals per run, persisted crawl frontiers, project settings (contact identity,
-- consents, capture), watches with dataset versions and diffs, field fingerprints for preset maintenance.

ALTER TABLE scrape_runs ADD COLUMN engine TEXT NOT NULL DEFAULT 'httpx';
ALTER TABLE scrape_runs ADD COLUMN purpose TEXT;
ALTER TABLE scrape_runs ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'website';
ALTER TABLE scrape_runs ADD COLUMN cached_responses INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scrape_runs ADD COLUMN discovered_urls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scrape_runs ADD COLUMN warc_path TEXT;

CREATE TABLE usage_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scrape_run_id TEXT NOT NULL REFERENCES scrape_runs(id),
    host TEXT NOT NULL,
    robots_status TEXT NOT NULL,
    crawl_delay REAL,
    tdm_reservation INTEGER,
    tdm_policy TEXT,
    content_usage_json TEXT NOT NULL DEFAULT '{}',
    ai_txt INTEGER NOT NULL DEFAULT 0,
    signals_json TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE INDEX idx_usage_signals_run ON usage_signals(scrape_run_id);
CREATE INDEX idx_usage_signals_host ON usage_signals(host, checked_at DESC);

CREATE TABLE crawl_frontier (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    url TEXT NOT NULL,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'skipped', 'failed')),
    discovered_at TEXT NOT NULL,
    visited_at TEXT,
    PRIMARY KEY (job_id, url)
);
CREATE INDEX idx_crawl_frontier_pending ON crawl_frontier(job_id, status);

CREATE TABLE project_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE watches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    params_json TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL CHECK (interval_minutes >= 15),
    status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'stopped')),
    next_run_at TEXT,
    last_job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE watch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id TEXT NOT NULL REFERENCES watches(id),
    job_id TEXT NOT NULL REFERENCES jobs(id),
    dataset_id TEXT,
    previous_dataset_id TEXT,
    diff_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_watch_runs_watch ON watch_runs(watch_id, id DESC);

CREATE TABLE preset_fingerprints (
    preset_id TEXT NOT NULL,
    preset_version TEXT NOT NULL,
    fixture TEXT NOT NULL,
    fingerprints_json TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (preset_id, preset_version, fixture)
);
