// @vitest-environment-options {"url": "https://www.zillow.com/houston-tx/"}
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

type Row = Record<string, string>;
type Bridge = {
  listings(): { records: Row[]; on_page: number; from_browsing: number; map_pins: number; reported_total: number | null; error: string | null };
  details(hint?: unknown): { kind: string; record: Row; error: string | null };
};

let bridge: Bridge;
const originalFetch = window.fetch;
let response: unknown = {};

const home = (zpid: number, extra: Record<string, unknown> = {}) => ({
  zpid: String(zpid), unformattedPrice: 300000 + zpid, addressStreet: `${zpid} Elm St`, addressCity: "Houston", addressState: "TX", addressZipcode: "77007",
  beds: 3, baths: 2, area: 1500, detailUrl: `/homedetails/${zpid}-Elm-St-Houston-TX-77007/${zpid}_zpid/`, statusText: "House for sale",
  brokerName: "Acme Realty", latLong: { latitude: 29.77, longitude: -95.4 }, imgSrc: `https://photos.example/${zpid}.jpg`,
  hdpData: { homeInfo: { zpid, homeType: "SINGLE_FAMILY", zestimate: 310000 + zpid, rentZestimate: 2100, taxAssessedValue: 250000, daysOnZillow: 4, lotAreaValue: 0.15, lotAreaUnit: "acres" } },
  agentName: "Jane Agent", ...extra,
});

beforeAll(() => {
  window.fetch = (async () => ({
    url: location.origin + "/async-create-search-page-state",
    headers: { get: (name: string) => (name === "content-type" ? "application/json" : null) },
    clone: () => ({ text: async () => JSON.stringify(response) }),
  })) as typeof window.fetch;
  const scope = globalThis as { CSS?: { escape?: (v: string) => string } };
  if (!scope.CSS?.escape) scope.CSS = { escape: (v: string) => v };
  new Function(readFileSync(resolve(process.cwd(), "src-tauri/src/studio.js"), "utf8"))();
  bridge = (window as unknown as { __dataforgeStudio: Bridge }).__dataforgeStudio;
});

afterAll(() => {
  window.fetch = originalFetch;
});

const setNextData = (data: unknown) => {
  document.body.innerHTML = `<script id="__NEXT_DATA__" type="application/json">${JSON.stringify(data)}</script>`;
};

describe("Zillow collector", () => {
  it("reads list results and map pins with Zillow's own fields and the reported total", () => {
    setNextData({ props: { pageProps: { searchPageState: { cat1: {
      searchResults: { listResults: [home(1), home(2)], mapResults: [home(2), home(3), home(4)] },
      searchList: { totalResultCount: 1234 },
    } } } } });
    const result = bridge.listings();
    expect(result.error).toBeNull();
    expect(result.records).toHaveLength(4);
    expect(result.reported_total).toBe(1234);
    expect(result.map_pins).toBe(2);
    expect(result.records[0]).toMatchObject({
      listing_id: "1", address: "1 Elm St", city: "Houston", zip: "77007", price: "300001", property_type: "Single family", zestimate: "310001",
      rent_zestimate: "2100", tax_assessed_value: "250000", days_on_market: "4", lot_size: "0.15 acres", collected_from: "page", position: "1",
      listing_url: "https://www.zillow.com/homedetails/1-Elm-St-Houston-TX-77007/1_zpid/",
    });
    expect(result.records[3]).toMatchObject({ listing_id: "4", collected_from: "map" });
    expect(JSON.stringify(result.records)).not.toMatch(/Jane Agent/);
  });

  it("keeps adding map pins the page loads as the user moves the map", async () => {
    response = { cat1: { searchResults: { mapResults: [home(10), home(11), home(12)] }, searchList: { totalResultCount: 5000 } } };
    await window.fetch("/async-create-search-page-state");
    await new Promise((done) => setTimeout(done, 0));
    const result = bridge.listings();
    expect(result.from_browsing).toBe(3);
    expect(result.records.filter((row) => row.collected_from === "map")).toHaveLength(5);
    expect(result.reported_total).toBe(1234); // the open page's own count wins over an earlier map move
  });

  it("reads a home page's facts, histories, schools, and photos without agent details", () => {
    const property = {
      zpid: 77, streetAddress: "77 Oak Ln", city: "Houston", state: "TX", zipcode: "77018", price: 455000, bedrooms: 4, bathrooms: 3, livingArea: 2400,
      homeStatus: "FOR_SALE", homeType: "SINGLE_FAMILY", yearBuilt: 1998, monthlyHoaFee: 50, zestimate: 460000,
      description: "Lovely home. Call Jane at 713-555-0100.",
      resoFacts: { heating: ["Central", "Gas"], hasGarage: true, agentName: "Jane", atAGlanceFacts: [{ factLabel: "Parking", factValue: "2 spaces" }] },
      attributionInfo: { brokerName: "Acme Realty", agentName: "Jane Agent", agentPhoneNumber: "713-555-0100", mlsId: "12345678", mlsName: "HAR" },
      priceHistory: [{ date: "2026-09-01", event: "Listed for sale", price: 455000 }],
      taxHistory: [{ time: 1704067200000, taxPaid: 9100, value: 400000 }],
      schools: [{ name: "Oak Elementary", grades: "K-5", distance: 0.4, rating: 8 }],
      responsivePhotos: [{ mixedSources: { jpeg: [{ url: "https://photos.example/77-small.jpg", width: 384 }, { url: "https://photos.example/77-big.jpg", width: 1536 }] } }],
    };
    setNextData({ props: { pageProps: { componentProps: { gdpClientCache: JSON.stringify({ query: { property } }) } } } });
    const { kind, record, error } = bridge.details({ url: location.href });
    expect(error).toBeNull();
    expect(kind).toBe("listings");
    expect(record).toMatchObject({
      listing_id: "77", address: "77 Oak Ln", year_built: "1998", hoa_fee: "50", mls_number: "12345678", mls_name: "HAR", brokerage: "Acme Realty",
      price_history: "2026-09-01 | Listed for sale | 455000", tax_history: "2024 | tax 9100 | assessed 400000", schools: "Oak Elementary | K-5 | 0.4 mi | 8/10",
      photos: "https://photos.example/77-big.jpg",
    });
    expect(record.facts).toContain("Heating: Central, Gas");
    expect(record.facts).toContain("Parking: 2 spaces");
    expect(record.description).toBe("Lovely home. Call Jane at [phone removed].");
    expect(JSON.stringify(record)).not.toMatch(/Jane Agent|agentName|713-555/);
  });
});
