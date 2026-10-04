import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const dataset = (id: string, name: string, kind = "import") => ({ id, name, kind, source_filename: `${name}.csv`, source_artifact_hash: "x", row_count: 6, column_count: 4, created_at: "2026-10-01T00:00:00Z", mapping_version: 1 });
const SCAN = {
  rows: 6,
  columns: [
    { column: "Phone", role: "phone", label: "Phone numbers", filled: 5, changes: 2, change_examples: [["512.555.0182", "(512) 555-0182"]], invalid: 0, placeholders: 1, problem_examples: { placeholder: ["000-000-0000"] } },
    { column: "Email", role: "email", label: "Email addresses", filled: 5, changes: 1, change_examples: [["John@GMIAL.com", "john@gmail.com"]], invalid: 1, placeholders: 0, problem_examples: { invalid: ["lee@@mail"] } },
  ],
  junk_rows: { empty: 1, exact_duplicates: 0, test: 1, examples: { test: [] } },
  clusters: [{ column: "Company", method: "company", rows: 4, values: [{ value: "ACME INC", rows: 2 }, { value: "Acme, Inc.", rows: 1 }, { value: "Acme Incorporated", rows: 1 }], suggested: "ACME INC" }],
};

beforeEach(() => {
  calls.length = 0;
  responses = {
    "dataset.list": [dataset("d1", "people")],
    "job.list": [],
    "dataset.profile": { columns: [] },
    "dataset.mapping": null,
    "dataset.cleanup_scan": SCAN,
    "dataset.cleanup_apply": { job_id: "c1" },
    "job.get": { id: "c1", kind: "dataset_cleanup", state: "completed", created_at: "", updated_at: "", params: {}, result: { dataset_id: "d2", rows: 4 }, error: null, events: [] },
  };
});

describe("Fix values step", () => {
  it("suggests fixes, lets the person adjust them, and continues with the cleaned copy", async () => {
    const { container } = render(<MatchTab initialDatasetId="d1" />);
    const step = await screen.findByRole("button", { name: /Fix values/ });
    await waitFor(() => expect((step as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(step);

    expect(await screen.findByRole("heading", { name: "Fix values before matching" })).toBeTruthy();
    expect(screen.getByText("512.555.0182")).toBeTruthy();
    const standardizePhones = screen.getByRole("checkbox", { name: "Standardize 2 values" });
    expect((standardizePhones as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Clear 1 placeholder value" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Clear 1 invalid value" }) as HTMLInputElement).checked).toBe(false); // invalid values are kept unless chosen
    expect((screen.getByRole("checkbox", { name: /Remove 1 test entries/ }) as HTMLInputElement).checked).toBe(true);
    expect(await accessibilityViolations(container)).toEqual([]);

    fireEvent.click(screen.getByRole("checkbox", { name: /Remove 1 empty rows/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Value to keep for Company" }), { target: { value: "Acme Inc." } });
    responses["dataset.list"] = [dataset("d2", "people (cleaned)", "cleaned"), dataset("d1", "people")];
    fireEvent.click(screen.getByRole("button", { name: "Save cleaned copy and continue" }));

    await waitFor(() => expect(calls.some((c) => c.command === "dataset.cleanup_apply")).toBe(true));
    const plan = calls.find((c) => c.command === "dataset.cleanup_apply")!.payload.plan as Record<string, unknown>;
    expect(plan).toEqual({
      standardize: ["Phone", "Email"],
      clear_invalid: ["Phone"],
      merge_values: [{ column: "Company", values: ["ACME INC", "Acme, Inc.", "Acme Incorporated"], to: "Acme Inc." }],
      drop_rows: ["test"],
    });
    expect(await screen.findByText(/people \(cleaned\) — 6 rows selected/)).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Check likely duplicates" })).toBeTruthy();
  });

  it("lets the person skip cleanup", async () => {
    responses["dataset.cleanup_scan"] = { rows: 6, columns: [], junk_rows: { empty: 0, exact_duplicates: 0, test: 0, examples: { test: [] } }, clusters: [] };
    render(<MatchTab initialDatasetId="d1" />);
    const step = await screen.findByRole("button", { name: /Fix values/ });
    await waitFor(() => expect((step as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(step);
    expect(await screen.findByText(/There is nothing to fix/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Check likely duplicates" })).toBeTruthy();
    expect(calls.some((c) => c.command === "dataset.cleanup_apply")).toBe(false);
  });
});
