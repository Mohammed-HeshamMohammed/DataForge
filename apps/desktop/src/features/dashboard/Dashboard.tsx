import type { Navigate } from "../../app/App.tsx";
import { useService } from "../../lib/hooks.ts";
import { isActive } from "../../lib/format.ts";
import type { Dataset, Job } from "../../lib/types.ts";
import { Metric } from "../../components/ui.tsx";

export function Dashboard({ navigate }: { navigate: Navigate }) {
  const datasets = useService<Dataset[]>("dataset.list");
  const jobs = useService<Job[]>("job.list", { limit: 50 }, 3000);
  const health = useService<{ status: string; service: string }>("health.check");

  const list = jobs.data ?? [];
  const matchJobs = list.filter((j) => j.kind === "match" && j.state === "completed" && j.params.run_mode === "full");

  return (
    <div className="stack">
      <div className="metrics">
        <Metric label="Datasets" value={datasets.data?.length} />
        <Metric label="Active jobs" value={list.filter((j) => isActive(j.state)).length} icon="▶" />
        <Metric label="Failed jobs" value={list.filter((j) => j.state === "failed").length} icon="✕" />
        <Metric label="Service" value={health.data?.status ?? "…"} icon={health.data?.status === "ok" ? "✓" : "!"} />
      </div>

      <div className="dashboard-grid">
      <section className="panel">
        <h2>Start</h2>
        <div className="row-actions">
          <button type="button" className="btn btn-primary" onClick={() => navigate("datasets")}>
            Import a file
          </button>
          <button type="button" className="btn" onClick={() => navigate("scraping")}>
            Collect from a website
          </button>
          <button type="button" className="btn" onClick={() => navigate("match")}>
            Match & deduplicate
          </button>
          <button type="button" className="btn" onClick={() => navigate("studio")}>
            Open Scrape Studio
          </button>
        </div>
        <p className="muted small">Tip: press Ctrl+K to search every command, or use the menus in the title bar.</p>
      </section>

      {matchJobs.length > 0 && (
        <section className="panel">
          <h2>Completed match jobs</h2>
          <ul className="plain-list">
            {matchJobs.slice(0, 5).map((job) => (
              <li key={job.id}>
                <button type="button" className="card-button" onClick={() => navigate("match", { datasetId: String(job.params.dataset_id), jobId: job.id })}>
                  <strong>{job.result?.metrics?.input_rows?.toLocaleString()} rows</strong>
                  <span className="muted">
                    {job.result?.metrics?.decisions?.match ?? 0} safe matches · {job.result?.metrics?.decisions?.possible_match ?? 0} flagged for review
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      </div>

    </div>
  );
}
