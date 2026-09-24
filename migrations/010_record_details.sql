-- Record details: the level each scrape run used and how many detail pages and detail fields it gathered,
-- so the Overview can total them without re-reading job results.

ALTER TABLE scrape_runs ADD COLUMN detail_level TEXT;
ALTER TABLE scrape_runs ADD COLUMN detail_pages INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scrape_runs ADD COLUMN detail_fields INTEGER NOT NULL DEFAULT 0;
