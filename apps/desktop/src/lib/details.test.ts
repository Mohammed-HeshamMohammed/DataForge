import test from "node:test";
import assert from "node:assert/strict";
import { describeDetails, detailCount, detailGroup, groupRecord, isDetailLevel, looksPersonal, shortKey, tableColumns, type DetailSummary } from "./details.ts";

const record = {
  title: "Widget 1",
  price: "$19.99",
  "price.amount": 19.99,
  "location.latitude": "41.9",
  "item.rating": "Four",
  "page.title": "Catalog",
  "detail.spec.weight": "1.5 kg",
  source_url: "https://shop.test/c",
  preset_id: "generic.html_list",
  empty: "",
};

test("keys group like the Python details module", () => {
  assert.equal(detailGroup("price.amount", record), "value");
  assert.equal(detailGroup("location.latitude", record), "field"); // a flattened API key, not a derived value
  assert.equal(detailGroup("item.rating", record), "item");
  assert.equal(detailGroup("page.title", record), "page");
  assert.equal(detailGroup("detail.spec.weight", record), "detail");
  assert.equal(detailGroup("source_url", record), "provenance");
  assert.equal(detailGroup("title", record), "field");
});

test("records group in display order without empty values", () => {
  const groups = groupRecord(record);
  assert.deepEqual(groups.map((g) => g.group), ["field", "value", "item", "detail", "page", "provenance"]);
  assert.deepEqual(groups[0].entries.map(([key]) => key), ["title", "price", "location.latitude"]);
  assert.equal(detailCount(record), 4);
  assert.equal(shortKey("detail.spec.weight", "detail"), "spec.weight");
  assert.equal(shortKey("price.amount", "value"), "price.amount");
});

test("table columns keep fields first and add details on request", () => {
  assert.deepEqual(tableColumns([record]), ["title", "price", "location.latitude", "source_url", "empty"]);
  assert.deepEqual(tableColumns([record], { includeDetails: true, exclude: ["source_url", "empty"] }), [
    "title", "price", "location.latitude", "price.amount", "item.rating", "page.title", "detail.spec.weight",
  ]);
  assert.ok(tableColumns([record], { keepProvenance: true }).includes("preset_id"));
});

test("summaries describe levels, groups, and detail pages", () => {
  const summary: DetailSummary = {
    level: "full", records_enriched: 6, fields_added: 80, groups: { value: 10, item: 15, page: 12, detail: 43 }, coverage: {},
    detail_pages: { candidates: 6, fetched: 2, reused: 1, failed: 1, skipped_scope: 1, skipped_robots: 1, stop_reason: "completed" },
  };
  assert.equal(
    describeDetails(summary),
    "Full: 80 detail fields on 6 records · 10 value, 15 element, 12 page, 43 detail · 2 detail pages read (1 failed, 1 out of scope, 1 disallowed by robots.txt, 1 shared)",
  );
  assert.equal(describeDetails({ ...summary, level: "none", detail_pages: null }), "Fields only: no details collected");
  assert.equal(describeDetails(undefined), "");
  assert.ok(isDetailLevel("standard") && !isDetailLevel("everything"));
  assert.ok(looksPersonal("item.phone.e164") && looksPersonal("detail.emails") && !looksPersonal("detail.spec.weight"));
});
