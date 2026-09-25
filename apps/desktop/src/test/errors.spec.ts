import { describe, expect, it } from "vitest";
import { describeError, toErrorMessage } from "../lib/errors.ts";

describe("user-facing errors", () => {
  it("turns website blocks into a useful next step", () => {
    const result = describeError("CAPTCHA challenge detected");
    expect(result.title).toMatch(/blocked automated collection/i);
    expect(result.nextStep).toMatch(/official API/i);
    expect(result.technical).toBe("CAPTCHA challenge detected");
  });

  it("routes sign-in to the temporary manual Studio session", () => {
    const result = describeError("login is required");
    expect(result.title).toMatch(/requires a sign-in/i);
    expect(result.nextStep).toMatch(/Scrape Studio/i);
    expect(result.nextStep).toMatch(/never reads or stores/i);
  });

  it("preserves technical messages for support", () => {
    expect(describeError("429 rate limit").technical).toBe("429 rate limit");
    expect(toErrorMessage(new Error("broken"))).toBe("broken");
  });
});
