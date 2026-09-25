import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CustomSelect } from "../components/CustomSelect.tsx";
import { accessibilityViolations } from "./a11y.ts";

describe("custom select", () => {
  it("supports search, keyboard focus, selection, and accessible listbox semantics", async () => {
    const onChange = vi.fn();
    render(
      <label className="field">
        <span>Website</span>
        <CustomSelect
          value=""
          onChange={onChange}
          searchable
          searchPlaceholder="Search websites"
          options={[
            { value: "amazon", label: "Amazon", group: "Marketplace" },
            { value: "github", label: "GitHub", group: "Developer", description: "github.com" },
          ]}
        />
      </label>,
    );

    const trigger = screen.getByRole("combobox", { name: "Website" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const search = screen.getByRole("searchbox", { name: "Search websites" });
    await waitFor(() => expect(document.activeElement).toBe(search));
    fireEvent.change(search, { target: { value: "git" } });
    expect(screen.queryByRole("option", { name: "Amazon" })).toBeNull();
    fireEvent.keyDown(search, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("github");
    expect(await accessibilityViolations(document.body)).toEqual([]);
  });
});
