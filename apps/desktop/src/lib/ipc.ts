import { invoke } from "@tauri-apps/api/core";

export type ServiceResponse<T> =
  | { schema_version: 1; ok: true; result: T }
  | { schema_version: 1; ok: false; error: { code: string; message: string } };

export class ServiceError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** Unwraps the versioned envelope. The UI never talks to SQLite, files, or workers directly. */
export function unwrap<T>(response: unknown): T {
  const envelope = response as ServiceResponse<T> | undefined;
  if (!envelope || envelope.schema_version !== 1 || typeof envelope.ok !== "boolean") {
    throw new ServiceError("invalid_response", "The DataForge service returned an unexpected response");
  }
  if (!envelope.ok) {
    throw new ServiceError(envelope.error.code, envelope.error.message);
  }
  return envelope.result;
}

export async function call<T>(command: string, payload: Record<string, unknown> = {}): Promise<T> {
  if (isTauri()) {
    return unwrap<T>(await invoke("service_call", { command, payload }));
  }
  // Browser-only development: `python -m dataforge_application.server --http 8765` behind the Vite proxy.
  const response = await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ schema_version: 1, command, payload }),
  });
  if (!response.ok) {
    throw new ServiceError("bridge_unavailable", "The development service bridge is not running");
  }
  return unwrap<T>(await response.json());
}

export async function pickFile(filters: { name: string; extensions: string[] }[]): Promise<string | null> {
  if (!isTauri()) {
    return null;
  }
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({ multiple: false, directory: false, filters });
  return typeof selected === "string" ? selected : null;
}

export async function pickDirectory(): Promise<string | null> {
  if (!isTauri()) {
    return null;
  }
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({ multiple: false, directory: true });
  return typeof selected === "string" ? selected : null;
}
