import test from "node:test";
import assert from "node:assert/strict";
import { displayValue, isActive, mappingProblems, maskValue, stageLabel } from "./format.ts";
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
