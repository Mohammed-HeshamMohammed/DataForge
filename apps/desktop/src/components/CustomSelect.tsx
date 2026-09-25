import { createPortal } from "react-dom";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";

export type SelectOption = {
  value: string;
  label: string;
  description?: string;
  group?: string;
  disabled?: boolean;
};

type MenuPosition = Pick<CSSProperties, "top" | "bottom" | "left" | "width" | "maxHeight">;

export function CustomSelect({
  value,
  options,
  onChange,
  placeholder = "Choose an option",
  ariaLabel,
  disabled = false,
  searchable = false,
  searchPlaceholder = "Search options",
  title,
  className = "",
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
  searchable?: boolean;
  searchPlaceholder?: string;
  title?: string;
  className?: string;
}) {
  const reactId = useId();
  const listboxId = `select-${reactId.replace(/:/g, "")}`;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [position, setPosition] = useState<MenuPosition>({});
  const selected = options.find((option) => option.value === value);
  const filteredOptions = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return options;
    return options.filter((option) => {
      const searchableText = [option.label, option.description, option.group].filter(Boolean).join(" ").toLowerCase();
      return terms.every((term) => searchableText.includes(term));
    });
  }, [options, query]);

  const placeMenu = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const margin = 8;
    const width = Math.min(Math.max(rect.width, 220), window.innerWidth - margin * 2);
    const left = Math.min(Math.max(rect.left, margin), window.innerWidth - width - margin);
    const below = window.innerHeight - rect.bottom - margin;
    const above = rect.top - margin;
    if (below < 220 && above > below) {
      setPosition({ bottom: window.innerHeight - rect.top + 4, left, width, maxHeight: Math.max(140, above - 4) });
    } else {
      setPosition({ top: rect.bottom + 4, left, width, maxHeight: Math.max(140, below - 4) });
    }
  };

  const close = (restoreFocus = true) => {
    setOpen(false);
    setQuery("");
    if (restoreFocus) window.requestAnimationFrame(() => triggerRef.current?.focus());
  };

  const show = () => {
    if (disabled) return;
    placeMenu();
    setOpen(true);
  };

  const choose = (option: SelectOption) => {
    if (option.disabled) return;
    onChange(option.value);
    close();
  };

  const focusOption = (direction: 1 | -1 | "first" | "last") => {
    const enabled = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role='option']:not(:disabled)") ?? [])];
    if (!enabled.length) return;
    const active = document.activeElement instanceof HTMLButtonElement ? enabled.indexOf(document.activeElement) : -1;
    const next = direction === "first" ? 0 : direction === "last" ? enabled.length - 1 : active < 0 ? (direction === 1 ? 0 : enabled.length - 1) : (active + direction + enabled.length) % enabled.length;
    enabled[next]?.focus();
  };

  useLayoutEffect(() => {
    if (!open) return;
    placeMenu();
    const timer = window.setTimeout(() => {
      if (searchable) searchRef.current?.focus();
      else {
        const selectedOption = menuRef.current?.querySelector<HTMLButtonElement>("[role='option'][aria-selected='true']");
        (selectedOption ?? menuRef.current?.querySelector<HTMLButtonElement>("[role='option']:not(:disabled)"))?.focus();
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open, searchable]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) close(false);
    };
    const onResize = () => close(false);
    const onScroll = (event: Event) => {
      if (!menuRef.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      show();
      window.setTimeout(() => focusOption(event.key === "ArrowDown" ? "first" : "last"), 0);
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      close();
    }
  };

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      focusOption(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      focusOption(event.key === "Home" ? "first" : "last");
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      close(false);
    }
  };

  let previousGroup: string | undefined;
  const menu = open ? createPortal(
    <div ref={menuRef} className="custom-select-menu" style={position} onKeyDown={onMenuKeyDown}>
      {searchable && (
        <div className="custom-select-search">
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                focusOption("first");
              } else if (event.key === "Escape") {
                event.preventDefault();
                close();
              } else if (event.key === "Enter" && filteredOptions.length === 1) {
                event.preventDefault();
                choose(filteredOptions[0]);
              }
            }}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
          />
        </div>
      )}
      <div id={listboxId} className="custom-select-options" role="listbox" aria-label={ariaLabel ?? "Options"}>
        {filteredOptions.map((option) => {
          const showGroup = !!option.group && option.group !== previousGroup;
          previousGroup = option.group;
          return (
            <div key={option.value} className="custom-select-option-wrap" role="presentation">
              {showGroup && <div className="custom-select-group" role="presentation">{option.group}</div>}
              <button
                type="button"
                role="option"
                aria-selected={option.value === value}
                disabled={option.disabled}
                onClick={() => choose(option)}
              >
                <span>{option.label}</span>
                {option.description && <small>{option.description}</small>}
              </button>
            </div>
          );
        })}
        {!filteredOptions.length && <p className="custom-select-empty">No matching options</p>}
      </div>
    </div>,
    document.body,
  ) : null;

  return (
    <div className={`custom-select ${className}`.trim()}>
      <button
        ref={triggerRef}
        type="button"
        className="custom-select-trigger"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        disabled={disabled}
        title={title}
        value={value}
        onClick={() => open ? close() : show()}
        onKeyDown={onTriggerKeyDown}
      >
        <span className={selected ? "" : "is-placeholder"}>{selected?.label ?? placeholder}</span>
        <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m5 7.5 5 5 5-5" /></svg>
      </button>
      {menu}
    </div>
  );
}
