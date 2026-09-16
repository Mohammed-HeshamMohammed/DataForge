import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { accessibilityViolations } from "./a11y.ts";

const calls: { command: string; payload: Record<string, unknown> }[] = [];
const responses: Record<string, unknown> = {};

vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => false,
  call: vi.fn(async (command: string, payload: Record<string, unknown> = {}) => {
    calls.push({ command, payload });
    if (command in responses) return responses[command];
    throw new Error(`unexpected command ${command}`);
  }),
}));

const { MappingEditor } = await import("../components/MappingEditor.tsx");

beforeEach(() => {
  calls.length = 0;
  Object.assign(responses, {
    "dataset.profile": {
      sensitive_roles: ["phone", "email", "address", "mailing_address", "name", "first_name", "last_name"],
      columns: [
        { name: "Phone", null_rate: 0, distinct_count: 2, sample_values: ["5125550182"], proposed_role: "phone", confidence: "proposed", candidates: ["phone"] },
        { name: "Owner Mailing Address", null_rate: 0.5, distinct_count: 1, sample_values: ["PO Box 1"], proposed_role: null, confidence: "ambiguous", candidates: ["address", "mailing_address"] },
        { name: "Notes", null_rate: 0, distinct_count: 1, sample_values: ["x"], proposed_role: null, confidence: "unmapped", candidates: [] },
      ],
    },
    "dataset.mapping": null,
    "dataset.mapping_flags": [{ id: "f1", column_name: "Phone", note: "shared office line" }],
    "dataset.confirm_mapping": { version: 1 },
  });
});

describe("mapping editor", () => {
  it("masks sensitive samples, requires confirming ambiguous columns, and saves export exclusions", async () => {
    const onSaved = vi.fn();
    const { container } = render(<MappingEditor datasetId="d1" onSaved={onSaved} />);
    await screen.findByText("Owner Mailing Address");

    expect(screen.queryByText("5125550182")).toBeNull(); // masked until revealed
    expect(screen.getByText(/shared office line/)).toBeTruthy(); // review report is visible
    const save = screen.getByRole("button", { name: "Save mapping" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByText(/need your confirmation: Owner Mailing Address/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Role for Owner Mailing Address"), { target: { value: "mailing_address" } });
    fireEvent.click(screen.getAllByRole("checkbox", { name: /Exclude/ })[2]);
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(1));
    const saved = calls.find((c) => c.command === "dataset.confirm_mapping")!;
    expect(saved.payload.mapping).toEqual({ Phone: "phone", "Owner Mailing Address": "mailing_address", Notes: "other" });
    expect(saved.payload.export_exclude).toEqual(["Notes"]);

    fireEvent.click(screen.getByRole("checkbox", { name: /Reveal sample values/ }));
    expect(screen.getByText("5125550182")).toBeTruthy();
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("blocks saving when a positional role is pooled or no evidence field is mapped", async () => {
    render(<MappingEditor datasetId="d1" />);
    await screen.findByText("Owner Mailing Address");
    fireEvent.change(screen.getByLabelText("Role for Phone"), { target: { value: "other" } });
    expect(await screen.findByText(/names alone cannot match safely/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Role for Owner Mailing Address"), { target: { value: "address" } });
    fireEvent.change(screen.getByLabelText("Role for Notes"), { target: { value: "address" } });
    expect(await screen.findByText(/"address" is used by Owner Mailing Address, Notes/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save mapping" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
