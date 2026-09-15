-- Signed preset packages (install history enables rollback) and fixture health checks.
CREATE TABLE preset_packages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    key_id TEXT NOT NULL,
    package_json TEXT NOT NULL,
    installed_at TEXT NOT NULL,
    removed_at TEXT,
    UNIQUE (name, version)
);

CREATE TABLE preset_health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preset_id TEXT NOT NULL,
    preset_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
    result_json TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE INDEX idx_preset_health ON preset_health_checks(preset_id, preset_version, id DESC);
