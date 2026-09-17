import { useEffect, useMemo, useRef, useState } from "react";
import { MENUS, flatten, fuzzyScore, type Command } from "../lib/commands.ts";
import { FolderIcon, SearchIcon } from "./icons.tsx";

/**
 * The title-bar command center: shows the open project and doubles as the search for every command.
 * Clicking it (or Ctrl+K / Ctrl+Shift+P) turns it into a search field with results dropping down under it.
 */
export function CommandCenter({
  commands,
  project,
  open,
  onOpenChange,
}: {
  commands: Command[];
  project: { name: string; path: string } | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);

  const results = useMemo(() => {
    return flatten(commands)
      .filter((c) => !c.children && !c.paletteHidden && c.enabled !== false)
      .map((c, order) => {
        const menu = c.id.startsWith("file.recent.") ? "Recent project" : MENUS.find((m) => m.id === c.menu)?.label ?? "";
        const score = fuzzyScore(`${menu}: ${c.label}`, query) ?? fuzzyScore(c.label, query);
        return { command: c, menu, score, order };
      })
      .filter((r) => r.score !== null)
      .sort((a, b) => (query ? (a.score as number) - (b.score as number) : a.order - b.order))
      .slice(0, 60);
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      window.setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);
  useEffect(() => setIndex(0), [query]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(".palette-item.active")?.scrollIntoView?.({ block: "nearest" });
  }, [index]);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) onOpenChange(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open, onOpenChange]);

  const run = (command: Command) => {
    onOpenChange(false);
    void command.run();
  };

  const label = project ? `${project.name} — ${project.path}` : "No project open";
  return (
    <div className={`command-center${open ? " open" : ""}`} ref={root}>
      {!open ? (
        <button type="button" className="command-center-bar" onClick={() => onOpenChange(true)} title={`${label} · Search commands (Ctrl+K)`} aria-label={`Search commands. Current project: ${label}`}>
          <SearchIcon size={13} />
          <span className="command-center-project">
            {project ? (
              <>
                <FolderIcon size={13} />
                <strong>{project.name}</strong>
                <span className="project-path">{project.path}</span>
              </>
            ) : (
              <span className="muted">Search commands or open a project</span>
            )}
          </span>
          <kbd>Ctrl+K</kbd>
        </button>
      ) : (
        <div className="command-center-bar editing">
          <SearchIcon size={13} />
          <input
            ref={input}
            className="command-center-input"
            value={query}
            placeholder={project ? `Search commands in ${project.name}…` : "Search commands…"}
            role="combobox"
            aria-label="Search commands"
            aria-expanded="true"
            aria-controls="command-center-results"
            aria-activedescendant={results[index] ? `command-${results[index].command.id}` : undefined}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                onOpenChange(false);
              } else if (e.key === "ArrowDown") {
                e.preventDefault();
                setIndex((i) => Math.min(i + 1, results.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setIndex((i) => Math.max(i - 1, 0));
              } else if (e.key === "Enter" && results[index]) {
                e.preventDefault();
                run(results[index].command);
              }
            }}
          />
          <kbd>Esc</kbd>
        </div>
      )}
      {open && (
        <ul id="command-center-results" ref={list} className="command-center-results" role="listbox" aria-label="Commands">
          {project && !query && (
            <li className="command-center-heading" role="presentation">
              {project.path}
            </li>
          )}
          {results.length === 0 && <li className="menu-empty">No matching commands</li>}
          {results.map((r, i) => (
            <li
              key={r.command.id}
              id={`command-${r.command.id}`}
              role="option"
              aria-selected={i === index}
              className={`palette-item${i === index ? " active" : ""}`}
              onMouseEnter={() => setIndex(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => run(r.command)}
            >
              <span className="palette-label">
                <span className="muted">{r.menu}: </span>
                {r.command.label}
              </span>
              {r.command.shortcut && <kbd>{r.command.shortcut}</kbd>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ShortcutsDialog({ commands, onClose }: { commands: Command[]; onClose: () => void }) {
  const withShortcuts = flatten(commands).filter((c) => c.shortcut);
  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="palette shortcuts" role="dialog" aria-modal="true" aria-labelledby="shortcuts-title" onMouseDown={(e) => e.stopPropagation()} onKeyDown={(e) => e.key === "Escape" && onClose()}>
        <div className="section-head">
          <h2 id="shortcuts-title">Keyboard shortcuts</h2>
          <button type="button" className="btn btn-small" onClick={onClose} autoFocus>
            Close
          </button>
        </div>
        <div className="shortcut-grid">
          {MENUS.map((menu) => {
            const items = withShortcuts.filter((c) => c.menu === menu.id);
            if (!items.length) return null;
            return (
              <section key={menu.id}>
                <h3 className="section-label">{menu.label}</h3>
                <dl className="shortcut-list">
                  {items.map((c) => (
                    <div key={c.id}>
                      <dt>{c.label}</dt>
                      <dd>
                        <kbd>{c.shortcut}</kbd>
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function AboutDialog({ version, details, onClose }: { version: string; details: [string, string][]; onClose: () => void }) {
  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="palette about" role="dialog" aria-modal="true" aria-labelledby="about-title" onMouseDown={(e) => e.stopPropagation()} onKeyDown={(e) => e.key === "Escape" && onClose()}>
        <h2 id="about-title">DataForge {version}</h2>
        <p className="muted">Local-first collection, certification, cleaning, and matching of public data for research.</p>
        <dl className="facts">
          {details.map(([label, value]) => (
            <div key={label} style={{ display: "contents" }}>
              <dt>{label}</dt>
              <dd>
                <code>{value}</code>
              </dd>
            </div>
          ))}
        </dl>
        <div className="row-actions">
          <button type="button" className="btn btn-primary" onClick={onClose} autoFocus>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
