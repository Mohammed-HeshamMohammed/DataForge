import { useState } from "react";
import { call, isTauri, pickFile } from "../../lib/ipc.ts";
import { useJob, useService } from "../../lib/hooks.ts";
import { formatTime, isActive } from "../../lib/format.ts";
import { ErrorNote, JobProgress, PathInput } from "../../components/ui.tsx";
import { PURPOSES } from "./sources.ts";
import { ChangeList } from "./Scraping.tsx";

export type HostSignals = {
  host: string;
  robots_status: string;
  crawl_delay: number | null;
  sitemaps?: string[];
  tdm_reservation: number | null;
  tdm_policy: string | null;
  content_usage: Record<string, { path: string; prefs: Record<string, string> }[]>;
  ai_txt: boolean;
  warnings: string[];
};

const ROBOTS_LABELS: Record<string, string> = {
  ok: "Found and applied",
  missing: "None published (no restrictions)",
  unreachable: "Server error: treated as disallow-all",
  forbidden: "Access denied: treated as disallow-all",
  local: "Local fixture (not checked)",
  not_checked: "Not checked",
};

export function SignalsTable({ signals }: { signals: HostSignals[] }) {
  if (!signals.length) return null;
  return (
    <div className="table-wrap">
      <table className="data-table compact">
        <thead>
          <tr>
            <th scope="col">Host</th>
            <th scope="col">robots.txt</th>
            <th scope="col">Crawl delay</th>
            <th scope="col">TDM reservation</th>
            <th scope="col">Content-Usage</th>
            <th scope="col">ai.txt</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
            <tr key={s.host}>
              <td>{s.host}</td>
              <td>{ROBOTS_LABELS[s.robots_status] ?? s.robots_status}</td>
              <td>{s.crawl_delay ? `${s.crawl_delay} s` : "—"}</td>
              <td>{s.tdm_reservation === 1 ? `Reserved${s.tdm_policy ? ` (${s.tdm_policy})` : ""}` : s.tdm_reservation === 0 ? "Not reserved" : "None published"}</td>
              <td>
                {Object.values(s.content_usage ?? {})
                  .flat()
                  .map((rule) => `${rule.path} ${Object.entries(rule.prefs).map(([k, v]) => `${k}=${v}`).join(", ")}`)
                  .join("; ") || "None published"}
              </td>
              <td>{s.ai_txt ? "Published (advisory)" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Read-only check of a site's robots.txt, TDMRep, AIPREF, and ai.txt signals for a declared purpose. */
export function SignalsPanel({ url, purpose }: { url: string; purpose: string }) {
  const [result, setResult] = useState<{ allowed: boolean; reason: string | null; hosts: HostSignals[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const check = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await call("scrape.check_signals", { url, purpose }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel" aria-labelledby="signals-heading">
      <div className="section-head">
        <h2 id="signals-heading">Site signals</h2>
        <button type="button" className="btn btn-small" disabled={!url.startsWith("https://") || busy} onClick={() => void check()}>
          {busy ? "Checking…" : "Check signals"}
        </button>
      </div>
      <p className="muted small">
        Before collecting, DataForge reads robots.txt, TDMRep text-and-data-mining reservations, IETF AIPREF Content-Usage preferences, and ai.txt, and applies them to the purpose you declare. A signal that
        disallows your purpose stops the job.
      </p>
      {result && (
        <>
          <p className={result.allowed ? "note" : "note note-error"}>
            {result.allowed ? `Allowed for ${PURPOSES.find((p) => p.id === purpose)?.label.toLowerCase() ?? purpose}.` : result.reason}
          </p>
          <SignalsTable signals={result.hosts} />
        </>
      )}
      <ErrorNote message={error} />
    </section>
  );
}

type Watch = {
  id: string;
  name: string;
  status: "active" | "paused" | "stopped";
  interval_minutes: number;
  next_run_at: string | null;
  last_job_id: string | null;
  params: Record<string, unknown>;
  runs: { id: number; job_id: string; dataset_id: string | null; created_at: string; diff: { counts: Record<string, number>; changed: { key: Record<string, unknown>; changes: Record<string, { before: unknown; after: unknown }> }[] } | null }[];
};

export function WatchesPanel() {
  const watches = useService<Watch[]>("watch.list", {}, 15000);
  const [error, setError] = useState<string | null>(null);
  const act = async (command: string, payload: Record<string, unknown>) => {
    try {
      await call(command, payload);
      setError(null);
      await watches.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <section className="panel" aria-labelledby="watches-heading">
      <h2 id="watches-heading">Watches</h2>
      <p className="muted small">Watches re-run a collection on a schedule while DataForge is open. Each run stages a new dataset version and reports added, removed, and changed records.</p>
      {!watches.data?.length && <p className="list-empty">No watches yet. Start one from a successful run.</p>}
      <ul className="plain-list">
        {watches.data?.map((watch) => {
          const last = watch.runs[0];
          return (
            <li key={watch.id} className="list-row">
              <div>
                <div className="list-item-title">
                  {watch.name} <span className={`badge badge-${watch.status === "active" ? "running" : "paused"}`}>{watch.status}</span>
                </div>
                <div className="list-item-sub">
                  Every {watch.interval_minutes >= 1440 ? `${Math.round(watch.interval_minutes / 1440)} day(s)` : `${watch.interval_minutes} minutes`}
                  {watch.next_run_at && watch.status === "active" ? ` · next ${formatTime(watch.next_run_at)}` : ""}
                  {last ? ` · last run ${formatTime(last.created_at)}` : " · not run yet"}
                  {last?.diff ? ` · ${last.diff.counts.added} added, ${last.diff.counts.removed} removed, ${last.diff.counts.changed} changed` : ""}
                </div>
                {last?.diff && last.diff.changed.length > 0 && (
                  <details>
                    <summary className="small">What changed</summary>
                    <ChangeList changes={last.diff.changed} />
                  </details>
                )}
              </div>
              <div className="row-actions">
                <button type="button" className="btn btn-small" onClick={() => void act("watch.run_now", { watch_id: watch.id })}>
                  Run now
                </button>
                {watch.status === "active" ? (
                  <button type="button" className="btn btn-small" onClick={() => void act("watch.set_status", { watch_id: watch.id, status: "paused" })}>
                    Pause
                  </button>
                ) : (
                  <button type="button" className="btn btn-small" onClick={() => void act("watch.set_status", { watch_id: watch.id, status: "active" })}>
                    Resume
                  </button>
                )}
                <button type="button" className="btn btn-small btn-danger" onClick={() => void act("watch.set_status", { watch_id: watch.id, status: "stopped" })}>
                  Stop
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      <ErrorNote message={error} />
    </section>
  );
}

export function CreateWatch({ params, defaultName }: { params: Record<string, unknown>; defaultName: string }) {
  const [name, setName] = useState(defaultName);
  const [interval, setInterval] = useState("1440");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    try {
      await call("watch.create", { name, interval_minutes: Number(interval), params });
      setMessage(`Watching "${name}". It appears under Watches.`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <details>
      <summary className="small">Watch this collection for changes</summary>
      <div className="split tight-2">
        <label className="field">
          <span>Watch name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>Repeat</span>
          <select value={interval} onChange={(e) => setInterval(e.target.value)}>
            <option value="60">Every hour</option>
            <option value="360">Every 6 hours</option>
            <option value="1440">Every day</option>
            <option value="10080">Every week</option>
          </select>
        </label>
      </div>
      <button type="button" className="btn btn-small" disabled={!name} onClick={() => void create()}>
        Create watch
      </button>
      {message && <p className="note small">{message}</p>}
      <ErrorNote message={error} />
    </details>
  );
}

type PresetOption = { id: string; version: string; display_name: string };

export function ArchiveForm({ presets, purpose, onJob }: { presets: PresetOption[]; purpose: string; onJob: (jobId: string) => void }) {
  const [archive, setArchive] = useState<"wayback" | "common_crawl">("wayback");
  const [pattern, setPattern] = useState("");
  const [presetKey, setPresetKey] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [maxCaptures, setMaxCaptures] = useState("50");
  const [compare, setCompare] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const preset = presets.find((p) => `${p.id}@${p.version}` === presetKey) ?? presets[0];
  const start = async (runMode: "test" | "full") => {
    if (!preset) return;
    try {
      const { job_id } = await call<{ job_id: string }>("archive.create_job", {
        archive, url_pattern: pattern, preset_id: preset.id, preset_version: preset.version, purpose, run_mode: runMode,
        policy_acknowledgement: acknowledged, max_captures: Number(maxCaptures), from_date: fromDate || undefined, to_date: toDate || undefined, compare: archive === "wayback" && compare,
      });
      setError(null);
      onJob(job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <>
      <label className="field">
        <span>Archive</span>
        <select value={archive} onChange={(e) => setArchive(e.target.value as typeof archive)}>
          <option value="wayback">Wayback Machine (Internet Archive)</option>
          <option value="common_crawl">Common Crawl</option>
        </select>
      </label>
      {archive === "common_crawl" && (
        <p className="note note-warning small">Common Crawl's data host disallows automated fetching in robots.txt, so DataForge can list captures but stops before reading them.</p>
      )}
      <label className="field">
        <span>URL or pattern</span>
        <input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="example.com/products/*" spellCheck={false} />
      </label>
      <div className="split tight-2">
        <label className="field">
          <span>From (optional)</span>
          <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
        </label>
        <label className="field">
          <span>To (optional)</span>
          <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>Extract with</span>
        <select value={preset ? `${preset.id}@${preset.version}` : ""} onChange={(e) => setPresetKey(e.target.value)}>
          {presets.map((p) => (
            <option key={`${p.id}@${p.version}`} value={`${p.id}@${p.version}`}>
              {p.display_name} — {p.id}@{p.version}
            </option>
          ))}
        </select>
      </label>
      <label className="field inline">
        <span>Max captures</span>
        <input type="number" min={1} max={500} value={maxCaptures} onChange={(e) => setMaxCaptures(e.target.value)} />
      </label>
      {archive === "wayback" && (
        <label className="toggle block">
          <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare each page's earliest and latest capture in this range (needs a preset with unique keys)
        </label>
      )}
      <label className="toggle block">
        <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> I am authorized to use this archived data and accept the archive's terms.
      </label>
      <div className="row-actions">
        <button type="button" className="btn btn-primary" disabled={!pattern || !acknowledged || !preset} onClick={() => void start("test")}>
          Test 10 captures
        </button>
        <button type="button" className="btn" disabled={!pattern || !acknowledged || !preset} onClick={() => void start("full")}>
          Start full run
        </button>
      </div>
      <ErrorNote message={error} />
    </>
  );
}

export function BulkForm({ onJob }: { onJob: (jobId: string) => void }) {
  const [path, setPath] = useState("");
  const [types, setTypes] = useState("LocalBusiness");
  const [suffix, setSuffix] = useState("");
  const [maxRecords, setMaxRecords] = useState("100000");
  const [error, setError] = useState<string | null>(null);
  const start = async (runMode: "test" | "full") => {
    try {
      const { job_id } = await call<{ job_id: string }>("bulk.create_job", {
        path, schema_types: types.split(",").map((t) => t.trim()).filter(Boolean), domain_suffix: suffix || undefined, max_records: Number(maxRecords), run_mode: runMode,
      });
      setError(null);
      onJob(job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <>
      <p className="muted small">
        Download a class-specific subset from webdatacommons.org (for example LocalBusiness), then read it here. The file is streamed, so large subsets stay within memory limits.
      </p>
      <PathInput label="N-Quads file (.nq or .nq.gz)" value={path} onChange={setPath} placeholder="C:\data\LocalBusiness.nq.gz"
        onBrowse={isTauri() ? () => pickFile([{ name: "N-Quads", extensions: ["nq", "gz", "txt"] }]) : undefined} />
      <div className="split tight-2">
        <label className="field">
          <span>schema.org types</span>
          <input value={types} onChange={(e) => setTypes(e.target.value)} placeholder="LocalBusiness, Product" />
        </label>
        <label className="field">
          <span>Only domains ending in (optional)</span>
          <input value={suffix} onChange={(e) => setSuffix(e.target.value)} placeholder=".de" />
        </label>
      </div>
      <label className="field inline">
        <span>Max records</span>
        <input type="number" min={1} value={maxRecords} onChange={(e) => setMaxRecords(e.target.value)} />
      </label>
      <div className="row-actions">
        <button type="button" className="btn btn-primary" disabled={!path || !types} onClick={() => void start("test")}>
          Test 10 records
        </button>
        <button type="button" className="btn" disabled={!path || !types} onClick={() => void start("full")}>
          Import all
        </button>
      </div>
      <ErrorNote message={error} />
    </>
  );
}

export function useJobRunning(jobId: string | null) {
  const job = useJob(jobId);
  return { job, running: !!job && isActive(job.state) };
}

export { JobProgress };
