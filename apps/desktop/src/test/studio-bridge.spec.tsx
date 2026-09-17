import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";

type Bridge = {
  setMode(mode: string, root?: string | null): unknown;
  takePicks(): Array<{ relative_selector: string | null; fallback_xpaths: string[]; inside_record_root: boolean }>;
  extract(config: unknown): { records: Record<string, string>[]; error: string | null };
};

describe("Scrape Studio bridge script", () => {
  let bridge: Bridge;
  beforeAll(() => {
    document.body.innerHTML = [1, 2, 3]
      .map((n) => `<article class="card"><h3 class="name">Widget ${n}</h3><dl><dt>SKU</dt><dd class="sku">W-${n}</dd><dt>Price</dt><dd class="price">$${n}9.99</dd></dl></article>`)
      .join("");
    const scope = globalThis as { CSS?: { escape?: (v: string) => string } };
    if (!scope.CSS?.escape) scope.CSS = { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\${c}`) };
    const source = readFileSync(resolve(process.cwd(), "src-tauri/src/studio.js"), "utf8");
    new Function(source)();
    bridge = (window as unknown as { __dataforgeStudio: Bridge }).__dataforgeStudio;
  });

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
    const bridge = (window as unknown as { __dataforgeStudio: { setMode(m: string, r?: string): unknown; takePicks(): { fallback_xpaths: string[] }[] } }).__dataforgeStudio;
    bridge.setMode("element", "article.book");
    document.querySelectorAll("p.price")[0].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(bridge.takePicks()[0].fallback_xpaths).toEqual(["./div[1]/p[1]"]);
  });
});
