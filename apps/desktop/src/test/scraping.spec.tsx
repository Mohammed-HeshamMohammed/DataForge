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
    if (command === "scrape.test_detail") return { url: String(payload.url), details: { "detail.description": "Long text", "detail.sku": "W-1", "detail.spec.weight": "1.5 kg" }, fields: { description: "Long text", seller: null }, missing: ["seller"] };
    if (command === "preset.validate") return { errors: [] };
    if (command === "preset.save_custom") return { id: "custom.local.items", version: "1.0.0" };
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
    expect(job).toMatchObject({ preset_id: "gdelt.doc_search", purpose: "research", run_mode: "test", variables: { query: "flood", limit: 75 }, engine: "httpx", start_url: "", detail_level: "full" });
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
  it("summarizes record details and opens every value of a record, grouped", async () => {
    const { ScrapeResult } = await import("../features/scraping/Scraping.tsx");
    const record = {
      title: "Widget 1", link: "https://shop.test/item/SKU-1", "link.domain": "shop.test", "item.price": "$19.99", "item.price.amount": 19.99, "item.price_original": "$25.00",
      "page.title": "Catalog", "detail.sku": "SKU-1", "detail.spec.weight": "1.5 kg", "detail.text": "x".repeat(900),
      source_url: "https://shop.test/c", source_retrieved_at: "2026-09-24T08:00:00Z", preset_id: "generic.html_list", preset_version: "1.1.0", strategy_used: "http",
    };
    const result = {
      strategy_used: "http", engine: "httpx", pages_fetched: 1, records_extracted: 1, records_rejected: 0, records_duplicate: 0, stop_reason: "completed", sample_records: [record],
      details: { level: "full", records_enriched: 1, fields_added: 7, groups: { value: 1, item: 3, page: 1, detail: 3 }, coverage: {},
        detail_pages: { candidates: 1, fetched: 1, reused: 0, failed: 0, skipped_scope: 0, skipped_robots: 0, stop_reason: "completed" } },
    };
    const { container } = render(<ScrapeResult result={result} jobId="job-2" onOpenDataset={() => {}} />);
    expect(screen.getByText(/Full: 7 detail fields on 1 record · 1 value, 3 element, 1 page, 3 detail · 1 detail page read/)).toBeTruthy();
    // Detected values are columns by default; whole texts and page-level values stay in the inspector.
    const headers = () => screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers()).toEqual(["title", "link", "source_url", "item.price", "item.price_original", "item.price.amount", "detail.sku", "detail.spec.weight", "link.domain", "Details"]);
    fireEvent.click(screen.getByRole("button", { name: "Preset fields (3)" }));
    expect(headers()).toEqual(["title", "link", "source_url", "Details"]);
    fireEvent.click(screen.getByRole("button", { name: "Detected values (9 columns)" }));
    fireEvent.click(screen.getByRole("button", { name: "Inspect record 1" }));
    const inspector = screen.getByRole("region", { name: "Record 1 of 1" });
    for (const group of ["Fields", "Value details", "From the record's element", "From the detail page", "From the page", "Provenance"]) {
      expect(inspector.textContent).toContain(group);
    }
    expect(screen.getByTitle("detail.spec.weight").textContent).toBe("spec.weight");
    fireEvent.click(screen.getByRole("button", { name: "Show all 900 characters" }));
    expect(screen.getByRole("button", { name: "Show less" })).toBeTruthy();
    expect(await accessibilityViolations(container)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region", { name: "Record 1 of 1" })).toBeNull();
  });

  it("sends the chosen record detail level with the job", async () => {
    const { Scraping } = await import("../features/scraping/Scraping.tsx");
    render(<Scraping navigate={() => {}} />);
    await screen.findByText(/Structured data \(schema.org\) —/);
    const level = screen.getByLabelText("Record detail") as HTMLSelectElement;
    expect(level.value).toBe("full");
    fireEvent.change(level, { target: { value: "basic" } });
    expect(screen.getByText(/Adds value details/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Start URL"), { target: { value: "https://shop.test/p" } });
    fireEvent.click(screen.getByLabelText(/I am authorized/));
    fireEvent.click(screen.getByRole("button", { name: "Test 10 records" }));
    await waitFor(() => expect(calls.some((c) => c.command === "scrape.create_job")).toBe(true));
    expect(calls.find((c) => c.command === "scrape.create_job")!.payload).toMatchObject({ preset_id: "generic.structured_data", detail_level: "basic" });
  });
  it("adds item-page fields to a custom preset, previews an item page, and saves them", async () => {
    const { Scraping } = await import("../features/scraping/Scraping.tsx");
    const { container } = render(<Scraping navigate={() => {}} />);
    await screen.findByText(/Structured data \(schema.org\) —/);
    fireEvent.click(screen.getByRole("tab", { name: "Customize preset" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add item-page field" }));
    fireEvent.change(screen.getByLabelText("Item-page field name"), { target: { value: "description" } });
    fireEvent.change(screen.getByLabelText("CSS selector for description on the item page"), { target: { value: "#productDescription" } });
    fireEvent.click(screen.getByRole("button", { name: "Add item-page field" }));
    fireEvent.change(screen.getAllByLabelText("Item-page field name")[1], { target: { value: "seller" } });
    fireEvent.change(screen.getByLabelText("CSS selector for seller on the item page"), { target: { value: "#seller a" } });
    fireEvent.change(screen.getByLabelText("What to read for seller"), { target: { value: "href" } });
    fireEvent.change(screen.getByLabelText("Sample item page URL"), { target: { value: "https://shop.test/item/1" } });
    fireEvent.click(screen.getByRole("button", { name: "Test item page" }));
    await screen.findByRole("region", { name: "What DataForge reads from https://shop.test/item/1" });
    expect(screen.getByText(/Not found on this page: seller/)).toBeTruthy();
    const test = calls.find((c) => c.command === "scrape.test_detail")!.payload as { preset: { details: unknown; url_scope: { allowed_hosts: string[] } } };
    expect(test.preset.url_scope.allowed_hosts).toEqual(["shop.test"]);
    expect(test.preset.details).toEqual({ level: "full", follow: { fields: [
      { key: "description", type: "string", selectors: [{ css: "#productDescription" }], transforms: ["trim", "collapse_whitespace"] },
      { key: "seller", type: "url", selectors: [{ css: "#seller a", attribute: "href" }], transforms: ["to_absolute_url"] },
    ] } });
    expect(await accessibilityViolations(container)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Validate and save custom preset" }));
    await waitFor(() => expect(calls.some((c) => c.command === "preset.save_custom")).toBe(true));
    const saved = calls.find((c) => c.command === "preset.save_custom")!.payload as { preset: { details: { follow: { fields: unknown[] } } } };
    expect(saved.preset.details.follow.fields).toHaveLength(2);
  });
});
