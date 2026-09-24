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

describe("item pages", () => {
  it("picks a value on a whole page and reads it back with a sanitized copy of the page", () => {
    document.body.innerHTML =
      "<main><h1>Widget 7</h1><div id='bullets'><ul><li>Steel</li><li>Blue</li></ul></div>" +
      "<table><tr><th>Weight</th><td>1.5 kg</td></tr></table><form><input type='hidden' name='csrf' value='secret-token'></form>" +
      "<script>window.track = 1;</script></main>";
    const bridge = (window as unknown as {
      __dataforgeStudio: {
        setMode(m: string, r?: string | null): unknown;
        takePicks(): { selector: string; fallback_xpaths: string[]; inside_record_root?: boolean }[];
        extract(config: unknown): { records: Record<string, string>[]; error: string | null };
      };
    }).__dataforgeStudio;
    bridge.setMode("element", null);
    document.querySelector("td")!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    const [pick] = bridge.takePicks();
    expect(pick.inside_record_root).toBeUndefined();
    expect(pick.fallback_xpaths).toContain("./main[1]/table[1]/tbody[1]/tr[1]/td[1]");
    const result = bridge.extract({ record_root: "body", limit: 1, element_html: true, element_max: 400000,
      fields: [{ key: "weight", selectors: [{ css: pick.selector }] }, { key: "bullets", selectors: [{ css: "#bullets" }] }] });
    const [record] = result.records;
    expect(record.weight).toBe("1.5 kg");
    expect(record.bullets).toMatch(/Steel\s*Blue/); // jsdom has no innerText; WebView2 separates list items
    expect(record.__element).toContain("<h1>Widget 7</h1>");
    expect(record.__element).not.toContain("secret-token");
    expect(record.__element).not.toContain("window.track");
  });
});

describe("background item pages", () => {
  type Marks = { markItems(config: unknown): { marked: number; roots?: number } };
  const cards = () => [...document.querySelectorAll("li.result")];

  it("outlines each card by its item link and shows one status line, without touching the page's own markup", () => {
    document.body.innerHTML = [1, 2, 3, 4].map((n) => `<li class="result"><a class="title" href="/dp/B0${n}#reviews">Item ${n}</a><span class="price">$${n}.99</span></li>`).join("");
    const bridge = (window as unknown as { __dataforgeStudio: Marks & Bridge }).__dataforgeStudio;
    const url = (n: number) => new URL(`/dp/B0${n}`, location.href).href;
    const result = bridge.markItems({
      record_root: "li.result",
      link: { css: "a.title", attribute: "href" },
      marks: { [url(1)]: "done", [url(2)]: "working", [url(3)]: "failed", [url(9)]: "done" },
      status: "Reading item pages in the background: 1 of 4",
    });
    expect(result).toEqual({ marked: 3, roots: 4 });
    expect(cards().map((card) => card.getAttribute("data-dataforge-item"))).toEqual(["done", "working", "failed", null]);
    expect(document.querySelector("[data-dataforge-status]")?.textContent).toBe("Reading item pages in the background: 1 of 4");
    // The marks never leak into the sanitized copies sent for record details.
    const copy = bridge.extract({ record_root: "li.result", fields: [], limit: 1, element_html: true }).records[0].__element;
    expect(copy).not.toContain("data-dataforge");
    expect(bridge.markItems({ record_root: "li.result[", marks: {} })).toMatchObject({ marked: 0 });
    bridge.markItems({ clear: true });
    expect(document.querySelectorAll("[data-dataforge-item], [data-dataforge-status], style[data-dataforge-marks]")).toHaveLength(0);
  });

  it("ignores states it does not know", () => {
    document.body.innerHTML = `<li class="result"><a class="title" href="/dp/X">X</a></li>`;
    const bridge = (window as unknown as { __dataforgeStudio: Marks }).__dataforgeStudio;
    expect(bridge.markItems({ record_root: "li.result", link: { css: "a.title", attribute: "href" }, marks: { [new URL("/dp/X", location.href).href]: "boom" } }).marked).toBe(0);
    bridge.markItems({ clear: true });
  });
});
