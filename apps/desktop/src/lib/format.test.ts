import test from "node:test";
import assert from "node:assert/strict";
import { compactNumber, formatBytes, niceMax, relativeTime, displayValue, isActive, mappingProblems, maskValue, stageLabel } from "./format.ts";
import { unwrap, ServiceError } from "./ipc.ts";

test("sensitive values are masked until revealed", () => {
  assert.equal(maskValue("5125550182"), "5••••••••2");
  assert.equal(maskValue(""), "");
  assert.equal(displayValue("ada@example.test", true, false).includes("@example"), false);
  assert.equal(displayValue("ada@example.test", true, true), "ada@example.test");
  assert.equal(displayValue("Austin", false, false), "Austin");
});

test("job helpers use backend state names", () => {
  assert.equal(isActive("paused"), true);
  assert.equal(isActive("completed"), false);
  assert.equal(stageLabel("finding_candidates"), "Finding candidates");
  assert.equal(stageLabel("custom_stage"), "custom stage");
});

test("mapping problems flag pooled positional roles and missing evidence", () => {
  assert.deepEqual(mappingProblems({ Phone: "phone", "Alt Phone": "phone" }), []);
  assert.equal(mappingProblems({ "Property Address": "address", "Mailing Address": "address", Phone: "phone" }).length, 1);
  assert.match(mappingProblems({ Name: "name" })[0], /names alone/);
});

test("unwrap returns results and raises typed service errors", () => {
  assert.deepEqual(unwrap({ schema_version: 1, ok: true, result: [1] }), [1]);
  assert.throws(
    () => unwrap({ schema_version: 1, ok: false, error: { code: "review_conflict", message: "changed" } }),
    (error: unknown) => error instanceof ServiceError && error.code === "review_conflict",
  );
  assert.throws(() => unwrap({ ok: true }), /unexpected response/);
});

test("compact numbers, sizes, relative times, and axis maxima", () => {
  assert.equal(compactNumber(1284), "1,284");
  assert.equal(compactNumber(12_940), "12.9K");
  assert.equal(compactNumber(4_200_000), "4.2M");
  assert.equal(compactNumber(150_000), "150K");
  assert.equal(compactNumber(undefined), "—");
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(803_216), "784 KB");
  const now = Date.parse("2026-09-24T12:00:00Z");
  assert.equal(relativeTime("2026-09-24T11:59:40Z", now), "just now");
  assert.equal(relativeTime("2026-09-24T11:30:00Z", now), "30 min ago");
  assert.equal(relativeTime("2026-09-23T12:00:00Z", now), "1 day ago");
  assert.equal(relativeTime("2026-09-24T14:00:00Z", now), "in 2 h");
  assert.equal(niceMax(0), 1);
  assert.equal(niceMax(7), 10);
  assert.equal(niceMax(120), 200);
  assert.equal(niceMax(5), 5);
});
