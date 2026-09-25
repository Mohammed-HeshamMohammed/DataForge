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
