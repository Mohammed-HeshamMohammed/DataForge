import { useEffect, useState } from "react";
import type { Navigate } from "../../app/App.tsx";
import { call, isTauri } from "../../lib/ipc.ts";
import { credentials } from "../../lib/desktop.ts";
import { useJob, useService } from "../../lib/hooks.ts";
import { formatCount, isActive } from "../../lib/format.ts";
import { ErrorNote, JobProgress } from "../../components/ui.tsx";

type Preset = {
  id: string;
  version: string;
  display_name: string;
  description?: string;
  status: string;
  source: "bundled" | "custom";
  errors: string[];
  page_type: string;
  url_scope: { allowed_hosts: string[]; user_supplied_host?: boolean };
  policy: Record<string, unknown>;
  strategy: { preferred: string; allowed: string[]; api_integration?: { auth?: string; header_name?: string } | null };
  successor?: string;
  health_status?: { status: string; failures: string[] } | null;
  request_limits: Record<string, number>;
  extraction: Record<string, unknown>;
  pagination: Record<string, unknown>;
  parent_preset_id?: string;
  [key: string]: unknown;
};

export function Scraping({ navigate }: { navigate: Navigate }) {
  const presets = useService<Preset[]>("preset.list");
  const [selectedKey, setSelectedKey] = useState("");
  const preset = presets.data?.find((p) => `${p.id}@${p.version}` === selectedKey) ?? presets.data?.[0];
  const [url, setUrl] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [maxPages, setMaxPages] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [savedCredentials, setSavedCredentials] = useState<string[]>([]);
  useEffect(() => {
    if (isTauri()) void credentials.list().then((list) => setSavedCredentials(list.filter((c) => c.stored).map((c) => c.name))).catch(() => {});
  }, []);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob(jobId);

  useEffect(() => setAcknowledged(false), [selectedKey]);

  const needsCredential = !!preset?.strategy.api_integration?.auth;

  const start = async (runMode: "test" | "full") => {
    if (!preset) return;
    try {
      const result = await call<{ job_id: string }>("scrape.create_job", {
        preset_id: preset.id,
        preset_version: preset.version,
        start_url: url,
        run_mode: runMode,
        policy_acknowledgement: acknowledged,
        max_pages: maxPages ? Number(maxPages) : undefined,
        credential_ref: needsCredential ? credentialRef : undefined,
      });
      setJobId(result.job_id);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const running = !!job && isActive(job.state);

  return (
    <div className="stack">
      <div className="split">
        <section className="panel">
          <h2>Collection job</h2>
          <ErrorNote message={presets.error} />
          <label className="field">
            <span>Preset</span>
            <select value={preset ? `${preset.id}@${preset.version}` : ""} onChange={(e) => setSelectedKey(e.target.value)}>
              {presets.data?.map((p) => (
                <option key={`${p.id}@${p.version}`} value={`${p.id}@${p.version}`} disabled={p.status === "disabled" || p.errors.length > 0}>
                  {p.display_name} — {p.id}@{p.version}
                  {p.source === "custom" ? " (custom)" : ""}
                  {p.status !== "active" ? ` [${p.status}]` : ""}
                </option>
              ))}
            </select>
          </label>
          {preset && (
            <>
              {preset.description && <p className="muted">{preset.description}</p>}
              <dl className="facts">
                <dt>Strategy</dt>
                <dd>
                  {preset.strategy.preferred} (allowed: {preset.strategy.allowed.join(", ")})
                </dd>
                <dt>Scope</dt>
                <dd>{preset.url_scope.user_supplied_host ? "Only the host of the start URL" : preset.url_scope.allowed_hosts.join(", ")}</dd>
                <dt>Limits</dt>
                <dd>
                  {preset.request_limits.max_pages_default} pages · {formatCount(preset.request_limits.max_records_default)} records · {preset.request_limits.min_delay_ms} ms between
                  requests
                </dd>
                <dt>Policy</dt>
                <dd>Robots {String(preset.policy.robots_policy)}; stops on login, CAPTCHA, access denial, paywall, or rate limit</dd>
              </dl>
              {preset.errors.length > 0 && <ErrorNote message={`Preset is invalid: ${preset.errors.join("; ")}`} />}
              {preset.status === "deprecated" && <p className="note note-warning">This preset version is deprecated{preset.successor ? `; use ${preset.successor}` : ""}.</p>}
              {preset.status === "degraded" && <p className="note note-warning">Fixture health check failing: {preset.health_status?.failures[0] ?? "see Settings"}. Results may be incomplete.</p>}
              {needsCredential && (
                <label className="field">
                  <span>API credential ({preset.strategy.api_integration?.auth === "bearer" ? "bearer token" : preset.strategy.api_integration?.header_name})</span>
                  <select value={credentialRef} onChange={(e) => setCredentialRef(e.target.value)}>
                    <option value="">Choose a saved credential…</option>
                    {savedCredentials.map((name) => (
                      <option key={name}>{name}</option>
                    ))}
                  </select>
                </label>
              )}
            </>
          )}
          <label className="field">
            <span>Start URL</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/listings" spellCheck={false} />
          </label>
          <label className="field inline">
            <span>Max pages</span>
            <input type="number" min={1} value={maxPages} onChange={(e) => setMaxPages(e.target.value)} placeholder={String(preset?.request_limits.max_pages_default ?? "")} />
          </label>
          <label className="toggle block">
            <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> I am authorized to collect and use this data and accept the site's
            terms.
          </label>
          <div className="row-actions">
            <button type="button" className="btn btn-primary" disabled={!url || !acknowledged || running || (needsCredential && !credentialRef)} onClick={() => void start("test")}>
              Test 10 records
            </button>
            <button type="button" className="btn" disabled={!url || !acknowledged || running || (needsCredential && !credentialRef)} onClick={() => void start("full")} title="Custom presets need a successful test first">
              Start full run
            </button>
          </div>
          <ErrorNote message={error} />
        </section>

        <section className="panel">
          <h2>Run</h2>
          {!jobId && <p className="muted">Test a preset on a permitted page to preview extracted values before a full run.</p>}
          {jobId && <JobProgress job={job} />}
          {job?.result && <ScrapeResult result={job.result} onOpenDataset={(id) => navigate("datasets", { datasetId: id })} />}
        </section>
      </div>
      {preset && <PresetEditor preset={preset} onSaved={(key) => { void presets.reload(); setSelectedKey(key); }} />}
    </div>
  );
}

export function ScrapeResult({ result, onOpenDataset }: { result: Record<string, any>; onOpenDataset: (id: string) => void }) {
  const samples: Record<string, unknown>[] = result.sample_records ?? [];
  const columns = samples[0] ? Object.keys(samples[0]).filter((k) => !["source_retrieved_at", "preset_id", "preset_version", "strategy_used"].includes(k)) : [];
  return (
    <div className="stack">
      <dl className="facts">
        <dt>Strategy</dt>
        <dd>
          {result.strategy_used} — {result.strategy_rationale}
        </dd>
        <dt>Pages</dt>
        <dd>{result.pages_fetched}</dd>
        <dt>Records</dt>
        <dd>
          {result.records_extracted} kept · {result.records_rejected} rejected · {result.records_duplicate} duplicates
        </dd>
        <dt>Stopped</dt>
        <dd>{String(result.stop_reason).replace(/_/g, " ")}</dd>
        <dt>Coverage</dt>
        <dd>
          {Object.entries(result.field_coverage ?? {})
            .map(([k, v]) => `${k} ${Math.round(Number(v) * 100)}%`)
            .join(" · ")}
        </dd>
      </dl>
      {(result.warnings ?? []).map((w: string) => (
        <p key={w} className="note note-warning">
          <span aria-hidden="true">! </span>
          {w}
        </p>
      ))}
      {columns.length > 0 && (
        <div className="table-wrap">
          <table className="data-table compact">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c} scope="col">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {samples.map((r, i) => (
                <tr key={i}>
                  {columns.map((c) => (
                    <td key={c}>{String(r[c] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {result.dataset_id && (
        <button type="button" className="btn btn-primary" onClick={() => onOpenDataset(result.dataset_id)}>
          Open staged dataset
        </button>
      )}
    </div>
  );
}

function PresetEditor({ preset, onSaved }: { preset: Preset; onSaved: (key: string) => void }) {
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [extraction, setExtraction] = useState("");
  const [pagination, setPagination] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setExtraction(JSON.stringify(preset.extraction, null, 2));
    setPagination(JSON.stringify(preset.pagination, null, 2));
    setName(preset.source === "custom" ? preset.id.split(".").slice(2).join("_") : `${preset.id.split(".")[1]}_copy`);
    setVersion(preset.source === "custom" ? bumpPatch(preset.version) : "1.0.0");
    setError(null);
  }, [preset]);

  const save = async () => {
    try {
      const parent = preset.source === "custom" && preset.parent_preset_id ? { id: preset.parent_preset_id as string, version: preset.parent_preset_version as string } : { id: preset.id, version: preset.version };
      const { source: _s, errors: _e, health_status: _h, ...base } = preset;
      const draft = {
        ...base,
        id: `custom.local.${name.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_")}`,
        version,
        display_name: `${preset.display_name} (custom: ${name})`,
        parent_preset_id: parent.id,
        parent_preset_version: parent.version,
        extraction: JSON.parse(extraction),
        pagination: JSON.parse(pagination),
      };
      const { errors } = await call<{ errors: string[] }>("preset.validate", { preset: draft });
      if (errors.length) throw new Error(errors.join("; "));
      const saved = await call<{ id: string; version: string }>("preset.save_custom", { preset: draft });
      setError(null);
      onSaved(`${saved.id}@${saved.version}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <details className="panel">
      <summary>
        <h2 className="inline-heading">Customize selectors</h2>
      </summary>
      <p className="muted">
        Edit the record root, field selectors (CSS with optional <code>::text</code> or <code>::attr(name)</code>), transforms, and pagination. Saving creates a versioned custom
        preset derived from <code>{preset.id}@{preset.version}</code>; its scope, policy, and strategies cannot be broadened. Run a successful test before a full run.
      </p>
      <div className="split">
        <label className="field">
          <span>Custom preset name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>Version</span>
          <input value={version} onChange={(e) => setVersion(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>Extraction</span>
        <textarea className="code" rows={14} value={extraction} onChange={(e) => setExtraction(e.target.value)} spellCheck={false} />
      </label>
      <label className="field">
        <span>Pagination</span>
        <textarea className="code" rows={5} value={pagination} onChange={(e) => setPagination(e.target.value)} spellCheck={false} />
      </label>
      <button type="button" className="btn btn-primary" disabled={!name} onClick={() => void save()}>
        Validate and save custom preset
      </button>
      <ErrorNote message={error} />
    </details>
  );
}

function bumpPatch(version: string): string {
  const [major, minor, patch] = version.split(".").map(Number);
  return `${major}.${minor}.${(patch || 0) + 1}`;
}
