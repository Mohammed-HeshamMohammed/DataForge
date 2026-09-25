import { useService } from "../lib/hooks.ts";
import { formatCount } from "../lib/format.ts";
import type { Project } from "../lib/types.ts";
import type { ProjectSummary } from "./Sidebar.tsx";
import type { Navigate } from "../app/App.tsx";

/** IDE-style indicator strip along the bottom edge: project, service health, jobs, review load, and version. */
export function StatusBar({ project, summary, navigate, version }: { project: Project; summary: ProjectSummary | null; navigate: Navigate; version: string }) {
  const health = useService<{ status: string; checked_at: string }>("health.check", {}, 15000);
  const serviceState = health.data?.status === "ok" ? "ok" : health.error ? "down" : "checking";
  const running = summary?.active_jobs.length ?? 0;
  const failed = summary?.recent_failed.length ?? 0;
  const review = summary?.totals.pending_review ?? 0;

  return (
    <footer className="statusbar" aria-label="Status bar">
      <div className="statusbar-group">
        <span className="statusbar-item statusbar-project" title={project.root_path}>
          {project.name}
        </span>
        <span className="statusbar-item" title={health.data ? `Checked ${new Date(health.data.checked_at).toLocaleTimeString()}` : undefined}>
          <span className={`statusbar-dot statusbar-dot-${serviceState}`} aria-hidden="true" />
          {serviceState === "ok" ? "Service ready" : serviceState === "down" ? "Service unavailable" : "Checking service…"}
        </span>
        <button type="button" className="statusbar-item statusbar-button" onClick={() => navigate("dashboard")} title="Jobs running now">
          {running > 0 ? <span className="statusbar-spinner" aria-hidden="true" /> : null}
          {running > 0 ? `${running} running` : "Idle"}
        </button>
        {failed > 0 && (
          <span className="statusbar-item statusbar-warn" title="Recent jobs that failed">
            {failed} failed
          </span>
        )}
      </div>
      <div className="statusbar-group">
        <button type="button" className="statusbar-item statusbar-button" onClick={() => navigate("match")} title="Open the review queue">
          {formatCount(review)} to review
        </button>
        <span className="statusbar-item">
          {formatCount(summary?.datasets.length ?? 0)} datasets · {formatCount(summary?.totals.rows ?? 0)} rows
        </span>
        <span className="statusbar-item">v{version}</span>
      </div>
    </footer>
  );
}
