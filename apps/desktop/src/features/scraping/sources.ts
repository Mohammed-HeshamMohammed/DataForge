/** Source types shown in the Scraping tab, and how presets are sorted into them. */

export type SourceKind = "website" | "sitemap" | "feed" | "crawl" | "api" | "documents" | "archive" | "bulk";

export type RequestVariable = {
  type?: "string" | "integer" | "number" | "enum" | "sparql" | "path";
  required?: boolean;
  default?: string | number;
  choices?: string[];
  minimum?: number;
  maximum?: number;
  pattern?: string;
  max_length?: number;
};

export type SourcePreset = {
  id: string;
  version: string;
  page_type: string;
  category?: string;
  strategy: { preferred: string; allowed: string[] };
  extraction: { mode?: string } & Record<string, unknown>;
  discovery?: { mode?: string } & Record<string, unknown>;
  request?: { url_template?: string; variables?: Record<string, RequestVariable>; limit_variable?: string } & Record<string, unknown>;
  requires_contact_user_agent?: boolean;
};

export const SOURCES: { id: SourceKind; label: string; hint: string }[] = [
  { id: "website", label: "Website", hint: "One page or paginated listings: selectors, structured data, or article text." },
  { id: "sitemap", label: "Sitemap", hint: "Pages listed in the site's XML sitemaps." },
  { id: "feed", label: "Feed", hint: "Entries from an RSS or Atom feed." },
  { id: "crawl", label: "Site crawl", hint: "Follow links within one site, to a set depth." },
  { id: "api", label: "Open data API", hint: "CKAN, Socrata, Wikidata, OpenAlex, OpenStreetMap, SEC EDGAR, GDELT, and JSON APIs." },
  { id: "documents", label: "Documents", hint: "Tables from PDFs, and CSV, XLSX, or JSON files linked on a page." },
  { id: "archive", label: "Web archive", hint: "Historical captures from the Wayback Machine or Common Crawl, without loading the live site." },
  { id: "bulk", label: "Bulk corpus", hint: "A downloaded Web Data Commons schema.org subset." },
];

export const PURPOSES: { id: string; label: string }[] = [
  { id: "internal_analysis", label: "Internal analysis" },
  { id: "lead_research", label: "Lead research" },
  { id: "dataset_building", label: "Building a dataset" },
  { id: "price_monitoring", label: "Price monitoring" },
  { id: "research", label: "Research" },
  { id: "archival", label: "Archiving" },
  { id: "search_indexing", label: "Search indexing" },
  { id: "ai_training", label: "AI training" },
];

export function sourceOf(preset: SourcePreset): SourceKind {
  if (preset.strategy.preferred === "api") return "api";
  if (preset.strategy.preferred === "webview") return "website";
  if (preset.extraction?.mode === "document_tables") return "documents";
  const mode = preset.discovery?.mode ?? "none";
  if (mode === "sitemap") return "sitemap";
  if (mode === "feed") return "feed";
  if (mode === "crawl") return "crawl";
  return "website";
}

export function presetsFor<T extends SourcePreset>(presets: T[], source: SourceKind): T[] {
  if (source === "archive") return presets.filter((p) => p.strategy.preferred === "http" && sourceOf(p) === "website");
  return presets.filter((p) => sourceOf(p) === source);
}

/** Initial values for a preset's request variables (defaults, with the limit variable left to the record cap). */
export function defaultVariables(preset: SourcePreset | undefined): Record<string, string> {
  const variables = preset?.request?.variables ?? {};
  return Object.fromEntries(Object.entries(variables).map(([name, spec]) => [name, spec.default === undefined ? "" : String(spec.default)]));
}

/** Client-side hints only; the service validates every variable again before any request. */
export function variableProblems(preset: SourcePreset | undefined, values: Record<string, string>): string[] {
  const problems: string[] = [];
  for (const [name, spec] of Object.entries(preset?.request?.variables ?? {})) {
    const value = (values[name] ?? "").trim();
    if (!value) {
      if (spec.required) problems.push(`${name} is required`);
      continue;
    }
    if (spec.type === "integer" || spec.type === "number") {
      const number = Number(value);
      if (!Number.isFinite(number) || (spec.type === "integer" && !Number.isInteger(number))) problems.push(`${name} must be a ${spec.type}`);
      else if ((spec.minimum !== undefined && number < spec.minimum) || (spec.maximum !== undefined && number > spec.maximum)) problems.push(`${name} must be between ${spec.minimum} and ${spec.maximum}`);
    } else if (spec.type === "enum" && spec.choices && !spec.choices.includes(value)) {
      problems.push(`${name} must be one of ${spec.choices.join(", ")}`);
    } else if (spec.pattern && !new RegExp(`^(?:${spec.pattern})$`).test(value)) {
      problems.push(`${name} has an invalid format`);
    }
  }
  return problems;
}

export function needsStartUrl(preset: SourcePreset | undefined): boolean {
  return !preset?.request?.url_template;
}

export function variablePayload(preset: SourcePreset | undefined, values: Record<string, string>): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [name, spec] of Object.entries(preset?.request?.variables ?? {})) {
    const value = (values[name] ?? "").trim();
    if (!value) continue;
    out[name] = spec.type === "integer" || spec.type === "number" ? Number(value) : value;
  }
  return out;
}
