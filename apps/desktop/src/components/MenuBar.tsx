import { useEffect, useRef, useState } from "react";
import { MENUS, type Command, type MenuId } from "../lib/commands.ts";

type OpenState = { menu: MenuId; index: number; submenu: number | null } | null;

/** Title-bar menu bar (File, Edit, Selection, View, Go, Run, Help) with keyboard navigation and Alt mnemonics. */
export function MenuBar({ commands }: { commands: Command[] }) {
  const [open, setOpen] = useState<OpenState>(null);
  const bar = useRef<HTMLDivElement>(null);
  const byMenu = (menu: MenuId) => commands.filter((c) => c.menu === menu);

  const run = (command: Command) => {
    if (command.enabled === false || command.children) return;
    setOpen(null);
    void command.run();
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey && !event.ctrlKey && !event.shiftKey) {
        const menu = MENUS.find((m) => m.mnemonic === event.key.toLowerCase());
        if (menu) {
          event.preventDefault();
          setOpen({ menu: menu.id, index: 0, submenu: null });
          return;
        }
      }
      if (!open) return;
      const items = byMenu(open.menu);
      const menuIndex = MENUS.findIndex((m) => m.id === open.menu);
      const current = items[open.index];
      const move = (delta: number) => {
        for (let step = 1; step <= items.length; step++) {
          const next = (open.index + delta * step + items.length) % items.length;
          if (items[next].enabled !== false) return next;
        }
        return open.index;
      };
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(open.submenu !== null ? { ...open, submenu: null } : null);
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (open.submenu !== null && current?.children?.length) {
          const length = current.children.length;
          setOpen({ ...open, submenu: (open.submenu + (event.key === "ArrowDown" ? 1 : -1) + length) % length });
        } else {
          setOpen({ ...open, index: move(event.key === "ArrowDown" ? 1 : -1), submenu: null });
        }
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        if (current?.children?.length && open.submenu === null) setOpen({ ...open, submenu: 0 });
        else setOpen({ menu: MENUS[(menuIndex + 1) % MENUS.length].id, index: 0, submenu: null });
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        if (open.submenu !== null) setOpen({ ...open, submenu: null });
        else setOpen({ menu: MENUS[(menuIndex - 1 + MENUS.length) % MENUS.length].id, index: 0, submenu: null });
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (open.submenu !== null && current?.children) run(current.children[open.submenu]);
        else if (current?.children?.length) setOpen({ ...open, submenu: 0 });
        else if (current) run(current);
      }
    };
    const onPointer = (event: MouseEvent) => {
      if (open && bar.current && !bar.current.contains(event.target as Node)) setOpen(null);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("mousedown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("mousedown", onPointer);
    };
  });

  return (
    <div className="menubar" role="menubar" aria-label="Application menu" ref={bar}>
      {MENUS.map((menu) => {
        const items = byMenu(menu.id);
        const isOpen = open?.menu === menu.id;
        return (
          <div key={menu.id} className="menubar-item">
            <button
              type="button"
              role="menuitem"
              aria-haspopup="menu"
              aria-expanded={isOpen}
              className="menubar-trigger"
              onMouseDown={(event) => {
                event.preventDefault();
                setOpen(isOpen ? null : { menu: menu.id, index: 0, submenu: null });
              }}
              onMouseEnter={() => open && !isOpen && setOpen({ menu: menu.id, index: 0, submenu: null })}
              onKeyDown={(event) => {
                if (!open && (event.key === "Enter" || event.key === " " || event.key === "ArrowDown")) {
                  event.preventDefault();
                  setOpen({ menu: menu.id, index: 0, submenu: null });
                }
              }}
            >
              <span className="mnemonic">{menu.label[0]}</span>
              {menu.label.slice(1)}
            </button>
            {isOpen && (
              <div className="menu" role="menu" aria-label={menu.label}>
                {items.map((item, index) => (
                  <MenuEntry
                    key={item.id}
                    item={item}
                    active={open.index === index}
                    separator={index > 0 && (items[index - 1].group ?? 0) !== (item.group ?? 0)}
                    submenuIndex={open.index === index ? open.submenu : null}
                    onHover={() => setOpen({ menu: menu.id, index, submenu: item.children?.length ? 0 : null })}
                    onRun={run}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function MenuEntry({ item, active, separator, submenuIndex, onHover, onRun }: { item: Command; active: boolean; separator: boolean; submenuIndex: number | null; onHover: () => void; onRun: (c: Command) => void }) {
  return (
    <>
      {separator && <div className="menu-separator" role="separator" />}
      <div className="menu-row">
        <button
          type="button"
          role="menuitem"
          aria-haspopup={item.children ? "menu" : undefined}
          aria-expanded={item.children ? submenuIndex !== null : undefined}
          aria-disabled={item.enabled === false}
          className={`menu-entry${active ? " active" : ""}`}
          onMouseEnter={onHover}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => onRun(item)}
          tabIndex={-1}
        >
          <span>{item.label}</span>
          <span className="menu-shortcut">{item.children ? "›" : item.shortcut ?? ""}</span>
        </button>
        {item.children && submenuIndex !== null && (
          <div className="menu submenu" role="menu" aria-label={item.label}>
            {item.children.length === 0 && <div className="menu-empty">Nothing here yet</div>}
            {item.children.map((child, index) => (
              <button key={child.id} type="button" role="menuitem" tabIndex={-1} aria-disabled={child.enabled === false} className={`menu-entry${submenuIndex === index ? " active" : ""}`} onMouseDown={(event) => event.preventDefault()} onClick={() => onRun(child)}>
                <span>{child.label}</span>
                <span className="menu-shortcut">{child.shortcut ?? ""}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
