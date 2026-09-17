import { useState } from "react";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { TitleBar } from "../components/TitleBar.tsx";
import { MenuBar } from "../components/MenuBar.tsx";
import { CommandCenter } from "../components/CommandPalette.tsx";
import { fuzzyScore, matchesShortcut, type Command } from "../lib/commands.ts";
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
  it("has settings, theme, updates, and window controls in order with accessible names", async () => {
    const onToggleTheme = vi.fn();
    const onUpdate = vi.fn();
    const onSettings = vi.fn();
    const { container } = render(<TitleBar theme="dark" onToggleTheme={onToggleTheme} onUpdate={onUpdate} onSettings={onSettings} updateAvailable />);
    const actions = container.querySelector(".titlebar-actions") as HTMLElement;
    expect(within(actions).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["Settings", "Toggle theme", "Updates", "Minimize", "Maximize", "Close"]);
    fireEvent.click(screen.getByLabelText("Toggle theme"));
    fireEvent.click(screen.getByLabelText("Updates"));
    fireEvent.click(screen.getByLabelText("Settings"));
    expect(onToggleTheme).toHaveBeenCalledOnce();
    expect(onUpdate).toHaveBeenCalledOnce();
    expect(onSettings).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Updates").getAttribute("title")).toMatch(/verified update/);
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("unifies the project location and command search in one field that drops down results", async () => {
    const reveal = vi.fn();
    const commands: Command[] = [
      { id: "file.reveal", label: "Open project folder", menu: "file", run: reveal },
      { id: "go.match", label: "Match & Deduplicate", menu: "go", shortcut: "Ctrl+5", run: () => {} },
    ];
    const Harness = () => {
      const [open, setOpen] = useState(false);
      return <TitleBar theme="dark" onToggleTheme={() => {}} onUpdate={() => {}} commands={commands} project={{ name: "Market study", path: "E:\Research\Market study" }} commandCenterOpen={open} onCommandCenterChange={setOpen} />;
    };
    const { container } = render(<Harness />);
    const bar = screen.getByRole("button", { name: /Search commands\. Current project: Market study/ });
    expect(bar.textContent).toContain("E:\Research\Market study");
    fireEvent.click(bar);
    const input = screen.getByRole("combobox", { name: "Search commands" });
    const results = screen.getByRole("listbox", { name: "Commands" });
    expect(input.closest(".command-center")?.contains(results)).toBe(true); // the dropdown is anchored under the search field
    fireEvent.change(input, { target: { value: "folder" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(reveal).toHaveBeenCalledOnce();
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(await accessibilityViolations(container)).toEqual([]);
  });
});

describe("menu bar and command palette", () => {
  const makeCommands = (spy: (id: string) => void): Command[] => [
    { id: "file.open", label: "Open project…", menu: "file", group: 1, shortcut: "Ctrl+O", run: () => spy("file.open") },
    { id: "file.recent", label: "Open recent", menu: "file", group: 1, run: () => {}, children: [{ id: "file.recent.0", label: "Study — E:\Study", menu: "file", run: () => spy("recent") }] },
    { id: "file.exit", label: "Exit", menu: "file", group: 2, run: () => spy("file.exit") },
    { id: "edit.find", label: "Find dataset…", menu: "edit", shortcut: "Ctrl+F", run: () => spy("edit.find"), enabled: false },
    { id: "view.palette", label: "Command palette…", menu: "view", shortcut: "Ctrl+Shift+P", run: () => spy("view.palette") },
  ];

  it("opens menus by click and Alt mnemonic, navigates with arrows, and runs items", async () => {
    const spy = vi.fn();
    const { container } = render(<MenuBar commands={makeCommands(spy)} />);
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["File", "Edit", "Selection", "View", "Go", "Run", "Help"]);
    fireEvent.mouseDown(screen.getByRole("menuitem", { name: "File" }));
    expect(screen.getByRole("menu", { name: "File" })).toBeTruthy();
    expect(container.querySelector(".menu-separator")).toBeTruthy();
    fireEvent.click(screen.getByRole("menuitem", { name: /Open project/ }));
    expect(spy).toHaveBeenCalledWith("file.open");

    fireEvent.keyDown(window, { key: "f", altKey: true });
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(spy).toHaveBeenCalledWith("recent");

    fireEvent.keyDown(window, { key: "e", altKey: true });
    fireEvent.click(screen.getByRole("menuitem", { name: /Find dataset/ }));
    expect(spy).not.toHaveBeenCalledWith("edit.find"); // disabled items do nothing
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("searches commands fuzzily, skips disabled ones, and closes on Escape", async () => {
    const spy = vi.fn();
    const onOpenChange = vi.fn();
    render(<CommandCenter commands={makeCommands(spy)} project={null} open onOpenChange={onOpenChange} />);
    const input = screen.getByRole("combobox", { name: "Search commands" });
    fireEvent.change(input, { target: { value: "opn prj" } });
    expect(screen.getAllByRole("option")[0].textContent).toContain("Open project");
    expect(screen.queryByText(/Find dataset/)).toBeNull(); // disabled commands are hidden
    fireEvent.change(input, { target: { value: "study" } });
    expect(screen.getAllByRole("option")[0].textContent).toContain("Recent project");
    fireEvent.change(input, { target: { value: "opn prj" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(spy).toHaveBeenCalledWith("file.open");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledTimes(2);
  });

  it("matches shortcuts with modifiers", () => {
    const event = (init: KeyboardEventInit) => new KeyboardEvent("keydown", init);
    expect(matchesShortcut(event({ key: "P", ctrlKey: true, shiftKey: true }), "Ctrl+Shift+P")).toBe(true);
    expect(matchesShortcut(event({ key: "p", ctrlKey: true }), "Ctrl+Shift+P")).toBe(false);
    expect(matchesShortcut(event({ key: "=", code: "Equal", ctrlKey: true }), "Ctrl+=")).toBe(true);
    expect(matchesShortcut(event({ key: "F5" }), "F5")).toBe(true);
    expect(fuzzyScore("Toggle left sidebar", "tls")).not.toBeNull();
    expect(fuzzyScore("Toggle left sidebar", "xyz")).toBeNull();
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
