import { useState } from "react";
import type { Navigate } from "../app/App.tsx";
import { formatCount } from "../lib/format.ts";
import type { Job, Project } from "../lib/types.ts";

export type DatasetSummary = {
  id: string;
  name: string;
  kind: string;
  row_count: number;
  mapping_version: number | null;
  job_id?: string;
  pending_review?: number;
  reviewed?: number;
  safe_matches?: number;
  canonical_records?: number;
};

export type ProjectSummary = {
  datasets: DatasetSummary[];
  totals: { pending_review: number; reviewed: number; safe_matches: number; rows: number };
  active_jobs: Job[];
  recent_failed: Job[];
  job_counts: Record<string, number>;
};

function ProgressRing({ percent }: { percent: number }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  return (
    <svg className="ring" width="64" height="64" viewBox="0 0 64 64" role="img" aria-label={`${percent}% of review items resolved`}>
      <circle cx="32" cy="32" r={radius} className="ring-track" />
      <circle cx="32" cy="32" r={radius} className="ring-value" strokeDasharray={circumference} strokeDashoffset={circumference * (1 - percent / 100)} transform="rotate(-90 32 32)" />
      <text x="32" y="36" textAnchor="middle" className="ring-text">
        {percent}%
      </text>
    </svg>
  );
}

function datasetStatus(d: DatasetSummary): { badge?: string; percent?: number; sub: string } {
  if (!d.mapping_version) return { badge: "Unmapped", sub: `${formatCount(d.row_count)} rows` };
  if (!d.job_id) return { badge: "Not matched", sub: `${formatCount(d.row_count)} rows · mapping v${d.mapping_version}` };
  const total = (d.pending_review ?? 0) + (d.reviewed ?? 0);
  const percent = total ? Math.round(((d.reviewed ?? 0) / total) * 100) : 100;
  return { percent, sub: `${formatCount(d.canonical_records)} canonical · ${formatCount(d.pending_review)} to review` };
}

export function Sidebar({ summary, navigate }: { project?: Project; summary: ProjectSummary | null; navigate: Navigate }) {
  const [filter, setFilter] = useState("");
  const totals = summary?.totals;
  const reviewTotal = (totals?.pending_review ?? 0) + (totals?.reviewed ?? 0);
  const percent = reviewTotal ? Math.round(((totals?.reviewed ?? 0) / reviewTotal) * 100) : 100;
  const datasets = (summary?.datasets ?? []).filter((d) => d.name.toLowerCase().includes(filter.toLowerCase()));

  return (
    <aside className="sidebar" aria-label="Project">
      <section>
        <h2 className="section-label">Review progress</h2>
        <div className="activity">
          <ProgressRing percent={percent} />
          <ul className="plain-list activity-legend">
            <li>
              <span className="dot dot-green" aria-hidden="true" />
              <strong>{formatCount(totals?.safe_matches ?? 0)}</strong> safe matches
            </li>
            <li>
              <span className="dot dot-amber" aria-hidden="true" />
              <strong>{formatCount(totals?.pending_review ?? 0)}</strong> to review
            </li>
            <li>
              <span className="dot dot-grey" aria-hidden="true" />
              <strong>{formatCount(totals?.rows ?? 0)}</strong> rows
            </li>
          </ul>
        </div>
      </section>

      <section className="sidebar-datasets">
        <div className="section-label-row">
          <h2 className="section-label">Your datasets</h2>
          <span className="count-badge">{summary?.datasets.length ?? 0}</span>
        </div>
        <input id="dataset-filter" className="filter-input" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter datasets..." aria-label="Filter datasets" />
        <ul className="plain-list scroll-list">
          {datasets.length === 0 && <li className="muted small list-empty">{summary?.datasets.length ? "No matches." : "No datasets yet."}</li>}
          {datasets.map((d) => {
            const status = datasetStatus(d);
            return (
              <li key={d.id}>
                <button type="button" className="list-item" onClick={() => (d.job_id ? navigate("match", { datasetId: d.id, jobId: d.job_id }) : navigate("datasets", { datasetId: d.id }))}>
                  <span className="list-item-head">
                    <span className="list-item-title">{d.name}</span>
                    {status.percent !== undefined ? <span className="list-item-meta">{status.percent}%</span> : <span className="status-badge">{status.badge}</span>}
                  </span>
                  {status.percent !== undefined && (
                    <span className="bar" aria-hidden="true">
                      <span style={{ width: `${Math.max(status.percent, 3)}%` }} />
                    </span>
                  )}
                  <span className="list-item-sub">{status.sub}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

    </aside>
  );
}
