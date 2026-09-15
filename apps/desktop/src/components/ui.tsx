import { useEffect, useRef, useState, type ReactNode } from "react";
import { formatCount, stageLabel } from "../lib/format.ts";
import type { Job, JobState } from "../lib/types.ts";

const STATE_ICON: Record<JobState, string> = {
  draft: "○",
  validating: "◌",
  queued: "◷",
  running: "▶",
  paused: "❚❚",
  completed: "✓",
  failed: "✕",
  cancelled: "⊘",
};

export function StateBadge({ state }: { state: JobState }) {
  return (
    <span className={`badge badge-${state}`}>
      <span aria-hidden="true">{STATE_ICON[state]}</span> {state}
    </span>
  );
}

export function ErrorNote({ message }: { message: string | null | undefined }) {
  if (!message) return null;
  return (
    <p className="note note-error" role="alert">
      <span aria-hidden="true">✕ </span>
      {message}
    </p>
  );
}

export function Metric({ label, value, icon, hint }: { label: string; value: number | string | undefined | null; icon?: string; hint?: string }) {
  return (
    <div className="metric" title={hint}>
      <span className="metric-value">
        {icon && <span aria-hidden="true">{icon} </span>}
        {typeof value === "number" || value == null ? formatCount(value as number | null) : value}
      </span>
      <span className="metric-label">{label}</span>
    </div>
  );
}

export function latestStage(job: Job | null): { stage: string; detail: Record<string, unknown> } | null {
  const event = [...(job?.events ?? [])].reverse().find((e) => e.event_type === "job.stage_changed");
  return event ? { stage: String(event.payload.stage), detail: event.payload } : null;
}

export function JobProgress({ job }: { job: Job | null }) {
  if (!job) return <p className="muted">Starting…</p>;
  const stages: { stage: string; detail: Record<string, unknown> }[] = [];
  for (const event of job.events ?? []) {
    if (event.event_type !== "job.stage_changed") continue;
    const stage = String(event.payload.stage);
    const existing = stages.find((s) => s.stage === stage);
    if (existing) existing.detail = { ...existing.detail, ...event.payload };
    else stages.push({ stage, detail: event.payload });
  }
  return (
    <div aria-live="polite">
      <p>
        <StateBadge state={job.state} />
      </p>
      <ol className="stage-list">
        {stages.map((s, index) => {
          const current = index === stages.length - 1 && (job.state === "running" || job.state === "paused");
          const detail = Object.entries(s.detail)
            .filter(([k, v]) => k !== "stage" && (typeof v === "number" || typeof v === "string") && k !== "url")
            .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "number" ? formatCount(v) : v}`)
            .join(" · ");
          return (
            <li key={s.stage} className={current ? "stage-current" : "stage-done"}>
              <span aria-hidden="true">{current ? "▶" : "✓"}</span> {stageLabel(s.stage)}
              <span className="muted"> {current ? "Running" : "Complete"}</span>
              {detail && <span className="stage-detail">{detail}</span>}
            </li>
          );
        })}
      </ol>
      <ErrorNote message={job.error} />
    </div>
  );
}

/** Confirmation dialog for destructive actions. States what is affected and whether raw data remains. */
export function ConfirmButton({
  label,
  title,
  body,
  confirmLabel,
  onConfirm,
  danger = false,
  disabled = false,
}: {
  label: string;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  return (
    <>
      <button type="button" className={danger ? "btn btn-danger" : "btn"} disabled={disabled} onClick={() => dialog.current?.showModal()}>
        {label}
      </button>
      <dialog ref={dialog} className="dialog" aria-labelledby={`${label}-title`}>
        <h2 id={`${label}-title`}>{title}</h2>
        <div>{body}</div>
        <div className="row-actions">
          <button type="button" className="btn" autoFocus onClick={() => dialog.current?.close()}>
            Keep it
          </button>
          <button
            type="button"
            className={danger ? "btn btn-danger" : "btn btn-primary"}
            onClick={() => {
              dialog.current?.close();
              onConfirm();
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </dialog>
    </>
  );
}

export function useToast() {
  const [toast, setToast] = useState<{ message: string; action?: { label: string; run: () => void } } | null>(null);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 8000);
    return () => window.clearTimeout(timer);
  }, [toast]);
  const node = (
    <div className="toast-region" role="status" aria-live="polite">
      {toast && (
        <div className="toast">
          {toast.message}
          {toast.action && (
            <button type="button" className="btn btn-small" onClick={() => { toast.action!.run(); setToast(null); }}>
              {toast.action.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
  return { show: setToast, node };
}

export function PathInput({
  value,
  onChange,
  placeholder,
  onBrowse,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  onBrowse?: () => Promise<string | null>;
  label: string;
}) {
  return (
    <div className="path-input">
      <label className="field">
        <span>{label}</span>
        <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} spellCheck={false} />
      </label>
      {onBrowse && (
        <button type="button" className="btn" onClick={async () => { const picked = await onBrowse(); if (picked) onChange(picked); }}>
          Browse…
        </button>
      )}
    </div>
  );
}
