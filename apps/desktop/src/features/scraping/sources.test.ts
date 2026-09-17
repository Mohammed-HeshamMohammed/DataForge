import test from "node:test";
import assert from "node:assert/strict";
import { defaultVariables, needsStartUrl, presetsFor, sourceOf, variablePayload, variableProblems, type SourcePreset } from "./sources.ts";

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
    overpass,
  ];
  assert.deepEqual(presets.map(sourceOf), ["website", "sitemap", "feed", "crawl", "documents", "api"]);
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
