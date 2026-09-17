import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { accessibilityViolations } from "./a11y.ts";

const calls: { command: string; payload: Record<string, unknown> }[] = [];
const PRESETS = [
  {
    id: "generic.structured_data", version: "1.0.0", display_name: "Structured data (schema.org)", page_type: "structured_data", status: "active", source: "bundled", errors: [],
    url_scope: { allowed_hosts: [], user_supplied_host: true }, policy: { robots_policy: "respect" }, strategy: { preferred: "http", allowed: ["http", "webview"] },
    request_limits: { max_pages_default: 50, max_records_default: 1000, min_delay_ms: 2000 }, extraction: { mode: "structured_data", fields: [] }, pagination: { type: "none" },
  },
  {
    id: "gdelt.doc_search", version: "1.0.0", display_name: "GDELT: news article search", page_type: "api_collection", status: "active", source: "bundled", errors: [],
    url_scope: { allowed_hosts: ["api.gdeltproject.org"] }, policy: { robots_policy: "respect" }, strategy: { preferred: "api", allowed: ["api"] },
    request_limits: { max_pages_default: 1, max_records_default: 250, min_delay_ms: 5000 }, extraction: { item_path: "articles", fields: [] }, pagination: { type: "none" },
    request: { url_template: "https://api.gdeltproject.org/api/v2/doc/doc?query={{query}}", limit_variable: "limit", variables: { query: { type: "string", required: true }, limit: { type: "integer", default: 75, minimum: 1, maximum: 250 } } },
  },
];

vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => false,
  pickFile: async () => null,
  call: vi.fn(async (command: string, payload: Record<string, unknown> = {}) => {
    calls.push({ command, payload });
    if (command === "preset.list") return PRESETS;
    if (command === "settings.get") return { default_purpose: "research", contact_identity: { organization: "", email: "" } };
    if (command === "watch.list") return [];
    if (command === "scrape.create_job") return { job_id: "job-1" };
    if (command === "preset.fixture_from_capture") return { fixture: "project:fixtures/captured/p/abc.html", source_url: "https://shop.test/p", redactions: { scripts: 2, contact_details: 1, tokens: 0 } };
    if (command === "job.get") return { id: "job-1", kind: "scrape", state: "running", params: {}, result: null, error: null, created_at: "", updated_at: "", events: [] };
    throw new Error(`unexpected ${command}`);
  }),
}));

describe("scraping tab", () => {
  beforeEach(() => {
    calls.length = 0;
  });

  it("switches source types, builds API variables, and sends purpose with the job", async () => {
    const { Scraping } = await import("../features/scraping/Scraping.tsx");
    const { container } = render(<Scraping navigate={() => {}} />);
    await screen.findByText(/Structured data \(schema.org\) —/);
    expect(screen.getByRole("tab", { name: "Web archive" })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Open data API" }));
    await screen.findByText(/GDELT: news article search —/);
    expect(screen.queryByLabelText("Start URL")).toBeNull(); // templated APIs take variables, not a URL
    const start = screen.getByRole("button", { name: "Test 10 records" }) as HTMLButtonElement;
    fireEvent.click(screen.getByLabelText(/I am authorized/));
    expect(start.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("query"), { target: { value: "flood" } });
    await waitFor(() => expect(start.disabled).toBe(false));
    fireEvent.click(start);
    await waitFor(() => expect(calls.some((c) => c.command === "scrape.create_job")).toBe(true));
    const job = calls.find((c) => c.command === "scrape.create_job")!.payload;
    expect(job).toMatchObject({ preset_id: "gdelt.doc_search", purpose: "research", run_mode: "test", variables: { query: "flood", limit: 75 }, engine: "httpx", start_url: "" });
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("shows field-level changes and saves a captured page as a sanitized fixture", async () => {
    const { ScrapeResult } = await import("../features/scraping/Scraping.tsx");
    const result = {
      strategy_used: "http", engine: "httpx", pages_fetched: 2, records_extracted: 1, records_rejected: 0, records_duplicate: 0, stop_reason: "completed",
      warc_capture: "C:/project/captures/job-9.warc.gz", sample_records: [],
      archive_diff: { counts: { added: 0, removed: 0, changed: 1, unchanged: 0, unkeyed: 0 }, added: [], removed: [], changed: [{ key: { sku: "W-1" }, changes: { price: { before: "$19.99", after: "$99.00" } } }] },
    };
    const { container } = render(<ScrapeResult result={result} jobId="job-9" onOpenDataset={() => {}} />);
    expect(screen.getByText("0 added · 0 removed · 1 changed")).toBeTruthy();
    expect(screen.getByRole("cell", { name: "$99.00" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save captured page as a test fixture" }));
    await screen.findByText(/2 scripts, 1 contact details/);
    expect(calls.find((c) => c.command === "preset.fixture_from_capture")?.payload).toEqual({ job_id: "job-9" });
    expect(await accessibilityViolations(container)).toEqual([]);
  });
});
