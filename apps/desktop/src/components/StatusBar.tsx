import type { ReactNode } from "react";
import type { Navigate } from "../app/App.tsx";
import { useService } from "../lib/hooks.ts";
import { formatCount, JOB_KIND_LABELS } from "../lib/format.ts";
import type { UpdateInfo } from "../lib/desktop.ts";
import type { Project } from "../lib/types.ts";
import type { ProjectSummary } from "./Sidebar.tsx";
import { ActivityIcon, AlertIcon, DownloadIcon, ErrorIcon, FolderIcon, LogoIcon, ReviewIcon, SidebarIcon, TableIcon, ZoomIcon } from "./icons.tsx";

type Tone = "muted" | "critical" | "warning" | "accent";

/** One status bar indicator: an icon, an optional count or short text, and a tooltip naming what it means. */
function Indicator({ icon, value, label, tone = "muted", onClick, pressed, busy }: {
  icon: ReactNode; value?: ReactNode; label: string; tone?: Tone; onClick?: () => void; pressed?: boolean; busy?: boolean;
}) {
  return (
    <button type="button" className={`statusbar-item tone-${tone}${busy ? " is-busy" : ""}`} title={label} aria-label={label} aria-pressed={pressed} onClick={onClick} disabled={!onClick}>
      {icon}
      {value !== undefined && <span className="statusbar-value">{value}</span>}
    </button>
  );
}

export type StatusBarProps = {
  project: Project | null;
  summary: ProjectSummary | null;
  navigate?: Navigate;
  layout: { left: boolean; right: boolean };
  onToggleLeft?: () => void;
  onToggleRight?: () => void;
  zoom: number;
  onResetZoom: () => void;
  update: UpdateInfo | null;
  onUpdate?: () => void;
  onProjectFolder?: () => void;
  onAbout: () => void;
  version: string;
};

/** The strip along the bottom of the window: live indicators for the service, project, jobs, and review queue. */
export function StatusBar({ project, summary, navigate, layout, onToggleLeft, onToggleRight, zoom, onResetZoom, update, onUpdate, onProjectFolder, onAbout, version }: StatusBarProps) {
  const health = useService<{ status: string; checked_at: string }>("health.check", {}, 15000);
  const online = !health.error && (health.data?.status ?? "ok") === "ok";
  const checked = health.data ? ` · checked ${new Date(health.data.checked_at).toLocaleTimeString()}` : "";

  const active = summary?.active_jobs ?? [];
  const failed = summary?.recent_failed ?? [];
  const unmapped = (summary?.datasets ?? []).filter((d) => !d.mapping_version).length;
  const review = summary?.totals.pending_review ?? 0;
  const datasets = summary?.datasets.length ?? 0;
  const rows = summary?.totals.rows ?? 0;
  const running = active.map((job) => JOB_KIND_LABELS[job.kind] ?? job.kind);
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

  return (
    <footer className="statusbar" aria-label="Status bar">
      <div className="statusbar-group">
        <button
          type="button"
          className={`statusbar-origin${online ? "" : " is-offline"}`}
          title={online ? `Local service running: everything stays on this computer${checked}` : `The local DataForge service is not responding${health.error ? `: ${health.error}` : ""}`}
          aria-label={online ? "Local service running" : "Local service offline"}
          onClick={() => void health.reload()}
        >
          <LogoIcon size={14} />
          <span>{online ? "Local" : "Offline"}</span>
        </button>
        {project && (
          <Indicator icon={<FolderIcon size={14} />} value={<span className="statusbar-project">{project.name}</span>} label={`Project ${project.name}: ${project.root_path}`} onClick={onProjectFolder} />
        )}
        {summary && (
          <>
            <Indicator
              icon={<ErrorIcon size={14} />}
              value={failed.length}
              label={failed.length ? `${plural(failed.length, "recent job")} failed. Open the Overview to see why` : "No recent failed jobs"}
              tone={failed.length ? "critical" : "muted"}
              onClick={navigate && (() => navigate("dashboard"))}
            />
            <Indicator
              icon={<AlertIcon size={14} />}
              value={unmapped}
              label={unmapped ? `${plural(unmapped, "dataset")} need a confirmed mapping before matching` : "Every dataset has a confirmed mapping"}
              tone={unmapped ? "warning" : "muted"}
              onClick={navigate && (() => navigate("datasets"))}
            />
            <Indicator
              icon={<ActivityIcon size={14} />}
              value={active.length || undefined}
              label={active.length ? `Running: ${running.join(", ")}. ${layout.right ? "Hide" : "Show"} the job center` : `No jobs running. ${layout.right ? "Hide" : "Show"} the job center`}
              tone={active.length ? "accent" : "muted"}
              busy={active.length > 0}
              pressed={layout.right}
              onClick={onToggleRight}
            />
          </>
        )}
      </div>
      <div className="statusbar-group">
        {summary && (
          <>
            <Indicator
              icon={<ReviewIcon size={14} />}
              value={formatCount(review)}
              label={review ? `${plural(review, "pair")} waiting for review` : "Review queue is clear"}
              tone={review ? "accent" : "muted"}
              onClick={navigate && (() => navigate("match"))}
            />
            <Indicator
              icon={<TableIcon size={14} />}
              value={datasets}
              label={`${plural(datasets, "dataset")}, ${formatCount(rows)} rows`}
              onClick={navigate && (() => navigate("datasets"))}
            />
          </>
        )}
        {Math.round(zoom * 100) !== 100 && <Indicator icon={<ZoomIcon size={14} />} value={`${Math.round(zoom * 100)}%`} label={`Zoom ${Math.round(zoom * 100)}%. Reset to 100%`} onClick={onResetZoom} />}
        {update?.available && (
          <Indicator icon={<DownloadIcon size={14} />} value={update.version} label={`DataForge ${update.version ?? ""} is available. Open update settings`} tone="accent" onClick={onUpdate} />
        )}
        {project && (
          <>
            <Indicator icon={<SidebarIcon size={14} />} label={`${layout.left ? "Hide" : "Show"} the project sidebar`} pressed={layout.left} onClick={onToggleLeft} />
            <Indicator icon={<SidebarIcon size={14} className="statusbar-mirror" />} label={`${layout.right ? "Hide" : "Show"} the job center`} pressed={layout.right} onClick={onToggleRight} />
          </>
        )}
        <Indicator icon={<span className="statusbar-version">v{version}</span>} label={`DataForge ${version}. About DataForge`} onClick={onAbout} />
      </div>
    </footer>
  );
}
