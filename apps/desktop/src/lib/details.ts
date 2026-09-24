/** Record details: the levels a scrape can collect and how a record's keys group for display.
 *  Grouping mirrors dataforge_scraping.details.detail_group so the UI and exports agree. */

export type DetailLevel = "none" | "basic" | "standard" | "full";

export const DETAIL_LEVELS: { id: DetailLevel; label: string; hint: string }[] = [
  { id: "none", label: "Fields only", hint: "Only the preset's fields and provenance." },
  {
    id: "basic",
    label: "Basic",
    hint: "Adds value details: price amount and currency, phone numbers in E.164 with country, ISO dates, domains, ratings with scale, numbers with units, stock status.",
  },
  {
    id: "standard",
    label: "Standard",
    hint: "Also adds everything inside each record's element (links, images, current and original prices, rating, availability, contacts, data attributes, microdata) and the page's metadata. No extra requests.",
  },
  {
    id: "full",
    label: "Full",
    hint: "Also opens each record's detail page for its structured data, specification tables, main text, contacts, social profiles, and images. One extra request per record, under the same scope, robots, signal, and politeness rules.",
  },
];

export const DEFAULT_DETAIL_LEVEL: DetailLevel = "full";

export function isDetailLevel(value: unknown): value is DetailLevel {
  return typeof value === "string" && DETAIL_LEVELS.some((level) => level.id === value);
}

export type DetailGroup = "field" | "value" | "item" | "detail" | "page" | "provenance";

export const GROUP_LABELS: Record<DetailGroup, string> = {
  field: "Fields",
  value: "Value details",
  item: "From the record's element",
  detail: "From the detail page",
  page: "From the page",
  provenance: "Provenance",
};

const GROUP_ORDER: DetailGroup[] = ["field", "value", "item", "detail", "page", "provenance"];

const PROVENANCE = new Set([
  "source_url", "source_retrieved_at", "preset_id", "preset_version", "strategy_used", "extraction_mode", "structured_syntax",
  "structured_conflicts", "schema_type", "source_page", "source_table", "source_row", "text_source",
  "source_kind", "source_file", "archive", "archive_crawl", "archive_capture_time", "archive_digest", "archive_role",
]);

const VALUE_SUFFIXES = new Set([
  "amount", "currency", "e164", "country", "type", "domain", "path", "file_type", "absolute", "normalized", "iso", "value", "scale",
  "in_stock", "quantity", "number", "unit", "street", "city", "state", "postal_code", "word_count",
]);

export function detailGroup(key: string, record: Record<string, unknown>): DetailGroup {
  if (key.startsWith("item.")) return "item";
  if (key.startsWith("page.")) return "page";
  if (key.startsWith("detail.")) return "detail";
  if (PROVENANCE.has(key)) return "provenance";
  const dot = key.lastIndexOf(".");
  if (dot > 0 && VALUE_SUFFIXES.has(key.slice(dot + 1)) && key.slice(0, dot) in record) return "value";
  return "field";
}

export function isDetailKey(key: string, record: Record<string, unknown>): boolean {
  const group = detailGroup(key, record);
  return group !== "field" && group !== "provenance";
}

/** A record's non-empty entries by group, in display order. */
export function groupRecord(record: Record<string, unknown>): { group: DetailGroup; entries: [string, unknown][] }[] {
  const groups = new Map<DetailGroup, [string, unknown][]>();
  for (const [key, value] of Object.entries(record)) {
    if (value === null || value === undefined || value === "") continue;
    const group = detailGroup(key, record);
    groups.set(group, [...(groups.get(group) ?? []), [key, value]]);
  }
  return GROUP_ORDER.filter((group) => groups.has(group)).map((group) => ({ group, entries: groups.get(group)! }));
}

/** The label shown for a key inside its group ("detail.spec.upc" -> "spec.upc"). */
export function shortKey(key: string, group: DetailGroup): string {
  return group === "item" || group === "page" || group === "detail" ? key.slice(key.indexOf(".") + 1) : key;
}

/** Columns for a compact table: the preset's own fields first, then (optionally) detail keys. */
export function tableColumns(
  records: Record<string, unknown>[],
  options: { includeDetails?: boolean; keepProvenance?: boolean; exclude?: string[]; max?: number } = {},
): string[] {
  const exclude = new Set(options.exclude ?? []);
  const seen = new Map<string, boolean>();
  for (const record of records) {
    for (const key of Object.keys(record)) {
      if (seen.has(key) || exclude.has(key)) continue;
      const group = detailGroup(key, record);
      if (group === "provenance" && key !== "source_url" && !options.keepProvenance) continue;
      seen.set(key, group !== "field" && group !== "provenance");
    }
  }
  const fields = [...seen].filter(([, detail]) => !detail).map(([key]) => key);
  const details = options.includeDetails ? [...seen].filter(([, detail]) => detail).map(([key]) => key) : [];
  return [...fields, ...details].slice(0, options.max ?? 200);
}

