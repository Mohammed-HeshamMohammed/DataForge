import { isTauri } from "./ipc.ts";

export type Theme = "dark" | "light";

export function loadTheme(): Theme {
  try {
    return localStorage.getItem("dataforge.theme") === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("dataforge.theme", theme);
  } catch {
    // Theme still applies for this session.
  }
}

export async function windowAction(action: "minimize" | "toggleMaximize" | "close"): Promise<void> {
  if (!isTauri()) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  await getCurrentWindow()[action]();
}

async function host<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  if (!isTauri()) throw new Error("This feature needs the DataForge desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
}

export type Bounds = { x: number; y: number; width: number; height: number };

export const credentials = {
  list: () => host<{ name: string; stored: boolean }[]>("credential_list"),
  save: (name: string, secret: string) => host("credential_save", { name, secret }),
  remove: (name: string) => host("credential_delete", { name }),
};

export const studioHost = {
  open: (url: string, allowedHosts: string[], bounds: Bounds) => host<void>("studio_open", { url, allowedHosts, bounds }),
  setBounds: (bounds: Bounds) => host<void>("studio_set_bounds", { bounds }),
  navigate: (url: string) => host<void>("studio_navigate", { url }),
  control: (action: "reload" | "stop" | "back") => host<void>("studio_control", { action }),
  close: () => host<void>("studio_close"),
  call: <T>(action: "setMode" | "takePicks" | "pageInfo" | "count" | "extract", args: unknown[] = []) => host<T>("studio_call", { action, args }),
  onEvent: async (handler: (event: { type: string; event?: string; url?: string }) => void) => {
    if (!isTauri()) return () => {};
    const { listen } = await import("@tauri-apps/api/event");
    return listen<{ type: string; event?: string; url?: string }>("studio-event", (e) => handler(e.payload));
  },
};

export type UpdateInfo = { available: boolean; version?: string; current_version?: string; notes?: string | null; published_at?: string | null };

export const updates = {
  check: (repository: string, channel: "stable" | "beta") => host<UpdateInfo>("update_check", { repository, channel }),
  install: () => host<void>("update_install"),
};

export function loadSetting<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`dataforge.${key}`);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function saveSetting(key: string, value: unknown): void {
  try {
    localStorage.setItem(`dataforge.${key}`, JSON.stringify(value));
  } catch {
    // Settings still apply for this session.
  }
}

export async function revealInFolder(path: string): Promise<void> {
  if (!isTauri()) return;
  const { revealItemInDir } = await import("@tauri-apps/plugin-opener");
  await revealItemInDir(path);
}
