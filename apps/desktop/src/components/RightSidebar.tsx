import { useState } from "react";
import type { Navigate } from "../app/App.tsx";
import { call } from "../lib/ipc.ts";
import { useService } from "../lib/hooks.ts";
import { formatTime, isActive, JOB_KIND_LABELS } from "../lib/format.ts";
import type { Job } from "../lib/types.ts";
import { ErrorNote, StateBadge } from "./ui.tsx";
import { InfoIcon, RefreshIcon } from "./icons.tsx";

export function RightSidebar({ navigate, onChanged }: { navigate: Navigate; onChanged: () => void }) {
  const jobs = useService<Job[]>("job.list", { limit: 30 }, 1500);
  const health = useService<{ status: string; checked_at: string }>("health.check", {}, 15000);
  const [error, setError] = useState<string | null>(null);
  const list = jobs.data ?? [];
  const active = list.filter((j) => isActive(j.state));
  const recent = list.filter((j) => !isActive(j.state));

  const act = async (command: string, jobId: string) => {
    try {
      await call(command, { job_id: jobId });
      setError(null);
      await jobs.reload();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const renderJob = (job: Job) => (
    <li key={job.id} className={`job-card job-chip-${job.state}`}>
      <div className="list-item-head">
        <span className="list-item-title">
          {JOB_KIND_LABELS[job.kind] ?? job.kind}
          {typeof job.params.run_mode === "string" && <span className="muted"> · {job.params.run_mode}</span>}
        </span>
        <StateBadge state={job.state} />
      </div>
      <span className="list-item-sub">{formatTime(job.created_at)}</span>
      {job.error && <span className="list-item-sub note-error" title={job.error}>{job.error}</span>}
      <div className="job-card-actions">
        {job.state === "running" && <button type="button" className="btn btn-small" onClick={() => void act("job.pause", job.id)}>Pause</button>}
        {job.state === "paused" && <button type="button" className="btn btn-small" onClick={() => void act("job.resume", job.id)}>Resume</button>}
        {isActive(job.state) && (
          <button type="button" className="btn btn-small btn-danger" title="Stops at a safe checkpoint; source data is unchanged" onClick={() => void act("job.cancel", job.id)}>
            Cancel
          </button>
        )}
        {(job.state === "failed" || job.state === "cancelled") && <button type="button" className="btn btn-small" onClick={() => void act("job.retry", job.id)}>Retry</button>}
        {job.kind === "match" && job.state === "completed" && (
          <button type="button" className="btn btn-small" onClick={() => navigate("match", { datasetId: String(job.params.dataset_id), jobId: job.params.run_mode === "full" ? job.id : undefined })}>
            Open
          </button>
        )}
        {job.state === "completed" && job.result?.dataset_id && (
          <button type="button" className="btn btn-small" onClick={() => navigate("datasets", { datasetId: String(job.result!.dataset_id) })}>
            View data
          </button>
        )}
      </div>
    </li>
  );

  return (
    <aside className="sidebar sidebar-right" aria-label="Job center">
      <section>
        <div className="section-label-row">
          <h2 className="section-label">Active jobs</h2>
          <span className="count-badge">{active.length}</span>
        </div>
        <ErrorNote message={error ?? jobs.error} />
        {active.length === 0 ? (
          <p className="info-box">
            <InfoIcon size={16} /> Nothing running. Progress for imports, scrapes, and match jobs appears here.
          </p>
        ) : (
          <ul className="plain-list">{active.map(renderJob)}</ul>
        )}
      </section>

      <section className="sidebar-grow">
        <div className="section-label-row">
          <h2 className="section-label">Recent jobs</h2>
          <span className="count-badge">{recent.length}</span>
        </div>
        {recent.length === 0 ? <p className="muted small">No finished jobs yet.</p> : <ul className="plain-list scroll-list tall">{recent.map(renderJob)}</ul>}
      </section>

      <div className="profile-card">
        <span className={`status-orb ${health.data?.status === "ok" ? "status-orb-ok" : "status-orb-warn"}`} aria-hidden="true" />
        <div className="profile-text">
          <strong>Local service</strong>
          <span className="list-item-sub">{health.data ? `Checked ${new Date(health.data.checked_at).toLocaleTimeString()}` : health.error ? "Unavailable" : "Checking…"}</span>
          <span className={health.data?.status === "ok" ? "role-badge" : "status-badge"}>{health.data?.status ?? "…"}</span>
        </div>
        <button type="button" className="icon-btn-plain" aria-label="Check service" onClick={() => void health.reload()}>
          <RefreshIcon size={18} />
        </button>
      </div>
    </aside>
  );
}
