import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { TitleBar } from "../components/TitleBar.tsx";
import { Sidebar, type ProjectSummary } from "../components/Sidebar.tsx";
import { PaneHeader } from "../components/PaneHeader.tsx";
import { ConfirmButton, JobProgress, StateBadge } from "../components/ui.tsx";
import type { Job } from "../lib/types.ts";
import { accessibilityViolations } from "./a11y.ts";

const summary: ProjectSummary = {
  datasets: [
    { id: "d1", name: "Austin listings", kind: "import", row_count: 2408, mapping_version: 2, job_id: "j1", pending_review: 20, reviewed: 60, safe_matches: 284, canonical_records: 2100 },
    { id: "d2", name: "Leads raw", kind: "scrape", row_count: 90, mapping_version: null },
  ],
  totals: { pending_review: 20, reviewed: 60, safe_matches: 284, rows: 2498 },
  active_jobs: [],
  recent_failed: [],
  job_counts: {},
};
const project = { id: "p", name: "Market study", root_path: "C:\\data\\market", created_at: "2026-09-15T00:00:00Z" };

describe("title bar", () => {
  it("has the four window controls in order with accessible names", async () => {
    const onToggleTheme = vi.fn();
    const onUpdate = vi.fn();
    const { container } = render(<TitleBar theme="dark" onToggleTheme={onToggleTheme} onUpdate={onUpdate} updateAvailable />);
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["Toggle theme", "Updates", "Minimize", "Close"]);
    fireEvent.click(screen.getByLabelText("Toggle theme"));
    fireEvent.click(screen.getByLabelText("Updates"));
    expect(onToggleTheme).toHaveBeenCalledOnce();
    expect(onUpdate).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Updates").getAttribute("title")).toMatch(/verified update/);
    expect(await accessibilityViolations(container)).toEqual([]);
  });
});

describe("left sidebar", () => {
  it("shows review progress, filters datasets, and exposes status as text", async () => {
    const navigate = vi.fn();
    const { container } = render(<Sidebar project={project} summary={summary} navigate={navigate} onSwitchProject={() => {}} />);
    expect(screen.getByRole("img", { name: "75% of review items resolved" })).toBeTruthy();
    expect(screen.getByText("Unmapped")).toBeTruthy(); // status is text, not colour only
    fireEvent.change(screen.getByLabelText("Filter datasets"), { target: { value: "austin" } });
    expect(screen.queryByText("Leads raw")).toBeNull();
    fireEvent.click(screen.getByText("Austin listings"));
    expect(navigate).toHaveBeenCalledWith("match", { datasetId: "d1", jobId: "j1" });
    fireEvent.click(screen.getByLabelText("Settings"));
    expect(navigate).toHaveBeenCalledWith("settings");
    expect(await accessibilityViolations(container)).toEqual([]);
  });
});

describe("pane header", () => {
  it("renders a tablist with the selected section and a labelled refresh button", async () => {
    const onTab = vi.fn();
    const { container } = render(
      <PaneHeader title="Overview" tabs={[{ id: "a", label: "Overview" }, { id: "b", label: "Datasets" }]} activeTab="a" onTab={onTab} chip={{ label: "Needs review", value: 20, onClick: () => {} }} onRefresh={() => {}} />,
    );
    const tabs = within(screen.getByRole("tablist", { name: "Sections" })).getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false"]);
    fireEvent.click(tabs[1]);
    expect(onTab).toHaveBeenCalledWith("b");
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy();
    expect(await accessibilityViolations(container)).toEqual([]);
  });
});

describe("shared components", () => {
  it("destructive confirmation explains consequences and focuses the safe choice", async () => {
    const onConfirm = vi.fn();
    const { container } = render(<ConfirmButton label="Delete dataset" danger title="Delete it?" body={<p>Raw rows are removed.</p>} confirmLabel="Delete" onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete dataset" }));
    const dialog = container.querySelector("dialog[open]") as HTMLElement;
    expect(within(dialog).getByText("Raw rows are removed.")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Keep it" }).hasAttribute("autofocus") || document.activeElement?.textContent === "Keep it").toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("job progress uses backend stages with a polite live region and text states", async () => {
    const job: Job = {
      id: "j", kind: "match", state: "running", created_at: "", updated_at: "", params: {}, result: null, error: null,
      events: [
        { id: 1, event_type: "job.stage_changed", occurred_at: "", payload: { stage: "normalizing", rows: 2408 } },
        { id: 2, event_type: "job.stage_changed", occurred_at: "", payload: { stage: "finding_candidates", candidate_pairs: 38492 } },
      ],
    };
    const { container } = render(<JobProgress job={job} />);
    expect(container.querySelector("[aria-live='polite']")).toBeTruthy();
    expect(screen.getByText("Normalizing fields")).toBeTruthy();
    expect(screen.getByText(/candidate pairs: 38,492/)).toBeTruthy();
    render(<StateBadge state="failed" />);
    expect(screen.getAllByText(/failed/).length).toBeGreaterThan(0);
    expect(await accessibilityViolations(container)).toEqual([]);
  });
});

describe("styles", () => {
  const css = readFileSync("src/styles/shell.css", "utf-8");

  it("honours reduced motion and visible focus", () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*?transition: none !important/);
    expect(css).toMatch(/:focus-visible\s*\{[\s\S]*?box-shadow: var\(--focus-ring-default\)/);
  });

  it("defines narrow and compact layouts at the specified breakpoints", () => {
    expect(css).toContain("@media (max-width: 1023px)");
    expect(css).toContain("@media (max-width: 767px)");
  });
});
