import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ListingCollector, collectorKind, mergedRows, rowKey, withDetails, zillowZipPages } from "../features/studio/ListingCollector.tsx";

const preset = { id: "generic.listings", version: "1.0.0", request_limits: { max_pages_default: 50, min_delay_ms: 8000 } };

describe("Scrape Studio built-in collector", () => {
  it("reads products on Amazon and listings everywhere else", () => {
    expect(collectorKind("https://www.amazon.eg/s?k=kettle")).toBe("products");
    expect(collectorKind("https://www.zillow.com/houston-tx/")).toBe("listings");
    expect(collectorKind("https://www.har.com/houston/realestate/for_sale")).toBe("listings");
    expect(collectorKind(null)).toBe("listings");
  });

  it("keys rows by link, then id, then address, and merges pages without losing filled values", () => {
    expect(rowKey({ listing_url: "https://x.test/1", listing_id: "9" }, "listings")).toBe("https://x.test/1");
    expect(rowKey({ address: "1 Main St", zip: "77007" }, "listings")).toBe("1 Main St|77007");
    expect(rowKey({ asin: "B0TEST0001" }, "products")).toBe("B0TEST0001");
    const rows = mergedRows({
      "https://x.test/p1": [{ listing_url: "https://x.test/1", price: "100", beds: "3" }, { listing_url: "https://x.test/2", price: "200" }],
      "https://x.test/p2": [{ listing_url: "https://x.test/2", price: "200", baths: "2" }, { price: "50" }],
    }, "listings");
    expect(rows).toHaveLength(2);
    expect(rows[1]).toEqual({ listing_url: "https://x.test/2", price: "200", baths: "2" });
  });

  it("builds Zillow ZIP pages from the ZIP codes already collected", () => {
    const rows = [{ zip: "77007" }, { zip: "77002-1234" }, { zip: "77007" }, { zip: "" }];
    expect(zillowZipPages("https://www.zillow.com/houston-tx/2_p/", rows)).toEqual(["https://www.zillow.com/houston-tx-77002/", "https://www.zillow.com/houston-tx-77007/"]);
    expect(zillowZipPages("https://www.zillow.com/houston-tx-77007/", rows)).toHaveLength(2);
    expect(zillowZipPages("https://www.zillow.com/homedetails/1-Elm/1_zpid/", rows)).toEqual([]);
  });

  it("merges detail-page fields into the row they belong to on any page", () => {
    const pages = { p1: [{ listing_url: "u1", price: "1" }], p2: [{ listing_url: "u2", price: "2" }] };
    const next = withDetails(pages, "listings", "u2", { year_built: "1990", price: "", facts: "Heating: Gas" });
    expect(next.p2[0]).toEqual({ listing_url: "u2", price: "2", year_built: "1990", facts: "Heating: Gas" });
    expect(next.p1[0]).toEqual({ listing_url: "u1", price: "1" });
  });

  it("asks for the authorization statement and a purpose before collecting", () => {
    render(<ListingCollector scopeUrl="https://www.har.com/houston/realestate/for_sale" pageReady={false} acknowledged={false} purpose="" presets={[preset]} onStaged={() => {}} />);
    expect(screen.getByText("Collect listings from har.com")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Collect listings" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Add this page" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Confirm the authorization statement/)).toBeTruthy();
  });

  it("only reads pages the user opens on sites whose terms forbid automated browsing", () => {
    render(<ListingCollector scopeUrl="https://houston.craigslist.org/search/apa" pageReady acknowledged purpose="internal_analysis" presets={[preset]} onStaged={() => {}} />);
    expect(screen.queryByRole("button", { name: "Collect listings" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add this page" })).toBeTruthy();
    expect(screen.getByText(/terms do not allow automated browsing/)).toBeTruthy();
  });
});
