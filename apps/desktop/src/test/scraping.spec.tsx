import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { accessibilityViolations } from "./a11y.ts";

const calls: { command: string; payload: Record<string, unknown> }[] = [];
const SITE_CATALOG = [
  { site_id: "amazon", site_name: "Amazon", site_category: "marketplace", domains: ["amazon.com"], recommended_method: "visual_studio", suggested_fields: ["title", "price", "rating"], requires_rendered: true, example_url: "https://www.amazon.com/s?k=laptop" },
  { site_id: "zillow", site_name: "Zillow", site_category: "real_estate", domains: ["zillow.com"], recommended_method: "visual_studio", suggested_fields: ["address", "price", "bedrooms"], requires_rendered: true, example_url: "https://www.zillow.com/homes/" },
  { site_id: "github", site_name: "GitHub", site_category: "developer", domains: ["github.com"], recommended_method: "visual_studio", suggested_fields: ["name", "owner", "stars"], requires_rendered: true, example_url: "https://github.com/openai" },
];
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
  {
    id: "generic.sitemap_structured", version: "1.0.0", display_name: "Sitemap: structured data", page_type: "structured_data", status: "active", source: "bundled", errors: [],
    url_scope: { allowed_hosts: [], user_supplied_host: true }, policy: { robots_policy: "respect" }, strategy: { preferred: "http", allowed: ["http"] },
    request_limits: { max_pages_default: 5000, max_records_default: 50000, min_delay_ms: 2000 }, extraction: { mode: "structured_data", fields: [] }, pagination: { type: "none" },
    discovery: { mode: "sitemap", sitemap: { url_pattern: "" } },
  },
  {
    id: "custom.detected.shop_test", version: "1.0.0", display_name: "Detected shop list", page_type: "draft", status: "active", source: "custom", errors: [],
    url_scope: { allowed_hosts: ["shop.test"] }, policy: { robots_policy: "respect" }, strategy: { preferred: "http", allowed: ["http", "webview"] },
    request_limits: { max_pages_default: 10, max_records_default: 500, min_delay_ms: 1000 }, extraction: { record_root: { css: "li.item" }, fields: [] }, pagination: { type: "none" },
  },
];

vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => false,
  pickFile: async () => null,
  call: vi.fn(async (command: string, payload: Record<string, unknown> = {}) => {
    calls.push({ command, payload });
    if (command === "preset.list") return PRESETS;
    if (command === "scrape.site_catalog") return SITE_CATALOG;
    if (command === "settings.get") return { default_purpose: "research", contact_identity: { organization: "", email: "" } };
    if (command === "watch.list") return [];
    if (command === "scrape.create_job") return { job_id: "job-1" };
    if (command === "scrape.detect_url") return { source: "website", preset_id: "custom.detected.shop_test", preset_version: "1.0.0", confidence: "high", reason: "Repeated record cards detected", content_type: "text/html", created_preset: true };
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
    await waitFor(() => expect(calls.some((entry) => entry.command === "preset.list")).toBe(true));
    fireEvent.click(screen.getByText("Choose a collection method manually"));
    expect(screen.getByRole("tab", { name: "Web archive" })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Open data API" }));
    await screen.findByText("GDELT: news article search");
    expect(screen.queryByLabelText("Start URL")).toBeNull(); // templated APIs take variables, not a URL
    const start = screen.getByRole("button", { name: "Preview 10 records" }) as HTMLButtonElement;
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

  it("detects a URL and selects the generated host-scoped preset", async () => {
    const { Scraping } = await import("../features/scraping/Scraping.tsx");
    const { container } = render(<Scraping navigate={() => {}} />);
    const input = await screen.findByLabelText("Page or data URL");
    fireEvent.click(screen.getByText("Advanced collection settings"));
    expect((screen.getByLabelText("Collection preset") as HTMLButtonElement).value).toBe("");
    expect(screen.getByText("Check a URL to choose a preset…")).toBeTruthy();
    fireEvent.change(input, { target: { value: "https://shop.test/products" } });
    fireEvent.click(screen.getByRole("button", { name: "Check this URL" }));
    await screen.findByText(/Repeated record cards detected/);
    expect(calls.find((entry) => entry.command === "scrape.detect_url")?.payload).toEqual({ url: "https://shop.test/products", purpose: "research" });
    expect((screen.getByLabelText("Collection preset") as HTMLButtonElement).value).toBe("custom.detected.shop_test@1.0.0");
    fireEvent.click(screen.getByText("Choose a collection method manually"));
    expect(screen.getByRole("tab", { name: "Use automatic detection" }).getAttribute("aria-selected")).toBe("true");
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("keeps a manually chosen method when a URL is checked and offers the detection as a suggestion", async () => {
    const { Scraping } = await import("../features/scraping/Scraping.tsx");
    const { container } = render(<Scraping navigate={() => {}} initialSource="sitemap" />);
    const input = await screen.findByLabelText("Page or data URL");
    fireEvent.click(screen.getByText("Advanced collection settings"));
    const preset = () => (screen.getByLabelText("Collection preset") as HTMLButtonElement).value;
    await waitFor(() => expect(preset()).toBe("generic.sitemap_structured@1.0.0"));
    fireEvent.change(input, { target: { value: "https://shop.test/" } });
    fireEvent.click(screen.getByRole("button", { name: "Check this URL" }));
    await screen.findByText(/Your Sitemap method is kept/);
    expect(preset()).toBe("generic.sitemap_structured@1.0.0");
    expect(screen.getByRole("tab", { name: "Sitemap" }).getAttribute("aria-selected")).toBe("true");
    expect(await accessibilityViolations(container)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Use the suggestion instead" }));
    await waitFor(() => expect(preset()).toBe("custom.detected.shop_test@1.0.0"));
    expect(screen.getByRole("tab", { name: "Use automatic detection" }).getAttribute("aria-selected")).toBe("true");
  });

  it("opens a method selected from Scrape Studio directly", async () => {
    const { Scraping } = await import("../features/scraping/Scraping.tsx");
    render(<Scraping navigate={() => {}} initialSource="api" />);
    await screen.findByText("GDELT: news article search");
    expect(screen.getByRole("tab", { name: "Open data API" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Open data API collection")).toBeTruthy();
  });

  it("browses supported sites and prepares a selected example for detection", async () => {
    const { Scraping } = await import("../features/scraping/Scraping.tsx");
    const { container } = render(<Scraping navigate={() => {}} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Supported sites" }));
    expect(await screen.findByRole("heading", { name: "Supported sites" })).toBeTruthy();
    expect(screen.getByText("3 sites")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Search supported sites"), { target: { value: "real estate" } });
    expect(screen.getByRole("heading", { name: "Zillow" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Amazon" })).toBeNull();
    expect(screen.getByText("1 site")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Use Zillow" }));
    expect(screen.getByRole("tab", { name: "Collect" }).getAttribute("aria-selected")).toBe("true");
    expect((screen.getByLabelText("Page or data URL") as HTMLInputElement).value).toBe("https://www.zillow.com/homes/");
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
