export type HealthResult = {
  schema_version: 1;
  service: string;
  status: "ok" | "degraded" | "failed";
  checked_at: string;
};

export type HealthCommand = () => Promise<HealthResult>;

export async function checkHealth(command: HealthCommand): Promise<HealthResult> {
  return command();
}

export function isValidHealthResult(value: unknown): value is HealthResult {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    record.schema_version === 1 &&
    typeof record.service === "string" &&
    record.service.length > 0 &&
    (record.status === "ok" || record.status === "degraded" || record.status === "failed") &&
    typeof record.checked_at === "string" &&
    record.checked_at.length > 0
  );
}
