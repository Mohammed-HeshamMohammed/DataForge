import { useEffect, useState } from "react";
import { formatCount } from "../lib/format.ts";
import { RefreshIcon, ReviewIcon } from "./icons.tsx";

type TabDef<T extends string> = { id: T; label: string };

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

export function PaneHeader<T extends string>({
  title,
  tabs,
  activeTab,
  onTab,
  chip,
  onRefresh,
}: {
  title: string;
  tabs: TabDef<T>[];
  activeTab: T;
  onTab: (id: T) => void;
  chip: { label: string; value: number; onClick: () => void };
  onRefresh: () => void;
}) {
  const now = useClock();
  return (
    <div className="pane-header">
      <div className="pane-header-top">
        <div>
          <p className="pane-clock">
            <strong>{now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</strong>{" "}
            <span>{now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}</span>
          </p>
          <h1 id="pane-title" className="pane-title">
            {title}
          </h1>
        </div>
        <div className="pane-header-actions">
          <button type="button" className="chip" onClick={chip.onClick}>
            <ReviewIcon size={14} /> {chip.label} <strong>{formatCount(chip.value)}</strong>
          </button>
          <button type="button" className="icon-btn" onClick={onRefresh} aria-label="Refresh" title="Refresh">
            <RefreshIcon size={20} />
          </button>
        </div>
      </div>
      <nav className="pane-tabs" role="tablist" aria-label="Sections">
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={t.id === activeTab} className="pane-tab" onClick={() => onTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
