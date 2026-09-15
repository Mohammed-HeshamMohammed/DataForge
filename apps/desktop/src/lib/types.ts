export type JobState = "draft" | "validating" | "queued" | "running" | "paused" | "completed" | "failed" | "cancelled";

export type JobEvent = {
  id: number;
  event_type: string;
  occurred_at: string;
  payload: Record<string, unknown>;
};

export type Job = {
  id: string;
  kind: string;
  state: JobState;
  created_at: string;
  updated_at: string;
  params: Record<string, unknown>;
  result: Record<string, any> | null;
  error: string | null;
  events?: JobEvent[];
};

export type Project = { id: string; name: string; root_path: string; created_at: string; schema_version?: number };

export type Dataset = {
  id: string;
  name: string;
  kind: "import" | "scrape";
  source_filename: string;
  source_artifact_hash: string;
  row_count: number;
  column_count: number;
  created_at: string;
  mapping_version: number | null;
};

export type ColumnProfile = {
  name: string;
  null_rate: number;
  distinct_count: number;
  sample_values: string[];
  proposed_role: string | null;
  confidence: "proposed" | "ambiguous" | "unmapped";
  candidates: string[];
};

export type Row = { id: string; row_number: number; raw: Record<string, unknown> };

export type Evidence = { field: string; similarity: number; result: string; strength: "strong" | "supporting" | "none" | "guard"; explanation: string };

export type ReviewItem = {
  decision_id: string;
  score: number;
  reason: string;
  review_version: number;
  evidence: Evidence[];
  left: Row;
  right: Row;
  can_merge: boolean;
};

export type MatchResults = {
  job_id: string;
  dataset_id: string;
  run_mode: "preview" | "full";
  policy_version: string;
  mapping_version_id: string;
  metrics: Record<string, any>;
  decisions: Record<string, number>;
  pending_review: number;
  reviewed: Record<string, number>;
  canonical_records: number;
  merged_groups: number;
  rows_suppressed: number;
  samples: { id: string; decision: string; score: number; reason: string }[];
  exports: { id: string; kind: string; path: string; row_count: number; is_final: number; created_at: string }[];
};
