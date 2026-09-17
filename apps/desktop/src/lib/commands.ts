/** App commands shared by the menu bar, the command palette, and global keyboard shortcuts. */

export type MenuId = "file" | "edit" | "selection" | "view" | "go" | "run" | "help";

export type Command = {
  id: string;
  label: string;
  menu: MenuId;
  /** Human shortcut such as "Ctrl+Shift+P"; also used to match key events. */
  shortcut?: string;
  /** Items with the same group are kept together; a separator is drawn between groups. */
  group?: number;
  run: () => void | Promise<void>;
  enabled?: boolean;
  /** Submenu items (for example File → Open recent). */
  children?: Command[];
  /** Shortcut is handled by the browser in text fields (Cut, Copy, Paste, Select all). */
  nativeInFields?: boolean;
  /** Hidden from the palette (for example submenu parents). */
  paletteHidden?: boolean;
};

export const MENUS: { id: MenuId; label: string; mnemonic: string }[] = [
  { id: "file", label: "File", mnemonic: "f" },
  { id: "edit", label: "Edit", mnemonic: "e" },
  { id: "selection", label: "Selection", mnemonic: "s" },
  { id: "view", label: "View", mnemonic: "v" },
  { id: "go", label: "Go", mnemonic: "g" },
  { id: "run", label: "Run", mnemonic: "r" },
  { id: "help", label: "Help", mnemonic: "h" },
];

const KEY_ALIASES: Record<string, string> = { "=": "=", "+": "=", "-": "-", ",": ",", "/": "/", esc: "escape", del: "delete", tab: "tab" };

export function parseShortcut(shortcut: string): { ctrl: boolean; shift: boolean; alt: boolean; key: string } {
  const parts = shortcut.toLowerCase().split("+").map((p) => p.trim());
  // "Ctrl+=" splits into ["ctrl", "="]; "Ctrl++" would be ambiguous, so shortcuts use "=".
  const key = parts[parts.length - 1];
  return { ctrl: parts.includes("ctrl"), shift: parts.includes("shift"), alt: parts.includes("alt"), key: KEY_ALIASES[key] ?? key };
}

export function eventKey(event: KeyboardEvent): string {
  const key = event.key.toLowerCase();
  if (key === "+") return "=";
  if (event.code === "Equal") return "=";
  if (event.code === "Minus") return "-";
  return key;
}

export function matchesShortcut(event: KeyboardEvent, shortcut: string): boolean {
  const wanted = parseShortcut(shortcut);
  return (event.ctrlKey || event.metaKey) === wanted.ctrl && event.shiftKey === wanted.shift && event.altKey === wanted.alt && eventKey(event) === wanted.key;
}

export function flatten(commands: Command[]): Command[] {
  return commands.flatMap((c) => [c, ...(c.children ? flatten(c.children) : [])]);
}

/** Case-insensitive fuzzy match: every query character appears in order. Lower scores are better. */
export function fuzzyScore(label: string, query: string): number | null {
  const text = label.toLowerCase();
  const q = query.toLowerCase().trim();
  if (!q) return 0;
  const direct = text.indexOf(q);
  if (direct >= 0) return direct;
  let position = 0;
  let gaps = 0;
  for (const char of q) {
    const found = text.indexOf(char, position);
    if (found < 0) return null;
    gaps += found - position;
    position = found + 1;
  }
  // Letters scattered far apart (for example across a long folder path) are not a meaningful match.
  if (gaps > q.length * 3 + 2) return null;
  return 100 + gaps;
}

export function isTextField(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest("input, textarea, select, [contenteditable='true'], [contenteditable='']");
}
