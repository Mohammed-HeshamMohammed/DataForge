import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { accessibilityViolations } from "./a11y.ts";

const siteCatalog = [
  { site_id: "amazon", site_name: "Amazon", site_category: "marketplace", domains: ["amazon.com"], recommended_method: "visual_studio", suggested_fields: ["title", "price"], requires_rendered: true, example_url: "https://www.amazon.com/s?k=laptop" },
  { site_id: "zillow", site_name: "Zillow", site_category: "real_estate", domains: ["zillow.com"], recommended_method: "visual_studio", suggested_fields: ["address", "price"], requires_rendered: true, example_url: "https://www.zillow.com/homes/" },
];
let siteCatalogAvailable = true;
const studioOpen = vi.fn(async () => {});

vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => true,
  call: vi.fn(async (command: string) => {
    if (command === "preset.list") return [{
      id: "generic.html_list", version: "1.1.0", display_name: "Visual page list", status: "active", errors: [],
      strategy: { preferred: "webview", allowed: ["webview"] }, request_limits: { max_pages_default: 10, max_records_default: 500, min_delay_ms: 1000 },
      url_scope: { allowed_hosts: [], user_supplied_host: true }, extraction: { mode: "selectors", fields: [] }, validation: {}, pagination: { type: "none" },
    }];
    if (command === "scrape.site_catalog") {
      if (!siteCatalogAvailable) throw new Error("unknown command scrape.site_catalog");
      return siteCatalog;
    }
    if (command === "settings.get") return { default_purpose: "internal_analysis" };
    if (command === "scrape.check_url") return { allowed: true, reason: null, skippable: false };
    if (command === "scrape.detect_url") return {
      source: "website", preset_id: "generic.html_list", preset_version: "1.1.0", confidence: "high",
      reason: "Known supported site", content_type: "text/html", created_preset: false, requires_rendered: true,
      site_name: "Supported website", site_category: "marketplace", suggested_fields: ["title", "price"], recommended_method: "visual_studio",
    };
    throw new Error(`unexpected ${command}`);
  }),
}));

vi.mock("../lib/desktop.ts", () => ({
  copyText: vi.fn(async () => {}),
  getZoom: () => 1,
  loadSetting: <T,>(_key: string, fallback: T) => fallback,
  saveSetting: vi.fn(),
  studioHost: {
    call: vi.fn(async () => ({})),
    close: vi.fn(async () => {}),
    control: vi.fn(async () => {}),
    navigate: vi.fn(async () => {}),
    onEvent: vi.fn(async () => () => {}),
    open: studioOpen,
    setBounds: vi.fn(async () => {}),
    setVisible: vi.fn(async () => {}),
  },
}));

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

beforeEach(() => {
  siteCatalogAvailable = true;
  studioOpen.mockClear();
});

describe("Scrape Studio supported websites", () => {
  it("opens and analyzes a selected website immediately", async () => {
    const { Studio } = await import("../features/studio/Studio.tsx");
    const { container } = render(<Studio navigate={() => {}} />);
    const picker = await screen.findByRole("combobox", { name: "Start with a supported website" });
    await waitFor(() => expect((picker as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(picker);
    fireEvent.change(screen.getByRole("searchbox", { name: /Search Amazon Egypt/ }), { target: { value: "zillow" } });
    fireEvent.click(screen.getByRole("option", { name: /Zillow/ }));

    expect((screen.getByRole("textbox", { name: "Search or page address" }) as HTMLInputElement).value).toBe("https://www.zillow.com/homes/");
    await waitFor(() => expect(studioOpen).toHaveBeenCalledWith("https://www.zillow.com/homes/", ["www.zillow.com"], expect.any(Object)));
    expect(await screen.findByText("Supported website")).toBeTruthy();
    await waitFor(() => expect(picker.getAttribute("aria-expanded")).toBe("false"));
    expect(await accessibilityViolations(container)).toEqual([]);
  });

  it("offers regional editions and opens Amazon Egypt", async () => {
    const { Studio } = await import("../features/studio/Studio.tsx");
    render(<Studio navigate={() => {}} />);
    const picker = await screen.findByRole("combobox", { name: "Start with a supported website" });
    await waitFor(() => expect((picker as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(picker);
    fireEvent.change(screen.getByRole("searchbox", { name: /Search Amazon Egypt/ }), { target: { value: "amazon egypt" } });
    fireEvent.click(screen.getByRole("option", { name: /Amazon — Egypt/ }));

    await waitFor(() => expect(studioOpen).toHaveBeenCalledWith("https://www.amazon.eg/s?k=laptop", ["www.amazon.eg"], expect.any(Object)));
    expect((screen.getByRole("textbox", { name: "Search or page address" }) as HTMLInputElement).value).toBe("https://www.amazon.eg/s?k=laptop");
  });

  it("starts on a browser home and exposes session history", async () => {
    const { Studio } = await import("../features/studio/Studio.tsx");
    const { container } = render(<Studio navigate={() => {}} />);
    expect(await screen.findByRole("heading", { name: "Search the web" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Search Google or type a URL" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Speed Dial" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Suggestions" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Add website/ })).toBeTruthy();
    expect(await accessibilityViolations(container)).toEqual([]);
    const history = screen.getByRole("button", { name: "Open browsing history" });
    fireEvent.click(history);
    expect(screen.getByRole("heading", { name: "History" })).toBeTruthy();
    expect(screen.getByText(/No browsing history yet/)).toBeTruthy();
  });

  it("uses the built-in website list when the running service is older", async () => {
    siteCatalogAvailable = false;
    const { Studio } = await import("../features/studio/Studio.tsx");
    render(<Studio navigate={() => {}} />);
    const picker = await screen.findByRole("combobox", { name: "Start with a supported website" });
    await waitFor(() => expect((picker as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(picker);
    expect(screen.getByRole("option", { name: /Amazon — Egypt/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /Zillow/ })).toBeTruthy();
    expect(screen.getByText(/Using the built-in website list/)).toBeTruthy();
  });
});
