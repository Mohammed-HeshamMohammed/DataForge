-- A cleaned copy remembers the dataset it came from and what was changed; the original stays untouched.
ALTER TABLE datasets ADD COLUMN parent_dataset_id TEXT REFERENCES datasets(id) ON DELETE SET NULL;
ALTER TABLE datasets ADD COLUMN derivation_json TEXT;
