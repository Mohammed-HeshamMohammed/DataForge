import type { JobState } from "./types.ts";

export const ACTIVE_STATES: readonly JobState[] = ["validating", "queued", "running", "paused"];

export function isActive(state: JobState): boolean {
  return ACTIVE_STATES.includes(state);
}

const STAGE_LABELS: Record<string, string> = {
  loading: "Loading rows",
  normalizing: "Normalizing fields",
  finding_candidates: "Finding candidates",
  evaluating_evidence: "Evaluating evidence",
  building_groups: "Building safe groups",
  creating_review_queue: "Creating review queue",
  fetching: "Fetching pages",
  page_extracted: "Extracting records",
  reading_file: "Reading file",
  working: "Working",
};

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage.replace(/_/g, " ");
}

export const JOB_KIND_LABELS: Record<string, string> = {
  dataset_import: "Import",
  match: "Match",
  scrape: "Scrape",
  scrape_rendered: "Studio scrape",
  fixture: "Test job",
};

/** Masks a sensitive value so previews do not expose it until the user deliberately reveals it. */
export function maskValue(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  if (text.length === 0) {
    return "";
  }
  if (text.length <= 2) {
    return "••";
  }
  return `${text[0]}${"•".repeat(Math.min(text.length - 2, 8))}${text[text.length - 1]}`;
}

export function displayValue(value: unknown, sensitive: boolean, revealed: boolean): string {
  const text = value === null || value === undefined ? "" : String(value);
  return sensitive && !revealed ? maskValue(text) : text;
}

export function formatCount(value: number | undefined | null): string {
  return value === undefined || value === null ? "—" : value.toLocaleString("en-US");
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export const MATCH_ROLES = [
  "other",
  "ignore",
  "identifier",
  "name",
  "first_name",
  "last_name",
  "phone",
  "email",
  "address",
  "mailing_address",
  "city",
  "region",
  "postal_code",
  "url",
] as const;

const MULTI_COLUMN_ROLES = new Set(["phone", "email", "identifier", "other", "ignore"]);

/** Mirrors the backend rule so the form can explain problems early; the backend still validates. */
export function mappingProblems(mapping: Record<string, string>): string[] {
  const seen = new Map<string, string[]>();
  for (const [column, role] of Object.entries(mapping)) {
    if (!MULTI_COLUMN_ROLES.has(role)) {
      seen.set(role, [...(seen.get(role) ?? []), column]);
    }
  }
  const problems = [...seen.entries()]
    .filter(([, columns]) => columns.length > 1)
    .map(([role, columns]) => `"${role}" is used by ${columns.join(", ")}; choose one column or a more specific role.`);
  const evidence = ["identifier", "phone", "email", "address", "mailing_address", "url"];
  if (!Object.values(mapping).some((role) => evidence.includes(role))) {
    problems.push("Map at least one identifier, phone, email, address, or URL column; names alone cannot match safely.");
  }
  return problems;
}
