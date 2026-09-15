import test from "node:test";
import assert from "node:assert/strict";
import { checkHealth, isValidHealthResult } from "./health.ts";

test("checkHealth returns the command payload", async () => {
  const result = await checkHealth(async () => ({
    schema_version: 1,
    service: "desktop-ui",
    status: "ok",
    checked_at: "2026-09-13T12:00:00.000Z",
  }));

  assert.equal(result.service, "desktop-ui");
  assert.equal(result.status, "ok");
});

test("isValidHealthResult rejects incomplete payloads", () => {
  assert.equal(isValidHealthResult({ schema_version: 1, service: "x", status: "ok" }), false);
  assert.equal(
    isValidHealthResult({
      schema_version: 1,
      service: "desktop-host",
      status: "ok",
      checked_at: "2026-09-13T12:00:00.000Z",
    }),
    true,
  );
});
