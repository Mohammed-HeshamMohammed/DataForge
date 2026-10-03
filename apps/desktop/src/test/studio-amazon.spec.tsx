// @vitest-environment-options {"url": "https://www.amazon.com/s?k=kettle&page=2"}
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";

type Row = Record<string, string>;
type Bridge = {
  products(): { records: Row[]; page: number; last_page: number | null; next_url: string | null; error: string | null };
  details(hint?: unknown): { kind: string; record: Row; error: string | null };
};

let bridge: Bridge;

beforeAll(() => {
  const scope = globalThis as { CSS?: { escape?: (v: string) => string } };
  if (!scope.CSS?.escape) scope.CSS = { escape: (v: string) => v };
  new Function(readFileSync(resolve(process.cwd(), "src-tauri/src/studio.js"), "utf8"))();
  bridge = (window as unknown as { __dataforgeStudio: Bridge }).__dataforgeStudio;
});

describe("Amazon collector", () => {
  it("reads delivery text and stops at the last results page", () => {
    document.body.innerHTML = `
      <div data-component-type="s-search-result" data-asin="B0TEST0003">
        <div data-cy="title-recipe"><h2><span>Steel Kettle</span></h2></div>
        <span class="a-price"><span class="a-offscreen">$19.99</span></span>
        <div data-cy="delivery-recipe"><div class="a-row">FREE delivery Tue, Oct 7</div><div class="a-row">Or fastest delivery Mon</div></div>
      </div>
      <span class="s-pagination-item">1</span><a class="s-pagination-item" href="/s?k=kettle&page=1">1</a><span class="s-pagination-item">2</span>
      <a href="/s?k=kettle&page=3" aria-label="Go to next page">Next</a>`;
    const result = bridge.products();
    expect(result.records[0]).toMatchObject({ asin: "B0TEST0003", title: "Steel Kettle", price: "19.99", delivery: "FREE delivery Tue, Oct 7 · Or fastest delivery Mon", page: "2" });
    expect(result.last_page).toBe(2);
    expect(result.next_url).toBeNull();
  });

  it("reads a product page's details", () => {
    window.history.pushState({}, "", "/Steel-Kettle/dp/B0TEST0003");
    document.body.innerHTML = `
      <span id="productTitle"> Steel Kettle 1.7L </span>
      <a id="sellerProfileTriggerId">Acme Store</a>
      <div id="availability"> In Stock </div>
      <div id="wayfinding-breadcrumbs_feature_div"><ul><li><a>Home &amp; Kitchen</a></li><li><a>Kettles</a></li></ul></div>
      <div id="feature-bullets"><ul><li>Boils in 3 minutes</li><li>Auto shut-off</li></ul></div>
      <table id="productOverview_feature_div"><tr><td>Brand</td><td>Acme</td></tr><tr><td>Capacity</td><td>1.7 Liters</td></tr></table>
      <div id="detailBullets_feature_div"><ul><li><span class="a-text-bold">ASIN :</span> B0TEST0003</li><li><span class="a-text-bold">Item weight :</span> 1 kg</li></ul></div>
      <div id="productDescription"> A kettle. </div>
      <script>var data = {"hiRes":"https://m.media-amazon.com/images/I/big1.jpg"};</script>`;
    const { kind, record, error } = bridge.details();
    expect(error).toBeNull();
    expect(kind).toBe("products");
    expect(record).toMatchObject({
      asin: "B0TEST0003", title: "Steel Kettle 1.7L", seller: "Acme Store", availability: "In Stock", category_path: "Home & Kitchen › Kettles",
      about_this_item: "Boils in 3 minutes\nAuto shut-off", product_details: "Brand: Acme\nCapacity: 1.7 Liters", additional_details: "Item weight: 1 kg",
      product_description: "A kettle.", all_images: "https://m.media-amazon.com/images/I/big1.jpg",
    });
    expect(JSON.parse(record.details_json)).toMatchObject({ Brand: "Acme", Capacity: "1.7 Liters", "Item weight": "1 kg" });
  });
});
