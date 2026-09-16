import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { accessibilityViolations } from "./a11y.ts";

const calls: { command: string; payload: Record<string, unknown> }[] = [];
let responses: Record<string, unknown | ((payload: Record<string, unknown>) => unknown)> = {};

vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => false,
  call: vi.fn(async (command: string, payload: Record<string, unknown> = {}) => {
    calls.push({ command, payload });
    const response = responses[command];
    if (response === undefined) throw new Error(`unexpected command ${command}`);
    return typeof response === "function" ? (response as (p: Record<string, unknown>) => unknown)(payload) : response;
  }),
}));

const { MatchTab } = await import("../features/matching/MatchTab.tsx");

const row = (id: string, name: string, phone: string) => ({ id, row_number: Number(id.slice(1)), raw: { Name: name, Phone: phone }, source: "d1", source_name: "people" });
const evidence = (strength: string, field = "phone") => ({ field, similarity: strength === "guard" ? 0 : 1, result: strength === "guard" ? "conflict" : "exact", strength, explanation: strength === "guard" ? "Different identifiers in APN" : "Exact phone match" });

function queue(items: unknown[]) {
  return { total: items.length, offset: 0, order: "score", sensitive_columns: ["Phone"], ranking_model: null, items };
}

beforeEach(() => {
  calls.length = 0;
  responses = {
    "dataset.list": [{ id: "d1", name: "people", kind: "import", source_filename: "p.csv", source_artifact_hash: "x", row_count: 4, column_count: 2, created_at: "2026-09-15T00:00:00Z", mapping_version: 1 }],
    "job.list": [],
    "job.get": { id: "j1", kind: "match", state: "completed", created_at: "", updated_at: "", params: { dataset_id: "d1", run_mode: "full", mapping_version: 1 }, result: {}, error: null, events: [] },
    "match.results": {
      job_id: "j1", dataset_id: "d1", compare_dataset_id: null, run_mode: "full", policy_version: "p", mapping_version_id: "m1", metrics: { input_rows: 4, candidate_pairs: 2 },
      decisions: { possible_match: 2 }, pending_review: 2, reviewed: {}, canonical_records: 4, merged_groups: 0, rows_suppressed: 0, samples: [], exports: [],
      review_turnaround: { decisions: 0, median_seconds: null }, mapping_flags: [],
    },
    "match.review_queue": queue([
      { decision_id: "dA", score: 0.9, reason: "Needs review", review_version: 0, evidence: [evidence("strong")], left: row("r2", "Grace Hopper", "2125550100"), right: row("r3", "G. Hopper", "2125550100"), can_merge: true, model_score: null },
      { decision_id: "dB", score: 0.8, reason: "Needs review", review_version: 3, evidence: [evidence("guard", "identifier")], left: row("r4", "Ada", "5125550182"), right: row("r5", "Ada", "5125550182"), can_merge: false, model_score: null },
    ]),
    "match.submit_review": { review_action_id: "ra1", review_version: 1 },
  };
});

describe("review queue", () => {
  it("defaults focus to keep separate, masks sensitive values, and merges with chosen values", async () => {
    const { container } = render(<MatchTab initialDatasetId="d1" initialJobId="j1" />);
    await screen.findByRole("heading", { name: "Review matches" });

    expect(document.activeElement?.textContent).toMatch(/^Keep separate/);
    const table = screen.getByRole("table", { name: /Record A compared with record B/ });
    expect(within(table).queryByText("2125550100")).toBeNull();
    expect(within(table).getByText(/people, row 2/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^Choose values/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Use record B for Name" }));
    fireEvent.click(screen.getByRole("button", { name: /^Merge/ }));
    await waitFor(() => expect(calls.some((c) => c.command === "match.submit_review")).toBe(true));
    const submitted = calls.find((c) => c.command === "match.submit_review")!.payload;
    expect(submitted).toMatchObject({ decision_id: "dA", action: "merge", expected_version: 0, values: { Name: "r3" } });
    expect(await screen.findByText(/Merged\. Canonical records updated\./)).toBeTruthy();
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("never enables merge for a guarded pair and supports keyboard shortcuts", async () => {
    render(<MatchTab initialDatasetId="d1" initialJobId="j1" />);
    await screen.findByRole("heading", { name: "Review matches" });
    fireEvent.keyDown(window, { key: "j" });
    expect(await screen.findByText("Cannot auto-merge", { exact: false })).toBeTruthy();
    expect((screen.getByRole("button", { name: /^Merge/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /^Choose values/ })).toBeNull();

    fireEvent.keyDown(window, { key: "m" });
    expect(calls.some((c) => c.command === "match.submit_review")).toBe(false);
    fireEvent.keyDown(window, { key: "s" });
    await waitFor(() => expect(calls.find((c) => c.command === "match.submit_review")?.payload).toMatchObject({ decision_id: "dB", action: "keep_separate", expected_version: 3 }));
  });

  it("reports a bad mapping for a field without resolving the pair", async () => {
    responses["match.flag_mapping"] = { flag_id: "f1", dataset_id: "d1" };
    render(<MatchTab initialDatasetId="d1" initialJobId="j1" />);
    await screen.findByRole("heading", { name: "Review matches" });
    fireEvent.click(screen.getByText("More actions"));
    fireEvent.change(screen.getByRole("combobox", { name: "Field" }), { target: { value: "Name" } });
    fireEvent.change(screen.getByRole("textbox", { name: "What is wrong" }), { target: { value: "nicknames" } });
    fireEvent.click(screen.getByRole("button", { name: "Mark bad mapping" }));
    await waitFor(() => expect(calls.find((c) => c.command === "match.flag_mapping")?.payload).toMatchObject({ column: "Name", note: "nicknames", decision_id: "dA" }));
    expect(calls.some((c) => c.command === "match.submit_review")).toBe(false);
    expect(await screen.findByRole("button", { name: "Fix mapping" })).toBeTruthy();
  });
});
