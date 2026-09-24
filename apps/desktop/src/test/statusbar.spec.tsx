import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StatusBar, type StatusBarProps } from "../components/StatusBar.tsx";
import type { ProjectSummary } from "../components/Sidebar.tsx";
import type { Job } from "../lib/types.ts";
import { accessibilityViolations } from "./a11y.ts";

let healthy = true;
vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => false,
  call: vi.fn(async (command: string) => {
    if (command !== "health.check") throw new Error(`unexpected ${command}`);
    if (!healthy) throw new Error("connection refused");
    return { status: "ok", checked_at: "2026-09-24T10:00:00Z" };
  }),
}));

const job = (id: string, kind: string, state: Job["state"]): Job => ({ id, kind, state, created_at: "", updated_at: "", params: {}, result: null, error: null });
const summary: ProjectSummary = {
  datasets: [
    { id: "d1", name: "Austin listings", kind: "import", row_count: 2408, mapping_version: 2, job_id: "j1", pending_review: 20, reviewed: 60, safe_matches: 284, canonical_records: 2100 },
    { id: "d2", name: "Leads raw", kind: "scrape", row_count: 90, mapping_version: null },
  ],
  totals: { pending_review: 20, reviewed: 60, safe_matches: 284, rows: 2498 },
  active_jobs: [job("a", "scrape", "running")],
  recent_failed: [job("f", "scrape", "failed")],
  job_counts: {},
};
const project = { id: "p", name: "Market study", root_path: "C:\\data\\market", created_at: "2026-09-15T00:00:00Z" };

function setup(overrides: Partial<StatusBarProps> = {}) {
  const props: StatusBarProps = {
    project, summary, navigate: vi.fn(), layout: { left: true, right: false }, onToggleLeft: vi.fn(), onToggleRight: vi.fn(),
    zoom: 1, onResetZoom: vi.fn(), update: null, onUpdate: vi.fn(), onProjectFolder: vi.fn(), onAbout: vi.fn(), version: "0.2.0", ...overrides,
  };
  return { props, ...render(<StatusBar {...props} />) };
}

describe("status bar", () => {
  beforeEach(() => {
    healthy = true;
  });

  it("shows live indicators as icons with counts and names each one", async () => {
    const { container } = setup();
    await screen.findByRole("button", { name: "Local service running" });
    expect(screen.getByRole("button", { name: "1 recent job failed. Open the Overview to see why" }).textContent).toBe("1");
    expect(screen.getByRole("button", { name: "1 dataset need a confirmed mapping before matching" }).textContent).toBe("1");
    expect(screen.getByRole("button", { name: "Running: Scrape. Show the job center" }).className).toContain("is-busy");
    expect(screen.getByRole("button", { name: "20 pairs waiting for review" }).textContent).toBe("20");
    expect(screen.getByRole("button", { name: "2 datasets, 2,498 rows" }).textContent).toBe("2");
    expect(screen.getByRole("button", { name: /^Project Market study/ }).textContent).toBe("Market study");
    expect(screen.queryByRole("button", { name: /^Zoom/ })).toBeNull(); // only when zoomed
    expect(screen.getByRole("button", { name: "1 recent job failed. Open the Overview to see why" }).className).toContain("tone-critical");
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("each indicator goes where its detail is", async () => {
    const { props } = setup({ zoom: 1.1, update: { available: true, version: "0.3.0" } });
    await screen.findByRole("button", { name: "Local service running" });
    fireEvent.click(screen.getByRole("button", { name: /job failed/ }));
    fireEvent.click(screen.getByRole("button", { name: /need a confirmed mapping/ }));
    fireEvent.click(screen.getByRole("button", { name: /waiting for review/ }));
    expect(vi.mocked(props.navigate!).mock.calls.map(([tab]) => tab)).toEqual(["dashboard", "datasets", "match"]);
    fireEvent.click(screen.getByRole("button", { name: "Show the job center", pressed: false }));
    fireEvent.click(screen.getByRole("button", { name: "Hide the project sidebar" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom 110%. Reset to 100%" }));
    fireEvent.click(screen.getByRole("button", { name: "DataForge 0.3.0 is available. Open update settings" }));
    fireEvent.click(screen.getByRole("button", { name: "DataForge 0.2.0. About DataForge" }));
    fireEvent.click(screen.getByRole("button", { name: /^Project Market study/ }));
    for (const handler of [props.onToggleRight, props.onToggleLeft, props.onResetZoom, props.onUpdate, props.onAbout, props.onProjectFolder]) expect(handler).toHaveBeenCalledOnce();
  });

  it("turns the service block red when the service stops answering, and stays minimal without a project", async () => {
    healthy = false;
    setup({ project: null, summary: null, navigate: undefined });
    await waitFor(() => expect(screen.getByRole("button", { name: "Local service offline" }).textContent).toBe("Offline"));
    expect(screen.getByRole("button", { name: "Local service offline" }).className).toContain("is-offline");
    expect(screen.queryByRole("button", { name: /waiting for review|Review queue/ })).toBeNull();
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["Local service offline", "DataForge 0.2.0. About DataForge"]);
  });
});
