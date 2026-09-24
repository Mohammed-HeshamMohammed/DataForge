import { useState } from "react";
import type { Navigate, Tab } from "../../app/App.tsx";
import { useService } from "../../lib/hooks.ts";
import { compactNumber, formatBytes, formatCount, niceMax, relativeTime } from "../../lib/format.ts";
import { ErrorNote } from "../../components/ui.tsx";

type Activity = { date: string; records: number; runs: number; failed: number; detail_pages: number };
type JobView = { id: string; kind: string; label: string; state: string; created_at: string; updated_at: string; summary: string; dataset_id?: string | null };
type RunView = {
  job_id: string; preset_id: string; preset_version: string; source_kind: string | null; host: string; run_mode: string | null; status: string; engine: string | null;
  records: number; pages: number; detail_pages: number; detail_level: string | null; detail_fields: number; created_at: string; dataset_id: string | null;
  stop_reason: string | null; error: string | null;
};
type Attention = { kind: string; severity: "critical" | "warning" | "info"; count: number; target: Tab; message: string; detail: string | null; dataset_id?: string };
export type Overview = {
  generated_at: string;
  app: { version: string };
  project: { id: string; name: string; root_path: string; created_at: string | null };
  collection: { runs: number; full_runs: number; test_runs: number; failed_runs: number; records: number; records_last_7_days: number; records_previous_7_days: number; pages: number; detail_pages: number; hosts: number };
  activity: Activity[];
  by_source: { source: string; records: number; runs: number }[];
  top_hosts: { host: string; records: number; runs: number; last_run: string }[];
  top_presets: { preset_id: string; version: string; records: number; runs: number; last_run: string }[];
  recent_runs: RunView[];
  datasets: { count: number; rows: number; scraped: number; imported: number; unmapped: number;
    items: { id: string; name: string; kind: string; row_count: number; column_count: number; created_at: string; mapping_version: number | null; match_job_id?: string; pending_review?: number; reviewed?: number; canonical_records?: number }[] };
  review: { pending: number; reviewed: number; safe_matches: number; canonical_records: number; matched_datasets: number; exports: number; last_export_at: string | null };
  jobs: { counts: Record<string, number>; active: JobView[]; recent_failed: JobView[]; recent: JobView[] };
  watches: { active: number; paused: number; items: { id: string; name: string; status: string; interval_minutes: number; next_run_at: string | null; last_state: string | null; last_changes: Record<string, number> | null }[] };
  presets: { counts: Record<string, number>; total: number; custom: number; attention: { id: string; version: string; display_name: string; status: string; reason: string | null }[] };
  site_limits: { host: string; reasons: string[]; checked_at: string }[];
  storage: { database_bytes: number; total_bytes: number; areas: { area: string; label: string; bytes: number }[] };
  attention: Attention[];
  checklist: { id: string; label: string; done: boolean; target: Tab }[];
};

const SOURCE_LABELS: Record<string, string> = {
  website: "Websites", sitemap: "Sitemaps", feed: "Feeds", crawl: "Site crawls", llms_txt: "llms.txt", api: "Open data APIs", studio: "Scrape Studio", archive: "Web archives",
};
const SEVERITY_ICON: Record<Attention["severity"], string> = { critical: "✕", warning: "!", info: "i" };
const STATUS_ICON: Record<string, string> = { completed: "✓", failed: "✕", started: "▶" };
const OUTCOMES: Record<string, string> = {
  completed: "Completed", missing_continuation: "Completed (no next page)", no_new_records: "Completed (no new records)", repeated_canonical_url: "Completed",
  repeated_response: "Completed", frontier_exhausted: "Completed (crawl finished)", max_records: "Stopped at the record limit", max_pages: "Stopped at the page limit",
  max_duration: "Stopped at the time limit", cancelled: "Cancelled",
};

export function outcome(stopReason: string | null | undefined): string {
  return OUTCOMES[stopReason ?? "completed"] ?? (stopReason ?? "completed").replace(/_/g, " ");
}

