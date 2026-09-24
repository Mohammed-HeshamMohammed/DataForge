import { describe, expect, it } from "vitest";
import { itemPageMarks } from "../features/studio/Studio.tsx";

describe("background item page marks", () => {
  const links = ["https://shop.test/dp/1", "https://shop.test/dp/2", "https://shop.test/dp/3", "https://shop.test/dp/4"];

  it("marks read cards, the one being read, and those still waiting", () => {
    const { marks, status } = itemPageMarks(links, [{ url: "https://shop.test/dp/1#top", status: "done", fields: 42, fetch_ms: 180, parse_ms: 9 }], true);
    expect(marks).toEqual({ [links[0]]: "done", [links[1]]: "working", [links[2]]: "pending", [links[3]]: "pending" });
    expect(status).toBe("Reading item pages in the background: 1 of 4 · last page: 42 fields, 180 ms download + 9 ms reading");
  });

  it("summarizes the outcome when the job ends and leaves unread cards unmarked", () => {
    const { marks, status } = itemPageMarks(
      links,
      [
        { url: links[0], status: "done", fields: 40, fetch_ms: 120, parse_ms: 7 },
        { url: links[1], status: "reused", fields: 40 },
        { url: links[2], status: "failed", reason: "HTTP 404" },
      ],
      false,
    );
    expect(marks).toEqual({ [links[0]]: "done", [links[1]]: "done", [links[2]]: "failed" });
    expect(status).toBe("Item pages read: 2 of 4 · 1 failed · last page: 40 fields, 120 ms download + 7 ms reading");
  });

  it("treats skipped and stopped pages as skipped", () => {
    const { marks } = itemPageMarks(links.slice(0, 2), [{ url: links[0], status: "skipped" }, { url: links[1], status: "stopped" }], false);
    expect(Object.values(marks)).toEqual(["skipped", "skipped"]);
  });
});
