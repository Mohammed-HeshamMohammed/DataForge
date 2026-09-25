import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { accessibilityViolations } from "./a11y.ts";

const call = vi.fn(async (command: string) => {
  if (command === "project.recent") return [];
  if (command === "project.create_sample") return { id: "sample", name: "Getting Started", root_path: "C:\\DataForge\\Getting Started", created_at: "", schema_version: 1 };
  throw new Error(`unexpected ${command}`);
});

vi.mock("../lib/ipc.ts", () => ({ call, isTauri: () => false, pickDirectory: async () => null }));

describe("project onboarding", () => {
  it("opens an idempotent sample project for a first-time user", async () => {
    const { ProjectGate } = await import("../features/onboarding/ProjectGate.tsx");
    const opened = vi.fn();
    const { container } = render(<ProjectGate onOpen={opened} />);
    fireEvent.click(screen.getByRole("button", { name: "Try the sample project" }));
    await waitFor(() => expect(opened).toHaveBeenCalledWith(expect.objectContaining({ name: "Getting Started" })));
    expect(call).toHaveBeenCalledWith("project.create_sample");
    expect(await accessibilityViolations(container)).toEqual([]);
  });
});
