import test from "node:test";
import assert from "node:assert/strict";
import { BUILT_IN_SITE_CATALOG, defaultVariables, enrichSiteCatalog, needsStartUrl, presetsFor, sourceOf, variablePayload, variableProblems, type SourcePreset } from "./sources.ts";

const http = (extra: Partial<SourcePreset>): SourcePreset => ({ id: "x.y", version: "1.0.0", page_type: "p", strategy: { preferred: "http", allowed: ["http"] }, extraction: {}, ...extra });

const overpass: SourcePreset = {
  ...http({}),
  id: "osm.overpass_pois",
  strategy: { preferred: "api", allowed: ["api"] },
  request: {
    url_template: "https://{{endpoint}}/api/interpreter",
    limit_variable: "limit",
    variables: {
      endpoint: { type: "enum", choices: ["overpass.kumi.systems", "overpass-api.de"], default: "overpass.kumi.systems" },
      amenity: { type: "string", required: true, pattern: "[a-z_]{2,30}" },
      south: { type: "number", required: true, minimum: -90, maximum: 90 },
      limit: { type: "integer", default: 500, minimum: 1, maximum: 5000 },
    },
  },
};

test("presets are sorted into source types", () => {
  const presets = [
    http({ id: "a.list" }),
    http({ id: "a.sitemap", discovery: { mode: "sitemap" } }),
    http({ id: "a.feed", discovery: { mode: "feed" } }),
    http({ id: "a.crawl", discovery: { mode: "crawl" } }),
    http({ id: "a.docs", extraction: { mode: "document_tables" } }),
    http({ id: "a.oai", discovery: { mode: "oai_pmh" } }),
    overpass,
  ];
  assert.deepEqual(presets.map(sourceOf), ["website", "sitemap", "feed", "crawl", "documents", "repository", "api"]);
  assert.deepEqual(presetsFor(presets, "archive").map((p) => p.id), ["a.list"]);
});

test("request variables get defaults, hints, and typed payloads", () => {
  const values = defaultVariables(overpass);
  assert.deepEqual(values, { endpoint: "overpass.kumi.systems", amenity: "", south: "", limit: "500" });
  assert.deepEqual(variableProblems(overpass, values), ["amenity is required", "south is required"]);
  const filled = { ...values, amenity: 'cafe"]', south: "120" };
  assert.deepEqual(variableProblems(overpass, filled), ["amenity has an invalid format", "south must be between -90 and 90"]);
  assert.deepEqual(variablePayload(overpass, { ...values, amenity: "cafe", south: "30.2" }), { endpoint: "overpass.kumi.systems", amenity: "cafe", south: 30.2, limit: 500 });
  assert.equal(needsStartUrl(overpass), false);
  assert.equal(needsStartUrl(http({})), true);
});

test("built-in website fallback covers every supported site group", () => {
  assert.equal(BUILT_IN_SITE_CATALOG.length, 54);
  assert.deepEqual(
    [...new Set(BUILT_IN_SITE_CATALOG.map((site) => site.site_category))].sort(),
    ["community", "content", "developer", "jobs", "local", "marketplace", "media", "real_estate"],
  );
  assert.ok(BUILT_IN_SITE_CATALOG.every((site) => site.example_url && site.suggested_fields.length));
  assert.ok(BUILT_IN_SITE_CATALOG.every((site) => site.variants?.length));
  const amazon = BUILT_IN_SITE_CATALOG.find((site) => site.site_id === "amazon");
  assert.ok(amazon?.variants?.some((variant) => variant.domain === "amazon.eg"));
  assert.ok(amazon?.variants?.some((variant) => variant.domain === "amazon.co.uk"));
});

test("service site entries inherit the shipped regional editions", () => {
  const [amazon] = enrichSiteCatalog([{
    site_id: "amazon", site_name: "Amazon", site_category: "marketplace", domains: ["amazon.com"],
    recommended_method: "visual_studio", suggested_fields: ["title", "price"], requires_rendered: true,
    example_url: "https://www.amazon.com/s?k=laptop",
  }]);
  assert.ok(amazon.domains.includes("amazon.eg"));
  assert.ok(amazon.variants?.some((variant) => variant.label === "Egypt" && variant.example_url.includes("amazon.eg")));
});
