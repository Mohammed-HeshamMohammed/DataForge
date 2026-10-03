import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const calls: { command: string; payload: Record<string, unknown> }[] = [];

vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => false,
  pickDirectory: vi.fn(async () => null),
  call: vi.fn(async (command: string, payload: Record<string, unknown> = {}) => {
    calls.push({ command, payload });
    if (command === "settings.get") return { default_purpose: "internal_analysis" };
    if (command === "job.get") return { id: "job-1", state: "completed", result: { dataset_id: "ds-1", rows: 12 }, events: [] };
    return { job_id: "job-1" };
  }),
}));

import { PublicRecords } from "../features/records/PublicRecords.tsx";

describe("Public Records tab", () => {
  it("starts nothing until the authorization statement is confirmed, then sends the HCAD filters", async () => {
    const navigate = vi.fn();
    render(<PublicRecords navigate={navigate} />);
    const build = screen.getByRole("button", { name: "Build the property list" }) as HTMLButtonElement;
    expect(build.disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Collect the tax sale list" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("checkbox", { name: /I am authorized/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "HCAD files folder" }), { target: { value: "C:\\hcad" } });
    fireEvent.change(screen.getByRole("textbox", { name: /ZIP codes/ }), { target: { value: "77009, 77018" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Owner mails elsewhere" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "trust" }));
    await waitFor(() => expect(build.disabled).toBe(false));
    fireEvent.click(build);

    await waitFor(() => expect(calls.some((entry) => entry.command === "records.hcad")).toBe(true));
    expect(calls.find((entry) => entry.command === "records.hcad")!.payload).toMatchObject({
      policy_acknowledgement: true, purpose: "internal_analysis", data_dir: "C:\\hcad", zip: ["77009", "77018"], property_class: ["A1"],
      absentee: true, out_of_state: false, owner_type: ["trust"],
    });
    fireEvent.click(await screen.findByRole("button", { name: "Open dataset" }));
    expect(navigate).toHaveBeenCalledWith("datasets", { datasetId: "ds-1" });
  });
});
