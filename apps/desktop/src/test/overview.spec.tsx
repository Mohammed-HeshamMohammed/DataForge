import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { accessibilityViolations } from "./a11y.ts";
import type { Overview } from "../features/dashboard/Dashboard.tsx";

const days = Array.from({ length: 14 }, (_, i) => ({ date: `2026-09-${String(11 + i).padStart(2, "0")}`, records: i === 13 ? 120 : i === 5 ? 40 : 0, runs: i === 13 ? 3 : i === 5 ? 1 : 0, failed: i === 13 ? 1 : 0, detail_pages: i === 13 ? 90 : 0 }));

const OVERVIEW: Overview = {
  generated_at: new Date().toISOString(),
  app: { version: "0.2.0" },
  project: { id: "p1", name: "Retail research", root_path: "C:/Projects/Retail research", created_at: "2026-09-01T00:00:00Z" },
  collection: { runs: 5, full_runs: 4, test_runs: 1, failed_runs: 1, records: 12940, records_last_7_days: 160, records_previous_7_days: 40, pages: 30, detail_pages: 90, hosts: 2 },
  activity: days,
  by_source: [{ source: "website", records: 12000, runs: 3 }, { source: "api", records: 940, runs: 1 }],
  top_hosts: [{ host: "shop.test", records: 12000, runs: 3, last_run: "2026-09-24T08:00:00Z" }],
  top_presets: [{ preset_id: "generic.html_list", version: "1.1.0", records: 12000, runs: 3, last_run: "2026-09-24T08:00:00Z" }],
  recent_runs: [
    { job_id: "j1", preset_id: "generic.html_list", preset_version: "1.1.0", source_kind: "website", host: "shop.test", run_mode: "full", status: "completed", engine: "httpx", records: 120, pages: 3,
      detail_pages: 90, detail_level: "full", detail_fields: 80, created_at: "2026-09-24T08:00:00Z", dataset_id: "d1", stop_reason: "missing_continuation", error: null },
    { job_id: "j2", preset_id: "generic.html_list", preset_version: "1.1.0", source_kind: "website", host: "big.test", run_mode: "full", status: "failed", engine: "httpx", records: 0, pages: 1,
      detail_pages: 0, detail_level: "full", detail_fields: 0, created_at: "2026-09-24T07:00:00Z", dataset_id: null, stop_reason: null,
      error: "Collection stopped: the page presented an access challenge (CAPTCHA/bot check)" },
  ],
  datasets: { count: 2, rows: 13000, scraped: 1, imported: 1, unmapped: 1, items: [
    { id: "d1", name: "Products", kind: "scrape", row_count: 12000, column_count: 90, created_at: "2026-09-24T08:00:00Z", mapping_version: 1, match_job_id: "m1", pending_review: 5, reviewed: 15, canonical_records: 900 },
    { id: "d2", name: "Suppliers", kind: "import", row_count: 1000, column_count: 6, created_at: "2026-09-20T08:00:00Z", mapping_version: null },
  ] },
  review: { pending: 5, reviewed: 15, safe_matches: 300, canonical_records: 900, matched_datasets: 1, exports: 0, last_export_at: null },
  jobs: { counts: { completed: 7, failed: 1, cancelled: 0 }, active: [], recent_failed: [{ id: "j2", kind: "scrape", label: "Scrape", state: "failed", created_at: "", updated_at: "2026-09-24T07:00:00Z", summary: "Collection stopped" }], recent: [] },
  watches: { active: 1, paused: 0, items: [{ id: "w1", name: "Nightly prices", status: "active", interval_minutes: 1440, next_run_at: "2026-09-25T02:00:00Z", last_state: "completed", last_changes: { added: 2, changed: 5, removed: 0 } }] },
  presets: { counts: { active: 20, degraded: 1, disabled: 0, deprecated: 2 }, total: 23, custom: 1, attention: [{ id: "generic.feed", version: "1.0.0", display_name: "Feed", status: "degraded", reason: "fixture failed" }] },
  site_limits: [{ host: "big.test", reasons: ["asks for 20 s between requests"], checked_at: "2026-09-24T07:00:00Z" }],
  storage: { database_bytes: 1_600_000, total_bytes: 2_000_000, areas: [{ area: "cache", label: "HTTP cache", bytes: 400_000 }] },
  attention: [
    { kind: "failed_jobs", severity: "critical", count: 1, target: "scraping", message: "1 job failed in the last 7 days", detail: "Collection stopped" },
    { kind: "review", severity: "warning", count: 5, target: "match", message: "5 possible matches wait for review", detail: null },
    { kind: "mapping", severity: "info", count: 1, target: "datasets", message: "1 dataset needs a confirmed mapping before matching", detail: "Suppliers", dataset_id: "d2" },
  ],
  checklist: [
    { id: "collect", label: "Import a file or collect from a website", done: true, target: "scraping" },
    { id: "export", label: "Export traceable results", done: false, target: "match" },
  ],
};

vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => false,
  call: vi.fn(async (command: string) => {
    if (command === "project.overview") return OVERVIEW;
    if (command === "health.check") return { status: "ok", service: "dataforge" };
    throw new Error(`unexpected ${command}`);
  }),
}));

describe("overview", () => {
  it("shows totals, what needs attention, recent runs, and a chart with a table view", async () => {
    const { Dashboard } = await import("../features/dashboard/Dashboard.tsx");
    const navigate = vi.fn();
    const { container } = render(<Dashboard navigate={navigate} />);
    await screen.findByRole("heading", { name: "Retail research" });
    const totals = screen.getByRole("list", { name: "Project totals" });
    expect(within(totals).getByText("12.9K")).toBeTruthy();
    expect(within(totals).getByText(/160 in the last 7 days \(\+120 vs the week before\)/)).toBeTruthy();
    expect(within(totals).getByText("90")).toBeTruthy();

    expect(screen.getByText("1 job failed in the last 7 days")).toBeTruthy();
    const attention = screen.getByRole("heading", { name: "Needs attention" }).closest("section")!;
    fireEvent.click(within(attention).getAllByRole("button", { name: "Open" })[2]);
    expect(navigate).toHaveBeenCalledWith("datasets", { datasetId: "d2" });

    expect(screen.getByText("Completed (no next page)")).toBeTruthy();
    expect(screen.getByText(/Failed: the page presented an access/)).toBeTruthy();

    const columns = screen.getByRole("list", { name: "Records collected per day" });
    const today = within(columns).getByRole("listitem", { name: /120 records, 3 runs, 90 detail pages, 1 failed/ });
    fireEvent.focus(today);
    expect(screen.getByRole("status").textContent).toContain("3 runs · 1 failed · 90 detail pages");
    expect(await accessibilityViolations(container)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Show as table" }));
    const table = screen.getByRole("table", { name: "Records collected per day" });
    expect(within(table).getAllByRole("row")).toHaveLength(15);
    expect(await accessibilityViolations(container)).toEqual([]);
  });
});
