-- How likely each candidate pair is the same record; it orders review and offers bulk decisions, never merges alone.
ALTER TABLE match_decisions ADD COLUMN likelihood REAL;
-- Decisions a reviewer makes together (bulk review) are undone together.
ALTER TABLE review_actions ADD COLUMN batch_id TEXT;
CREATE INDEX IF NOT EXISTS idx_review_actions_batch ON review_actions(job_id, batch_id);
