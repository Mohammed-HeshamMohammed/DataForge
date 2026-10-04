// @vitest-environment-options {"url": "https://www.zillow.com/homes/"}
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { call } from "../lib/ipc.ts";
import { pageDataList } from "../features/studio/Studio.tsx";

type Captured = { responses: Array<{ url: string; data: unknown }>; error: string | null };
type Bridge = { networkData(): Captured };

let bridge: Bridge;
const originalFetch = window.fetch;
let body: unknown = null;
const send = async (path: string, data: unknown) => {
  body = data;
  await window.fetch(path);
  await new Promise((done) => setTimeout(done, 0));
};

// A Zillow search response is about 2 KB per home, so 500 map pins make it larger than 1 MB.
const home = (n: number) => ({
  zpid: String(1000 + n), price: `$${400 + n},000`, address: `${n} NE Briarwood Ct, Ankeny, IA 50021`, beds: 4, baths: 4, area: 2216,
  detailUrl: `https://www.zillow.com/homedetails/${n}-NE-Briarwood-Ct-Ankeny-IA-50021/${1000 + n}_zpid/`, statusText: "House for sale",
  imgSrc: `https://photos.zillowstatic.com/fp/${"a".repeat(32)}-p_e.jpg`, brokerName: "LPT REALTY, LLC", latLong: { latitude: 41.7, longitude: -93.6 },
  hdpData: { homeInfo: { zpid: 1000 + n, homeType: "SINGLE_FAMILY", zestimate: 410000, daysOnZillow: 3, lotAreaValue: 0.3, lotAreaUnit: "acres" } },
  carouselPhotos: Array.from({ length: 16 }, (_, photo) => ({ url: `https://photos.zillowstatic.com/fp/${photo}${"b".repeat(40)}-p_e.jpg` })),
});
const searchState = (homes: number) => ({
  regionState: { regionInfo: [{ regionType: 2, regionId: 16, regionName: "Iowa", isPointRegion: false }] },
  cat1: {
    searchResults: { listResults: Array.from({ length: 41 }, (_, n) => home(n)), mapResults: Array.from({ length: homes }, (_, n) => home(n)) },
    searchList: { totalResultCount: 3293, totalPages: 20 },
  },
  categoryTotals: { cat2: { totalResultCount: 9999 } },
});
// Another JSON the page loads: nearby regions with only a name and a flag (what the old detection showed as the fields).
const regions = { regions: Array.from({ length: 48 }, (_, n) => ({ regionName: `Region ${n}`, isPointRegion: false })) };

vi.mock("../lib/ipc.ts", () => ({
  isTauri: () => true,
  call: vi.fn(async (command: string) => {
    if (command === "preset.list") return [{
      id: "generic.html_list", version: "1.1.0", display_name: "Visual page list", status: "active", errors: [],
      strategy: { preferred: "webview", allowed: ["webview"] }, request_limits: { max_pages_default: 10, max_records_default: 500, min_delay_ms: 1000 },
      url_scope: { allowed_hosts: [], user_supplied_host: true }, extraction: { mode: "selectors", fields: [] }, validation: {}, pagination: { type: "none" },
    }];
    if (command === "scrape.site_catalog") return [];
    if (command === "settings.get") return { default_purpose: "internal_analysis" };
    if (command === "scrape.check_url") return { allowed: true, reason: null, skippable: false };
    if (command === "scrape.detect_url") return { source: "website", preset_id: "generic.html_list", preset_version: "1.1.0", confidence: "medium", reason: "Rendered page", content_type: "text/html", created_preset: false };
    if (command === "preset.validate") return { errors: [] };
    if (command === "preset.save_custom") return {};
    if (command === "scrape.stage_rendered") return { job_id: "job-1" };
    throw new Error(`unexpected ${command}`);
  }),
}));

vi.mock("../lib/desktop.ts", () => ({
  copyText: vi.fn(async () => {}),
  getZoom: () => 1,
  studioHost: {
    // Page data comes from the real bridge script, so capture and detection are tested together.
    call: vi.fn(async (action: string) => {
      if (action === "networkData") return bridge.networkData();
      if (action === "pageInfo") return { url: "https://www.zillow.com/homes/", title: "Homes", ready_state: "complete", challenge_detected: false, password_fields: 0, inaccessible_frames: 0 };
      return {};
    }),
    close: vi.fn(async () => {}),
    control: vi.fn(async () => {}),
    navigate: vi.fn(async () => {}),
    onEvent: vi.fn(async () => () => {}),
    open: vi.fn(async () => {}),
    setBounds: vi.fn(async () => {}),
    setVisible: vi.fn(async () => {}),
  },
  loadSetting: (_key: string, fallback: unknown) => fallback,
  saveSetting: () => {},
}));

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  window.fetch = (async (path: string) => ({
    url: location.origin + path,
    headers: { get: (name: string) => (name === "content-type" ? "application/json" : null) },
    clone: () => ({ text: async () => JSON.stringify(body) }),
  })) as typeof window.fetch;
  new Function(readFileSync(resolve(process.cwd(), "src-tauri/src/studio.js"), "utf8"))();
  bridge = (window as unknown as { __dataforgeStudio: Bridge }).__dataforgeStudio;
});

afterAll(() => {
  window.fetch = originalFetch;
});