/** Detail keys that read badly in a table cell (whole texts, lists, page-level values that repeat on every record);
 *  the record inspector shows them. */
const NOISY_COLUMN = /^(item\.(text|links|images|html)|detail\.(text|html|images|bullets|retrieved_at|status|final_url|content_type|url|page\..*)|page\..*)$/;
const COLUMN_RANK: Partial<Record<DetailGroup, number>> = { item: 0, detail: 1, value: 2 };

/** Columns for a results table: the preset's own fields, then the detected values (card, item-page, and value
 *  details) that most records carry and that differ between records, up to `max` columns. */
export function richColumns(
  records: Record<string, unknown>[],
  options: { keepProvenance?: boolean; exclude?: string[]; max?: number } = {},
): string[] {
  const exclude = new Set(options.exclude ?? []);
  const fields = tableColumns(records, { keepProvenance: options.keepProvenance, exclude: [...exclude] });
  const found = new Map<string, { count: number; values: Set<string>; rank: number }>();
  for (const record of records) {
    for (const [key, value] of Object.entries(record)) {
      if (value === null || value === undefined || value === "" || typeof value === "object" || exclude.has(key) || NOISY_COLUMN.test(key)) continue;
      const rank = COLUMN_RANK[detailGroup(key, record)];
      if (rank === undefined) continue;
      const entry = found.get(key) ?? { count: 0, values: new Set<string>(), rank };
      entry.count += 1;
      if (entry.values.size < 2) entry.values.add(String(value));
      found.set(key, entry);
    }
  }
  // A value identical on every record says nothing per record. Coverage first (in steps of a tenth, so near-equal
  // keys keep their group order), then card, item page, value.
  const bucket = (count: number) => Math.round((count / Math.max(1, records.length)) * 10);
  const detected = [...found]
    .filter(([, entry]) => records.length < 3 || entry.values.size > 1 || entry.count < records.length)
    .sort(([a, x], [b, y]) => bucket(y.count) - bucket(x.count) || x.rank - y.rank || a.localeCompare(b))
    .map(([key]) => key);
  return [...fields, ...detected].slice(0, options.max ?? 24);
}

export function detailCount(record: Record<string, unknown>): number {
  return Object.entries(record).filter(([key, value]) => value !== null && value !== undefined && value !== "" && isDetailKey(key, record)).length;
}

/** Keys that may hold personal data even though no mapping marks them (phones, e-mails, addresses, names). */
export function looksPersonal(key: string): boolean {
  return /phone|e164|email|mail|address|street|contact|given_name|family_name|author|person|social/i.test(key);
}

export type DetailSummary = {
  level: DetailLevel;
  records_enriched: number;
  fields_added: number;
  groups: { value: number; item: number; page: number; detail: number };
  coverage: Record<string, number>;
  detail_pages?: {
    candidates: number;
    fetched: number;
    reused: number;
    failed: number;
    skipped_scope: number;
    skipped_robots: number;
    stop_reason: string;
  } | null;
};

export function describeDetails(summary: DetailSummary | null | undefined): string {
  if (!summary) return "";
  const level = DETAIL_LEVELS.find((l) => l.id === summary.level)?.label ?? summary.level;
  if (summary.level === "none") return `${level}: no details collected`;
  const parts = [`${level}: ${summary.fields_added} detail field${summary.fields_added === 1 ? "" : "s"} on ${summary.records_enriched} record${summary.records_enriched === 1 ? "" : "s"}`];
  const groups = Object.entries(summary.groups)
    .filter(([, count]) => count > 0)
    .map(([group, count]) => `${count} ${group === "item" ? "element" : group}`);
  if (groups.length) parts.push(groups.join(", "));
  const pages = summary.detail_pages;
  if (pages) {
    const extra = [
      pages.failed ? `${pages.failed} failed` : "",
      pages.skipped_scope ? `${pages.skipped_scope} out of scope` : "",
      pages.skipped_robots ? `${pages.skipped_robots} disallowed by robots.txt` : "",
      pages.reused ? `${pages.reused} shared` : "",
    ].filter(Boolean);
    parts.push(`${pages.fetched} detail page${pages.fetched === 1 ? "" : "s"} read${extra.length ? ` (${extra.join(", ")})` : ""}`);
  }
  return parts.join(" · ");
}
