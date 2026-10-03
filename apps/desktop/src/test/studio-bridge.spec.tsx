import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

type Bridge = {
  setMode(mode: string, root?: string | null): unknown;
  takePicks(): Array<{ relative_selector: string | null; fallback_xpaths: string[]; inside_record_root: boolean }>;
  extract(config: unknown): { records: Record<string, string>[]; error: string | null };
  count(css: string): { count: number; error: string | null };
  click(css: string): { clicked: boolean; error: string | null };
  networkData(): { responses: Array<{ url: string; data: unknown }>; error: string | null };
  pageInfo(): { url: string; title: string; favicon_url: string | null; thumbnail_url: string | null };
  suggestFlow(): { kind: string; next_css: string | null; load_more_css: string | null; can_scroll: boolean; reason: string };
  scrollPage(direction: number): { height: number; y: number };
  listings(): { url: string; records: Record<string, string>[]; on_page: number; from_browsing: number; next_url: string | null; error: string | null };
  products(): { url: string; records: Record<string, string>[]; next_url: string | null; error: string | null };
  nextPage(): { url: string; page: number; next_url: string | null; next_page: number | null; error: string | null };
  details(hint?: unknown): { kind: string; record: Record<string, string>; error: string | null };
};

let bridge: Bridge;
const originalFetch = window.fetch;
let fetchDocument: unknown = { items: [] };

