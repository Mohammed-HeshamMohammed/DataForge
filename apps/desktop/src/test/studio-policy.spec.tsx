import { describe, expect, it } from "vitest";
import { isInternalAutomationHost } from "../features/studio/Studio.tsx";

describe("internal browser compatibility boundary", () => {
  it("accepts only loopback, private-network, and internal-test hosts", () => {
    expect(isInternalAutomationHost("localhost")).toBe(true);
    expect(isInternalAutomationHost("service.internal")).toBe(true);
    expect(isInternalAutomationHost("10.12.0.4")).toBe(true);
    expect(isInternalAutomationHost("172.20.1.8")).toBe(true);
    expect(isInternalAutomationHost("192.168.1.20")).toBe(true);
    expect(isInternalAutomationHost("172.40.1.8")).toBe(false);
    expect(isInternalAutomationHost("example.com")).toBe(false);
  });
});