export function Dashboard({ navigate }: { navigate: Navigate }) {
  const [tzOffset] = useState(() => new Date().getTimezoneOffset());
  const data = useService<Overview>("project.overview", { days: 14, tz_offset_minutes: tzOffset }, 5000);
  const health = useService<{ status: string; service: string }>("health.check");
  const o = data.data;

  if (!o) {
    return (
      <div className="stack">
        <ErrorNote message={data.error} />
        {!data.error && <p className="muted">Loading the overview…</p>}
      </div>
    );
  }
  const weekDelta = o.collection.records_last_7_days - o.collection.records_previous_7_days;
  const checklistOpen = o.checklist.some((item) => !item.done);

  return (
    <div className="stack overview">
      <header className="overview-head">
        <div>
          <h2 className="overview-title">{o.project.name}</h2>
          <p className="muted small">
            <code>{o.project.root_path}</code> · DataForge {o.app.version} · service {health.data?.status === "ok" ? "running" : (health.data?.status ?? "…")} · updated {relativeTime(o.generated_at)}
          </p>
        </div>
        <div className="row-actions">
          <button type="button" className="btn btn-primary" onClick={() => navigate("scraping")}>
            Collect from a website
          </button>
          <button type="button" className="btn" onClick={() => navigate("studio")}>
            Open Scrape Studio
          </button>
          <button type="button" className="btn" onClick={() => navigate("datasets")}>
            Import a file
          </button>
          <button type="button" className="btn" onClick={() => navigate("match")}>
            Match & deduplicate
          </button>
        </div>
      </header>

      <div className="stat-row" role="list" aria-label="Project totals">
        <StatTile label="Records collected" value={o.collection.records}
          sub={o.collection.records_last_7_days ? `${compactNumber(o.collection.records_last_7_days)} in the last 7 days${weekDelta ? ` (${weekDelta > 0 ? "+" : "−"}${compactNumber(Math.abs(weekDelta))} vs the week before)` : ""}` : "none in the last 7 days"} />
        <StatTile label="Detail pages read" value={o.collection.detail_pages} sub={`${compactNumber(o.collection.pages)} listing pages · ${o.collection.hosts} site${o.collection.hosts === 1 ? "" : "s"}`} />
        <StatTile label="Datasets" value={o.datasets.count} sub={`${compactNumber(o.datasets.rows)} rows · ${o.datasets.scraped} scraped, ${o.datasets.imported} imported`} />
        <StatTile label="Waiting for review" value={o.review.pending} sub={`${compactNumber(o.review.safe_matches)} safe matches · ${compactNumber(o.review.canonical_records)} canonical records`} />
      </div>

      <div className="overview-grid">
        <section className="panel overview-wide" aria-labelledby="activity-heading">
          <h2 id="activity-heading">Records collected per day</h2>
          <p className="muted small">Full runs over the last {o.activity.length} days, in your time zone. Hover or focus a day for runs and detail pages.</p>
          <ActivityChart activity={o.activity} />
        </section>

        <section className="panel" aria-labelledby="attention-heading">
          <h2 id="attention-heading">Needs attention</h2>
          {o.attention.length === 0 ? (
            <p className="muted">
              <span aria-hidden="true">✓ </span>Nothing needs attention.
            </p>
          ) : (
            <ul className="plain-list attention-list">
              {o.attention.map((item, index) => (
                <li key={`${item.kind}-${index}`} className={`attention attention-${item.severity}`}>
                  <span className="attention-icon" aria-hidden="true">
                    {SEVERITY_ICON[item.severity]}
                  </span>
                  <span className="attention-text">
                    <span className="sr-only">{item.severity === "critical" ? "Problem: " : item.severity === "warning" ? "Warning: " : "Note: "}</span>
                    <strong>{item.message}</strong>
                    {item.detail && <span className="muted small">{item.detail}</span>}
                  </span>
                  <button type="button" className="btn btn-small" onClick={() => navigate(item.target, item.dataset_id ? { datasetId: item.dataset_id } : {})}>
                    Open
                  </button>
                </li>
              ))}
            </ul>
          )}
          {checklistOpen && (
            <>
              <h3 className="section-label">Getting started</h3>
              <ol className="plain-list checklist">
                {o.checklist.map((item) => (
                  <li key={item.id} className={item.done ? "done" : ""}>
                    <span aria-hidden="true">{item.done ? "✓" : "○"}</span>
                    <span className="sr-only">{item.done ? "Done: " : "To do: "}</span>
                    {item.done ? (
                      <span>{item.label}</span>
                    ) : (
                      <button type="button" className="btn-link" onClick={() => navigate(item.target)}>
                        {item.label}
                      </button>
                    )}
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>

        <section className="panel" aria-labelledby="sources-heading">
          <h2 id="sources-heading">Where records come from</h2>
          <BarList
            label="Records by source"
            items={o.by_source.map((s) => ({ key: s.source, label: SOURCE_LABELS[s.source] ?? s.source, value: s.records, hint: `${s.runs} run${s.runs === 1 ? "" : "s"}` }))}
            empty="Nothing collected yet."
          />
          <h3 className="section-label">Top sites</h3>
          <BarList label="Records by site" items={o.top_hosts.map((h) => ({ key: h.host, label: h.host, value: h.records, hint: `${h.runs} run${h.runs === 1 ? "" : "s"}, last ${relativeTime(h.last_run)}` }))} empty="—" />
          <h3 className="section-label">Most used presets</h3>
          <BarList label="Records by preset" items={o.top_presets.map((p) => ({ key: p.preset_id, label: p.preset_id, value: p.records, hint: `${p.runs} run${p.runs === 1 ? "" : "s"}` }))} empty="—" />
        </section>

        <section className="panel overview-wide" aria-labelledby="runs-heading">
          <div className="section-head">
            <h2 id="runs-heading">Recent collection runs</h2>
            <button type="button" className="btn btn-small" onClick={() => navigate("scraping")}>
              Go to Scraping
            </button>
          </div>
          {o.recent_runs.length === 0 ? (
            <p className="muted">No collection runs yet. Test a preset on a permitted website, then run it in full.</p>
          ) : (
            <div className="table-wrap">
              <table className="data-table compact">
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Preset and site</th>
                    <th scope="col" className="numeric">Records</th>
                    <th scope="col" className="numeric">Detail pages</th>
                    <th scope="col">Result</th>
                    <th scope="col">Dataset</th>
                  </tr>
                </thead>
                <tbody>
                  {o.recent_runs.map((run) => (
                    <tr key={run.job_id}>
                      <td title={run.created_at}>{relativeTime(run.created_at)}</td>
                      <td>
                        {run.preset_id}
                        <span className="muted small">
                          {run.host ? ` · ${run.host}` : ""}
                          {run.run_mode === "test" ? " · test" : ""}
                        </span>
                      </td>
                      <td className="numeric">{formatCount(run.records)}</td>
                      <td className="numeric">{run.detail_pages ? formatCount(run.detail_pages) : run.detail_level === "full" ? "0" : "—"}</td>
                      <td title={run.error ?? undefined}>
                        <span aria-hidden="true">{STATUS_ICON[run.status] ?? "•"} </span>
                        {run.status === "failed" ? `Failed${run.error ? `: ${run.error.replace(/^Collection stopped: /, "").slice(0, 32)}${run.error.length > 52 ? "…" : ""}` : ""}` : run.status === "completed" ? outcome(run.stop_reason) : "Running"}
                      </td>
                      <td>
                        {run.dataset_id ? (
                          <button type="button" className="btn btn-small" onClick={() => navigate("datasets", { datasetId: run.dataset_id! })}>
                            Open
                          </button>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="panel" aria-labelledby="datasets-heading">
          <div className="section-head">
            <h2 id="datasets-heading">Datasets</h2>
            <button type="button" className="btn btn-small" onClick={() => navigate("datasets")}>
              All datasets
            </button>
          </div>
          {o.datasets.items.length === 0 ? (
            <p className="muted">No datasets yet. Import a CSV, XLSX, or JSON file, or run a full collection.</p>
          ) : (
            <ul className="plain-list">
              {o.datasets.items.map((d) => {
                const decided = (d.reviewed ?? 0) + (d.pending_review ?? 0);
                return (
                  <li key={d.id}>
                    <button type="button" className="card-button" onClick={() => navigate(d.match_job_id ? "match" : "datasets", { datasetId: d.id, jobId: d.match_job_id })}>
                      <strong>{d.name}</strong>
                      <span className="muted small">
                        {d.kind === "scrape" ? "Scraped" : "Imported"} · {formatCount(d.row_count)} rows · {d.column_count} columns · {d.mapping_version ? `mapping v${d.mapping_version}` : "no mapping yet"}
                      </span>
                      {decided > 0 && (
                        <span className="meter-line small">
                          <span className="meter" role="img" aria-label={`${d.reviewed} of ${decided} review decisions made`}>
                            <span className="meter-fill" style={{ width: `${Math.round(((d.reviewed ?? 0) / decided) * 100)}%` }} />
                          </span>
                          {d.pending_review ? `${d.pending_review} to review` : "review complete"}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="muted small">
            {o.review.exports ? `${o.review.exports} export${o.review.exports === 1 ? "" : "s"}, last ${relativeTime(o.review.last_export_at)}` : "No exports yet"}
          </p>
        </section>

        <section className="panel" aria-labelledby="jobs-heading">
          <h2 id="jobs-heading">Jobs</h2>
          <p className="muted small">
            {o.jobs.active.length} running · {formatCount(o.jobs.counts.completed ?? 0)} completed · {formatCount(o.jobs.counts.failed ?? 0)} failed · {formatCount(o.jobs.counts.cancelled ?? 0)} cancelled
          </p>
          <ul className="plain-list job-lines">
            {[...o.jobs.active, ...o.jobs.recent.filter((j) => !o.jobs.active.some((a) => a.id === j.id))].slice(0, 6).map((job) => (
              <li key={job.id}>
                <span aria-hidden="true">{job.state === "completed" ? "✓" : job.state === "failed" ? "✕" : job.state === "cancelled" ? "–" : "▶"} </span>
                <strong>{job.label}</strong> <span className="muted">{job.state}</span>
                <span className="muted small"> · {relativeTime(job.updated_at)}</span>
                <span className="small job-summary">{job.summary}</span>
              </li>
            ))}
            {o.jobs.recent.length === 0 && <li className="muted">No jobs yet.</li>}
          </ul>
        </section>

        <section className="panel" aria-labelledby="watches-heading">
          <h2 id="watches-heading">Watches</h2>
          <p className="muted small">
            {o.watches.active} active · {o.watches.paused} paused. Watches re-collect on a schedule while DataForge is open and report what changed.
          </p>
          <ul className="plain-list job-lines">
            {o.watches.items.map((w) => (
              <li key={w.id}>
                <strong>{w.name}</strong> <span className="muted">{w.status}</span>
                <span className="small job-summary">
                  {w.status === "active" && w.next_run_at ? `Next run ${relativeTime(w.next_run_at)}` : "Not scheduled"}
                  {w.last_changes ? ` · last run: ${w.last_changes.added ?? 0} added, ${w.last_changes.changed ?? 0} changed, ${w.last_changes.removed ?? 0} removed` : ""}
                  {w.last_state === "failed" ? " · last run failed" : ""}
                </span>
              </li>
            ))}
            {o.watches.items.length === 0 && <li className="muted">No watches. After a successful test run, create one from the Scraping tab.</li>}
          </ul>
        </section>

        <section className="panel" aria-labelledby="presets-heading">
          <div className="section-head">
            <h2 id="presets-heading">Presets</h2>
            <button type="button" className="btn btn-small" onClick={() => navigate("settings")}>
              Preset health
            </button>
          </div>
          <p className="small">
            {o.presets.total} presets · {o.presets.counts.active ?? 0} active · {o.presets.counts.degraded ?? 0} degraded · {o.presets.counts.disabled ?? 0} disabled · {o.presets.counts.deprecated ?? 0} deprecated ·{" "}
            {o.presets.custom} custom
          </p>
          <ul className="plain-list job-lines">
            {o.presets.attention.map((p) => (
              <li key={`${p.id}@${p.version}`}>
                <span aria-hidden="true">{p.status === "disabled" ? "✕" : "!"} </span>
                <strong>{p.display_name}</strong> <span className="muted">{p.status}</span>
                {p.reason && <span className="small job-summary">{p.reason}</span>}
              </li>
            ))}
          </ul>
          {o.site_limits.length > 0 && (
            <>
              <h3 className="section-label">Sites that limit collection</h3>
              <ul className="plain-list job-lines">
                {o.site_limits.map((s) => (
                  <li key={s.host}>
                    <strong>{s.host}</strong>
                    <span className="small job-summary">{s.reasons.join("; ")}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        <section className="panel" aria-labelledby="storage-heading">
          <h2 id="storage-heading">Storage</h2>
          <p>
            <strong>{formatBytes(o.storage.total_bytes)}</strong> <span className="muted small">in the project folder</span>
          </p>
          <BarList
            label="Disk use by area"
            items={[{ key: "database", label: "Project database", value: o.storage.database_bytes, hint: "" }, ...o.storage.areas.map((a) => ({ key: a.area, label: a.label, value: a.bytes, hint: a.area }))]}
            format={formatBytes}
            empty="—"
          />
          <p className="muted small">Clear the HTTP cache from Settings → Collection. Raw imports and staged scrapes stay until you delete their datasets.</p>
        </section>
      </div>
    </div>
  );
}

function StatTile({ label, value, sub }: { label: string; value: number; sub: string }) {
  return (
    <div className="stat-tile" role="listitem">
      <span className="stat-label">{label}</span>
      <span className="stat-value" title={formatCount(value)}>
        {compactNumber(value)}
      </span>
      <span className="stat-sub">{sub}</span>
    </div>
  );
}

/** Horizontal bars for a few named categories: one hue, value at the tip, the name and runs in the tooltip. */
function BarList({ label, items, empty, format = compactNumber }: { label: string; items: { key: string; label: string; value: number; hint: string }[]; empty: string; format?: (v: number) => string }) {
  if (!items.length || items.every((i) => !i.value)) return <p className="muted small">{empty}</p>;
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <ul className="plain-list bar-list" aria-label={label}>
      {items.map((item) => (
        <li key={item.key} title={item.hint ? `${item.label}: ${format(item.value)} (${item.hint})` : `${item.label}: ${format(item.value)}`}>
          <span className="bar-list-label">{item.label}</span>
          <span className="bar-list-track">
            <span className="bar-list-fill" style={{ width: `${Math.max(2, (item.value / max) * 100)}%` }} />
          </span>
          <span className="bar-list-value">{format(item.value)}</span>
        </li>
      ))}
    </ul>
  );
}

function shortDate(iso: string): string {
  const date = new Date(`${iso}T12:00:00`);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Records collected per day: thin columns on one baseline, a hover/focus readout per day, and a table view. */
export function ActivityChart({ activity }: { activity: Activity[] }) {
  const [active, setActive] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const peak = Math.max(...activity.map((d) => d.records), 0);
  const top = niceMax(peak);
  const total = activity.reduce((sum, d) => sum + d.records, 0);
  const peakIndex = activity.findIndex((d) => d.records === peak && peak > 0);
  const last = activity.length - 1;
  const shown = active !== null ? activity[active] : null;

  return (
    <figure className="activity-chart">
      <div className="section-head">
        <figcaption className="small muted">
          {total ? `${formatCount(total)} records in ${activity.length} days${peak ? ` · busiest day ${shortDate(activity[peakIndex].date)} (${formatCount(peak)})` : ""}` : `No full runs in the last ${activity.length} days.`}
        </figcaption>
        <button type="button" className="btn btn-small" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </div>
      {asTable ? (
        <div className="table-wrap">
          <table className="data-table compact">
            <caption className="sr-only">Records collected per day</caption>
            <thead>
              <tr>
                <th scope="col">Day</th>
                <th scope="col" className="numeric">Records</th>
                <th scope="col" className="numeric">Runs</th>
                <th scope="col" className="numeric">Failed</th>
                <th scope="col" className="numeric">Detail pages</th>
              </tr>
            </thead>
            <tbody>
              {activity.map((d) => (
                <tr key={d.date}>
                  <th scope="row">{shortDate(d.date)}</th>
                  <td className="numeric">{formatCount(d.records)}</td>
                  <td className="numeric">{formatCount(d.runs)}</td>
                  <td className="numeric">{formatCount(d.failed)}</td>
                  <td className="numeric">{formatCount(d.detail_pages)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="activity-plot" onPointerLeave={() => setActive(null)}>
          <div className="activity-axis" aria-hidden="true">
            <span>{compactNumber(top)}</span>
            <span>{compactNumber(top / 2)}</span>
            <span>0</span>
          </div>
          <div className="activity-area">
            <span className="activity-grid" style={{ bottom: "100%" }} aria-hidden="true" />
            <span className="activity-grid" style={{ bottom: "50%" }} aria-hidden="true" />
            <div className="activity-columns" role="list" aria-label="Records collected per day">
              {activity.map((d, index) => {
                const height = (d.records / top) * 100;
                const labelled = d.records > 0 && (index === peakIndex || index === last);
                return (
                  <div
                    key={d.date}
                    role="listitem"
                    tabIndex={0}
                    className={`activity-col${active === index ? " is-active" : ""}`}
                    aria-label={`${shortDate(d.date)}: ${formatCount(d.records)} records, ${d.runs} runs, ${d.detail_pages} detail pages${d.failed ? `, ${d.failed} failed` : ""}`}
                    onPointerEnter={() => setActive(index)}
                    onFocus={() => setActive(index)}
                    onBlur={() => setActive(null)}
                  >
                    {labelled && (
                      <span className="activity-value" style={{ bottom: `calc(${height}% + 4px)` }} aria-hidden="true">
                        {compactNumber(d.records)}
                      </span>
                    )}
                    <span className="activity-bar" style={{ height: `${height}%` }} />
                  </div>
                );
              })}
            </div>
            {shown && active !== null && (
              <div
                className={`activity-tooltip${active > activity.length * 0.66 ? " to-left" : active < activity.length * 0.34 ? " to-right" : ""}`}
                role="status"
                style={{ left: `${((active + 0.5) / activity.length) * 100}%` }}
              >
                <span>
                  <strong>{formatCount(shown.records)}</strong> <span className="muted">records</span>
                </span>
                <span className="small">{shortDate(shown.date)}</span>
                <span className="small muted">
                  {shown.runs} run{shown.runs === 1 ? "" : "s"}
                  {shown.failed ? ` · ${shown.failed} failed` : ""} · {formatCount(shown.detail_pages)} detail pages
                </span>
              </div>
            )}
          </div>
          <div className="activity-dates" aria-hidden="true">
            {activity.map((d, index) => (
              <span key={d.date}>{index % 2 === last % 2 ? shortDate(d.date) : ""}</span>
            ))}
          </div>
        </div>
      )}
    </figure>
  );
}