beforeAll(() => {
  window.fetch = (async () => ({
    url: location.origin + "/api/items?token=secret",
    headers: { get: (name: string) => name === "content-type" ? "application/json" : null },
    clone: () => ({ text: async () => JSON.stringify(fetchDocument) }),
  })) as typeof window.fetch;
  document.body.innerHTML = [1, 2, 3]
    .map((n) => `<article class="card"><h3 class="name">Widget ${n}</h3><dl><dt>SKU</dt><dd class="sku">W-${n}</dd><dt>Price</dt><dd class="price">$${n}9.99</dd></dl></article>`)
    .join("");
  const scope = globalThis as { CSS?: { escape?: (v: string) => string } };
  if (!scope.CSS?.escape) scope.CSS = { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\${c}`) };
  const source = readFileSync(resolve(process.cwd(), "src-tauri/src/studio.js"), "utf8");
  new Function(source)();
  bridge = (window as unknown as { __dataforgeStudio: Bridge }).__dataforgeStudio;
});

afterAll(() => {
  window.fetch = originalFetch;
});

describe("Scrape Studio bridge script", () => {
  it("proposes label-anchored and structural XPath fallbacks for a picked value", () => {
    bridge.setMode("element", "article.card");
    const price = document.querySelectorAll("dd.price")[1];
    price.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    const [pick] = bridge.takePicks();
    expect(pick.inside_record_root).toBe(true);
    expect(pick.fallback_xpaths).toEqual([".//dt[normalize-space(.)='Price']/following-sibling::dd[1]", "./dl[1]/dd[2]"]);
  });

  it("extracts with the fallbacks when the CSS selector no longer matches", () => {
    document.querySelectorAll("dd.price").forEach((node) => node.setAttribute("class", "amount-now"));
    const result = bridge.extract({
      record_root: "article.card",
      limit: 10,
      fields: [
        { key: "price", selectors: [{ css: "dd.price" }, { xpath: ".//dt[normalize-space(.)='Price']/following-sibling::dd[1]" }, { xpath: "./dl[1]/dd[2]" }] },
        { key: "sku", selectors: [{ css: "dd.gone" }, { xpath: "./dl[1]/dd[1]" }] },
      ],
    });
    expect(result.error).toBeNull();
    expect(result.records).toEqual([
      { price: "$19.99", sku: "W-1" },
      { price: "$29.99", sku: "W-2" },
      { price: "$39.99", sku: "W-3" },
    ]);
  });
});

describe("label-anchored fallbacks", () => {
  it("ignores item-specific text such as a title and keeps only the structural fallback", () => {
    document.body.innerHTML = [1, 2, 3].map((n) => `<article class="book"><h3>Title ${n}</h3><div><p class="price">£${n}.00</p></div></article>`).join("");
    bridge.setMode("element", "article.book");
    document.querySelectorAll("p.price")[0].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(bridge.takePicks()[0].fallback_xpaths).toEqual(["./div[1]/p[1]"]);
  });
});

describe("dynamic page methods", () => {
  it("returns page icons and social preview images for browser history", () => {
    document.head.innerHTML = "<link rel='icon' href='/brand.svg'><meta property='og:image' content='/preview.webp'>";
    document.title = "Example listing";
    expect(bridge.pageInfo()).toMatchObject({
      title: "Example listing",
      favicon_url: location.origin + "/brand.svg",
      thumbnail_url: location.origin + "/preview.webp",
    });
  });

  it("extracts from open shadow roots and same-origin frames", () => {
    document.body.replaceChildren();
    const host = document.createElement("section");
    host.attachShadow({ mode: "open" }).innerHTML = "<article class='item'><span class='title'>Shadow item</span></article>";
    document.body.append(host);
    const frame = document.createElement("iframe");
    document.body.append(frame);
    frame.contentDocument!.body.innerHTML = "<article class='item'><span class='title'>Frame item</span></article>";

    expect(bridge.count(".item")).toEqual({ count: 2, error: null });
    const result = bridge.extract({ record_root: ".item", fields: [{ key: "title", selectors: [{ css: ".title" }] }], limit: 10 });
    expect(result.records.map((row) => row.title)).toEqual(["Shadow item", "Frame item"]);
  });

  it("clicks a visible load-more control but refuses form controls", () => {
    document.body.replaceChildren();
    const button = document.createElement("button");
    button.type = "button";
    button.className = "more";
    Object.defineProperty(button, "getClientRects", { value: () => [{ width: 10, height: 10 }] });
    let clicks = 0;
    button.addEventListener("click", () => clicks++);
    document.body.append(button);
    const form = document.createElement("form");
    const unsafe = document.createElement("button");
    unsafe.type = "button";
    unsafe.className = "unsafe";
    Object.defineProperty(unsafe, "getClientRects", { value: () => [{ width: 10, height: 10 }] });
    form.append(unsafe);
    document.body.append(form);

    expect(bridge.click(".more").clicked).toBe(true);
    expect(clicks).toBe(1);
    expect(bridge.click(".unsafe").clicked).toBe(false);
  });

  it("captures same-origin fetch JSON without query strings or request headers", async () => {
    fetchDocument = { items: [{ id: 1, name: "Alpha" }] };
    await window.fetch("/api/items");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(bridge.networkData().responses).toEqual([{ url: location.origin + "/api/items", data: fetchDocument }]);
  });

  it("recommends visible next-page and load-more controls", () => {
    document.body.innerHTML = "<nav><a rel='next' href='/page/2'>Next</a></nav>";
    const next = document.querySelector("a")!;
    Object.defineProperty(next, "getClientRects", { value: () => [{ width: 10, height: 10 }] });
    expect(bridge.suggestFlow()).toMatchObject({ kind: "next_link", next_css: "body > nav > a" });

    document.body.innerHTML = "<section><button type='button'>Load more</button></section>";
    const more = document.querySelector("button")!;
    Object.defineProperty(more, "getClientRects", { value: () => [{ width: 10, height: 10 }] });
    expect(bridge.suggestFlow()).toMatchObject({ kind: "load_more", load_more_css: "body > section > button" });
  });
});

describe("Listing and product collectors", () => {
  const listing = (n: number) => ({
    "@type": "ListItem",
    position: n,
    item: {
      "@type": "RealEstateListing",
      url: `${location.origin}/home/${n}-main-st-houston-tx-77007`,
      offers: { "@type": "Offer", price: 300000 + n, priceCurrency: "USD" },
      itemOffered: {
        "@type": "SingleFamilyResidence",
        address: { "@type": "PostalAddress", streetAddress: `${n} MAIN ST`, addressLocality: "HOUSTON", addressRegion: "TX", postalCode: "77007" },
        numberOfBedrooms: 3,
        numberOfBathroomsTotal: 2,
        floorSize: { "@type": "QuantitativeValue", value: 1800 },
      },
    },
  });

  it("reads listings from the page's schema.org data, with the preset's field keys", () => {
    document.body.innerHTML = `<script type="application/ld+json">${JSON.stringify({ "@type": "ItemList", itemListElement: [1, 2, 3].map(listing) })}</script>`;
    const result = bridge.listings();
    expect(result.error).toBeNull();
    expect(result.records).toHaveLength(3);
    expect(result.records[0]).toMatchObject({
      address: "1 Main St", city: "Houston", state: "TX", zip: "77007", price: "300001", beds: "3", baths: "2",
      living_area_sqft: "1800", listing_url: `${location.origin}/home/1-main-st-houston-tx-77007`, collected_from: "page",
    });
  });

  it("falls back to repeated price cards, choosing the listing link over the agent link", () => {
    document.body.innerHTML = `<ul>${[1, 2, 3, 4].map((n) => `
      <li class="listing-card item">
        <a href="/jane-doe/agent_${n}"><span class="agent-photo" style="background-image:url('/realtors/jd.jpg')"></span>Jane Doe</a>
        <div class="photo" style="background-image:url('https://cdn.example/photos/${n}.jpeg')"></div>
        <strong>$45${n},000</strong>
        <a href="/homedetail/${n}-oak-ln-houston-tx-77018/9000${n}"><p class="address">${n} Oak Ln</p><p>Houston, TX 77018</p></a>
        <span>4 bedrooms</span><span>2 full &amp; 1 half baths</span><span>2,100 Sqft.</span><span>Call 713-555-0100</span>
      </li>`).join("")}</ul>`;
    const { records } = bridge.listings();
    expect(records).toHaveLength(4);
    expect(records[0]).toMatchObject({
      address: "1 Oak Ln", city: "Houston", state: "TX", zip: "77018", price: "451000", beds: "4", baths: "2.5", living_area_sqft: "2100",
      listing_url: `${location.origin}/homedetail/1-oak-ln-houston-tx-77018/90001`, image: "https://cdn.example/photos/1.jpeg",
    });
    expect(JSON.stringify(records)).not.toMatch(/Jane Doe|713-555-0100|agent_/);
  });

  it("adds listings the site loads while browsing, without agent contact fields", async () => {
    document.body.innerHTML = "<main>Map search</main>";
    fetchDocument = {
      searchResults: {
        listResults: [1, 2].map((n) => ({
          zpid: `2000${n}`, unformattedPrice: 250000 + n, addressStreet: `${n} Elm St`, addressCity: "Spring", addressState: "TX", addressZipcode: "77388",
          beds: 3, baths: 2, area: 1600, detailUrl: `/homedetails/${n}-elm-st/2000${n}_zpid/`, statusText: "House for sale",
          agentName: "John Agent", agentPhone: "281-555-0199",
        })),
      },
    };
    await window.fetch("/async-create-search-page-state");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const result = bridge.listings();
    expect(result.from_browsing).toBe(2);
    expect(result.records).toHaveLength(2);
    expect(result.records[1]).toMatchObject({
      listing_id: "20002", address: "2 Elm St", city: "Spring", state: "TX", zip: "77388", price: "250002", collected_from: "browsing",
      listing_url: `${location.origin}/homedetails/2-elm-st/20002_zpid/`,
    });
    expect(JSON.stringify(result.records)).not.toMatch(/John Agent|281-555-0199/);
  });

  it("reads Amazon search result cards as products", () => {
    document.body.innerHTML = `
      <div data-component-type="s-search-result" data-asin="B0TEST0001">
        <div data-cy="title-recipe"><h2>Acme</h2><h2><span>Acme Kettle 1.7L</span></h2></div>
        <span class="a-price"><span class="a-offscreen">EGP 1,299.00</span></span>
        <span class="a-price a-text-price"><span class="a-offscreen">EGP 1,599.00</span></span>
        <i class="a-icon-alt">4.5 out of 5 stars</i>
        <div data-cy="reviews-block"><a aria-label="4.5 out of 5 stars"></a><a aria-label="1,234 ratings"></a></div>
        <img class="s-image" src="https://m.media-amazon.com/images/I/kettle._AC_UL320_.jpg">
      </div>`;
    const { records } = bridge.products();
    expect(records).toEqual([{
      site: "localhost", asin: "B0TEST0001", title: "Acme Kettle 1.7L", brand: "Acme", price: "1299.00", currency: "EGP", list_price: "1599.00",
      rating: "4.5", ratings_count: "1234", page: "1", position: "1", image: "https://m.media-amazon.com/images/I/kettle.jpg",
      product_url: `${location.origin}/dp/B0TEST0001`,
    }]);
  });

  it("reads a listing page's main listing and its labelled facts", () => {
    const page = { props: { homeDetails: {
      url: `${location.origin}/home/6807-tournament-dr-houston-tx-77069`, price: { formattedPrice: "$300,000" },
      location: { streetAddress: "6807 Tournament Dr", city: "Houston", stateCode: "TX", zipCode: "77069" }, bedrooms: 2, bathrooms: 2, livingArea: 1500,
      description: "A quiet home near the golf course. Call 713-555-0100 for a showing.",
      features: [{ formattedName: "Year Built", formattedValue: "1995" }, { formattedName: "HOA", formattedValue: "$100/Monthly" },
        { formattedName: "Heating", formattedValue: "Electric" }, { formattedName: "Listed By", formattedValue: "Jane Agent" }],
    } } };
    document.body.innerHTML = `<script id="__NEXT_DATA__" type="application/json">${JSON.stringify(page)}</script>`;
    const { kind, record } = bridge.details({ url: `${location.origin}/home/6807-tournament-dr-houston-tx-77069` });
    expect(kind).toBe("listings");
    expect(record).toMatchObject({ address: "6807 Tournament Dr", price: "300000", year_built: "1995", hoa_fee: "100" });
    expect(record.facts).toBe(["Year Built: 1995", "HOA: $100/Monthly", "Heating: Electric"].join("\n"));
    expect(record.description).toContain("[phone removed]");
    expect(JSON.stringify(record)).not.toMatch(/Jane Agent/);
  });

  it("reads detail pages whose facts are in analytics scripts, schema.org blocks, and HTML tables", () => {
    window.history.pushState({}, "", "/138512553/");
    const residence = { "@context": "http://schema.org/", "@type": "Residence", url: `${location.origin}/138512553/`,
      address: { "@type": "PostalAddress", streetAddress: "7715 Cloverlake Court", addressLocality: "Houston", addressRegion: "TX", postalCode: "77040" },
      description: "Single Family Home for sale in Houston, TX for $289,000 with 3 bedrooms. This home was built in 2007 on a quiet street." };
    document.body.innerHTML = `
      <script>window.dataLayer.push({"event":"PropertyDetailView","ecommerce":{"detail":{"dimension53":'False'}},"listedPrice":"289,000.00","sqft":"1,988","baths":"2.0","beds":"3","mlsNumber":"87788777","lotSize":"0.1837","propertyType":"Single Family"});</script>
      <script type="application/ld+json">${JSON.stringify(residence)}</script>
      <table><tr><th>Heating</th><td>Central gas</td></tr><tr><th>Listed by</th><td>Jane Agent</td></tr></table>
      <ul><li>Garage: 2 spaces</li></ul>`;
    const { record } = bridge.details({ url: `${location.origin}/138512553/` });
    expect(record).toMatchObject({ address: "7715 Cloverlake Court", zip: "77040", price: "289000", beds: "3", baths: "2", living_area_sqft: "1988",
      mls_number: "87788777", lot_size: "0.1837", year_built: "2007" });
    expect(record.facts).toContain("Heating: Central gas");
    expect(record.facts).toContain("Garage: 2 spaces");
    expect(JSON.stringify(record)).not.toMatch(/Jane Agent/);
    window.history.pushState({}, "", "/");
  });

  it("finds the next results page", () => {
    document.body.innerHTML = "<nav><a href='/homes?page=1'>1</a><a href='/homes?page=2' aria-label='Go to next page'>Next</a></nav>";
    expect(bridge.nextPage()).toMatchObject({ next_url: `${location.origin}/homes?page=2`, next_page: 2, error: null });
  });
});