describe("page data capture in the Studio bridge", () => {
  it("keeps a search response larger than 1 MB and skips JSON that holds no list of records", async () => {
    await send("/zg-graph", regions);
    await send("/beacon", { ok: true });
    expect(JSON.stringify(searchState(500)).length).toBeGreaterThan(1_000_000);
    await send("/async-create-search-page-state?searchQueryState=x", searchState(500));
    expect(bridge.networkData().responses.map((response) => response.url)).toEqual([
      "https://www.zillow.com/zg-graph",
      "https://www.zillow.com/async-create-search-page-state",
    ]);
  });

  it("drops the oldest responses first when the buffer is full", async () => {
    for (let n = 0; n < 60; n++) await send(`/api/page-${n}`, { items: [{ id: n }] });
    const urls = bridge.networkData().responses.map((response) => response.url);
    expect(urls).toHaveLength(50);
    expect(urls.at(-1)).toBe("https://www.zillow.com/api/page-59");
    expect(urls).not.toContain("https://www.zillow.com/zg-graph");
  });

  it("includes JSON the page shipped inline, such as Next.js __NEXT_DATA__", () => {
    document.body.innerHTML = `<script id="__NEXT_DATA__" type="application/json">${JSON.stringify({ props: { pageProps: { searchPageState: searchState(3) } } })}</script>`;
    const [first] = bridge.networkData().responses;
    expect(first.url).toBe("https://www.zillow.com/homes/#__NEXT_DATA__");
    const list = pageDataList([first])!;
    expect(list.path).toBe("props.pageProps.searchPageState.cat1.searchResults.listResults");
    expect(list.total).toBe(3293);
    document.body.innerHTML = "";
  });
});

describe("choosing the page's list of records", () => {
  const url = "https://www.zillow.com/async-create-search-page-state";

  it("reads one list of listings with flattened fields and the total the site reports", () => {
    const list = pageDataList([{ url: "https://www.zillow.com/zg-graph", data: regions }, { url, data: searchState(500) }])!;
    expect(list.path).toBe("cat1.searchResults.mapResults");
    expect(list.records).toHaveLength(500);
    expect(list.total).toBe(3293);
    expect(list.records[0]).toMatchObject({ zpid: "1000", price: "$400,000", beds: "4", "latLong.latitude": "41.7", "hdpData.homeInfo.zestimate": "410000" });
    const paths = list.fields.map((field) => field.path);
    expect(paths).not.toContain("regionName");
    expect(paths).not.toContain("carouselPhotos");
    expect(list.records.every((record) => !("regionName" in record))).toBe(true);
  });

  it("merges the same list from later responses without duplicates", () => {
    const later = { cat1: { searchResults: { mapResults: [home(1), home(600)] }, searchList: { totalResultCount: 3293 } } };
    const list = pageDataList([{ url, data: searchState(50) }, { url, data: later }])!;
    expect(list.path).toBe("cat1.searchResults.mapResults");
    expect(list.records).toHaveLength(51);
    expect(list.records.at(-1)!.zpid).toBe("1600");
  });

  it("uses a count only when it is plausible for the list", () => {
    expect(pageDataList([{ url, data: { results: [{ title: "a" }, { title: "b" }], count: 120 } }])!.total).toBe(120);
    expect(pageDataList([{ url, data: { hits: { total: { value: 75 }, hits: [{ title: "a" }, { title: "b" }] } } }])!.total).toBe(75);
    expect(pageDataList([{ url, data: { results: [{ title: "a" }, { title: "b" }], total: 1 } }])!.total).toBeNull();
  });
});

describe("Find data already loaded by the page", () => {
  it("selects the listings, shows loaded versus reported results, and stages records by field", async () => {
    await send("/zg-graph", regions);
    await send("/async-create-search-page-state", searchState(500));
    const { Studio } = await import("../features/studio/Studio.tsx");
    render(<Studio navigate={() => {}} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search or page address" }), { target: { value: "https://www.zillow.com/homes/" } });
    const go = screen.getByRole("button", { name: "Go" });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(go);
    fireEvent.click(await screen.findByRole("button", { name: "Find data already loaded by the page" }));

    expect(await screen.findByText("Page data selected")).toBeTruthy();
    expect(screen.getByText("500 of 3,293 results loaded")).toBeTruthy();
    expect(screen.getByText(/Found 500 of the 3,293 results the site reports in page JSON \(cat1\.searchResults\.mapResults\)/)).toBeTruthy();
    const chips = within(screen.getByLabelText("Selected fields"));
    for (const key of ["zpid", "price", "address", "lat_long_latitude", "hdp_data_home_info_zestimate"]) expect(chips.getByText(key)).toBeTruthy();
    expect(chips.queryByText("region_name")).toBeNull();

    fireEvent.click(screen.getByRole("checkbox", { name: /I am authorized/ }));
    fireEvent.click(screen.getByRole("button", { name: "Preview 10 records" }));
    await waitFor(() => expect(vi.mocked(call)).toHaveBeenCalledWith("scrape.stage_rendered", expect.anything()));
    const staged = vi.mocked(call).mock.calls.find(([command]) => command === "scrape.stage_rendered")![1] as { pages: { records: Record<string, string>[] }[] };
    expect(staged.pages[0].records).toHaveLength(10);
    expect(staged.pages[0].records[0]).toMatchObject({ zpid: "1000", price: "$400,000", lat_long_latitude: "41.7", hdp_data_home_info_zestimate: "410000" });
  });
});
