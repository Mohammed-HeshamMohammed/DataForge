import { useEffect, useMemo, useState } from "react";
import type { Navigate } from "../../app/App.tsx";
import { call, isTauri } from "../../lib/ipc.ts";
import { credentials, loadSetting, saveSetting } from "../../lib/desktop.ts";
import { useJob, useService } from "../../lib/hooks.ts";
import { formatCount, isActive } from "../../lib/format.ts";
import { ErrorNote, JobProgress } from "../../components/ui.tsx";
import { ArchiveForm, BulkForm, CreateWatch, SignalsPanel, SignalsTable, WatchesPanel, type HostSignals } from "./panels.tsx";
import {
  PURPOSES, SOURCES, defaultVariables, needsStartUrl, presetsFor, type RequestVariable, type SourceKind, type SourcePreset, variablePayload, variableProblems,
} from "./sources.ts";

type Preset = SourcePreset & {
  display_name: string;
  description?: string;
  status: string;
  source: "bundled" | "custom" | "package";
  errors: string[];
  url_scope: { allowed_hosts: string[]; user_supplied_host?: boolean };
  policy: Record<string, unknown>;
  strategy: { preferred: string; allowed: string[]; api_integration?: { auth?: string; header_name?: string; parameter?: string; optional?: boolean } | null };
  successor?: string;
  health_status?: { status: string; failures: string[] } | null;
  request_limits: Record<string, number>;
  pagination: Record<string, unknown>;
  parent_preset_id?: string;
  engine?: string;
  [key: string]: unknown;
};

const START_URL_PLACEHOLDER: Record<SourceKind, string> = {
  website: "https://example.com/listings",
  sitemap: "https://example.com/ (or a sitemap URL)",
  feed: "https://example.com/feed.xml",
  crawl: "https://example.com/products/",
  api: "https://api.example.com/items",
  documents: "https://example.com/reports (or a PDF URL)",
  archive: "",
  bulk: "",
};

export function Scraping({ navigate }: { navigate: Navigate }) {
  const presets = useService<Preset[]>("preset.list");
  const settings = useService<{ default_purpose: string; contact_identity: { organization: string; email: string } }>("settings.get");
  const [source, setSource] = useState<SourceKind>("website");
  const [selectedKey, setSelectedKey] = useState("");
  const available = useMemo(() => presetsFor(presets.data ?? [], source), [presets.data, source]);
  const preset = available.find((p) => `${p.id}@${p.version}` === selectedKey) ?? available[0];
  const [url, setUrl] = useState("");
  const [purpose, setPurpose] = useState("");
  const [engine, setEngine] = useState("auto");
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [acknowledged, setAcknowledged] = useState(false);
  const [maxPages, setMaxPages] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [savedCredentials, setSavedCredentials] = useState<string[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob(jobId);

  useEffect(() => {
    if (isTauri()) void credentials.list().then((list) => setSavedCredentials(list.filter((c) => c.stored).map((c) => c.name))).catch(() => {});
  }, []);
  useEffect(() => {
    if (!purpose && settings.data) setPurpose(settings.data.default_purpose);
  }, [settings.data, purpose]);
  useEffect(() => {
    setAcknowledged(false);
    setVariables(defaultVariables(preset));
  }, [preset?.id, preset?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const integration = preset?.strategy.api_integration;
  const needsCredential = !!integration?.auth && !integration.optional;
  const startUrlRequired = needsStartUrl(preset);
  const problems = variableProblems(preset, variables);
  const needsContact = !!preset?.requires_contact_user_agent && !(settings.data?.contact_identity.organization && settings.data.contact_identity.email);
  const running = !!job && isActive(job.state);
  const canStart = !!preset && acknowledged && !!purpose && !running && (!startUrlRequired || !!url) && problems.length === 0 && (!needsCredential || !!credentialRef) && !needsContact;

  const jobParams = (runMode: "test" | "full") => ({
    preset_id: preset?.id,
    preset_version: preset?.version,
    start_url: startUrlRequired ? url : "",
    run_mode: runMode,
    purpose,
    engine: preset?.strategy.preferred === "http" ? engine : "httpx",
    variables: variablePayload(preset, variables),
    policy_acknowledgement: acknowledged,
    max_pages: maxPages ? Number(maxPages) : undefined,
    credential_ref: integration?.auth ? credentialRef || undefined : undefined,
  });

  const start = async (runMode: "test" | "full") => {
    if (!preset) return;
    try {
      const result = await call<{ job_id: string }>("scrape.create_job", jobParams(runMode));
      setJobId(result.job_id);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const [view, setView] = useState<ScrapingView>(() => loadSetting<ScrapingView>("scrapingView", "collect"));
  useEffect(() => saveSetting("scrapingView", view), [view]);
  const httpPresets = presetsFor(presets.data ?? [], "archive");
  const selectorSource = source !== "archive" && source !== "bulk";
  const shownView: ScrapingView = (view === "signals" && (!selectorSource || source === "api")) || (view === "customize" && !selectorSource) ? "collect" : view;
  const signalsUrl = startUrlRequired ? url : "";

  return (
    <div className="scraping">
      <div className="segmented source-strip" role="tablist" aria-label="Source type">
        {SOURCES.map((s) => (
          <button key={s.id} type="button" role="tab" aria-selected={source === s.id} title={s.hint} onClick={() => { setSource(s.id); setSelectedKey(""); }}>
            {s.label}
          </button>
        ))}
      </div>
      <nav className="subnav" role="tablist" aria-label="Scraping sections">
        {SCRAPING_VIEWS.filter((v) => (v.id === "signals" ? selectorSource && source !== "api" : v.id === "customize" ? selectorSource : true)).map((v) => (
          <button key={v.id} type="button" role="tab" aria-selected={shownView === v.id} onClick={() => setView(v.id)}>
            {v.label}
          </button>
        ))}
      </nav>

      {shownView === "collect" && (
        <div className="work-grid">
          <section className="panel" aria-labelledby="collect-heading">
            <h2 id="collect-heading">{SOURCES.find((s) => s.id === source)?.label} collection</h2>
            <p className="muted small">{SOURCES.find((s) => s.id === source)?.hint}</p>
            <ErrorNote message={presets.error} />
            <div className="form-grid">
              <label className="field">
                <span>Purpose</span>
                <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                  {PURPOSES.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          {source === "archive" && <ArchiveForm presets={httpPresets} purpose={purpose} onJob={setJobId} />}
          {source === "bulk" && <BulkForm onJob={setJobId} />}

          {source !== "archive" && source !== "bulk" && (
            <>
              <label className="field">
                <span>Preset</span>
                <select value={preset ? `${preset.id}@${preset.version}` : ""} onChange={(e) => setSelectedKey(e.target.value)}>
                  {available.map((p) => (
                    <option key={`${p.id}@${p.version}`} value={`${p.id}@${p.version}`} disabled={p.status === "disabled" || p.errors.length > 0}>
                      {p.display_name} — {p.id}@{p.version}
                      {p.source === "custom" ? " (custom)" : ""}
                      {p.status !== "active" ? ` [${p.status}]` : ""}
                    </option>
                  ))}
                </select>
              </label>
              {!available.length && <p className="list-empty">No presets for this source yet.</p>}
              {preset && (
                <>
                  <details className="preset-details">
                    <summary className="small muted">{preset.description ?? "Preset details"}</summary>
                  <dl className="facts">
                    <dt>Extraction</dt>
                    <dd>{extractionLabel(preset)}</dd>
                    <dt>Scope</dt>
                    <dd>{preset.url_scope.user_supplied_host ? "Only the host you enter" : preset.url_scope.allowed_hosts.join(", ")}</dd>
                    <dt>Limits</dt>
                    <dd>
                      {preset.request_limits.max_pages_default} pages · {formatCount(preset.request_limits.max_records_default)} records · {preset.request_limits.min_delay_ms} ms between requests
                      {preset.request_limits.max_requests_per_second ? ` · at most ${preset.request_limits.max_requests_per_second}/s` : ""}
                    </dd>
                    <dt>Policy</dt>
                    <dd>Robots {String(preset.policy.robots_policy)}; honours TDMRep and AIPREF signals; stops on login, CAPTCHA, access denial, paywall, or rate limit</dd>
                  </dl>
                  </details>
                  {preset.errors.length > 0 && <ErrorNote message={`Preset is invalid: ${preset.errors.join("; ")}`} />}
                  {preset.status === "deprecated" && <p className="note note-warning">This preset version is deprecated{preset.successor ? `; use ${preset.successor}` : ""}.</p>}
                  {preset.status === "degraded" && <p className="note note-warning">Fixture health check failing: {preset.health_status?.failures[0] ?? "see Settings"}. Results may be incomplete.</p>}
                  {needsContact && <p className="note note-warning">This source requires a contact identity in the User-Agent. Add your organization and email in Settings → Collection.</p>}
                  {integration?.auth && (
                    <label className="field">
                      <span>
                        API credential ({integration.auth === "bearer" ? "bearer token" : integration.auth === "query_param" ? `${integration.parameter} parameter` : integration.header_name})
                        {integration.optional ? " — optional" : ""}
                      </span>
                      <select value={credentialRef} onChange={(e) => setCredentialRef(e.target.value)}>
                        <option value="">{integration.optional ? "None" : "Choose a saved credential…"}</option>
                        {savedCredentials.map((name) => (
                          <option key={name}>{name}</option>
                        ))}
                      </select>
                    </label>
                  )}
                  <VariableFields preset={preset} values={variables} onChange={setVariables} />
                </>
              )}
              {startUrlRequired && (
                <label className="field">
                  <span>{source === "feed" ? "Feed URL" : source === "sitemap" ? "Site or sitemap URL" : "Start URL"}</span>
                  <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={START_URL_PLACEHOLDER[source]} spellCheck={false} />
                </label>
              )}
              <div className="form-grid">
                <label className="field">
                  <span>Max pages</span>
                  <input type="number" min={1} value={maxPages} onChange={(e) => setMaxPages(e.target.value)} placeholder={String(preset?.request_limits.max_pages_default ?? "")} />
                </label>
                {preset?.strategy.preferred === "http" && (
                  <label className="field">
                    <span>Engine</span>
                    <select value={engine} onChange={(e) => setEngine(e.target.value)} title="Tests always use the built-in engine">
                      <option value="auto">Automatic (Scrapy for sitemaps and crawls over 200 pages)</option>
                      <option value="httpx">Built-in (httpx)</option>
                      <option value="scrapy">Scrapy</option>
                    </select>
                  </label>
                )}
              </div>
              {problems.length > 0 && <p className="note note-warning small">{problems.join("; ")}</p>}
              <label className="toggle block">
                <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> I am authorized to collect and use this data and accept the source's terms.
              </label>
              <div className="row-actions">
                <button type="button" className="btn btn-primary" disabled={!canStart} onClick={() => void start("test")}>
                  Test 10 records
                </button>
                <button type="button" className="btn" disabled={!canStart} onClick={() => void start("full")} title="Custom presets need a successful test first">
                  Start full run
                </button>
              </div>
              {job?.state === "completed" && job.result?.run_mode === "test" && job.kind === "scrape" && <CreateWatch params={jobParams("full")} defaultName={preset?.display_name ?? "Watch"} />}
            </>
          )}
            <ErrorNote message={error} />
          </section>

          <section className="panel" aria-labelledby="run-heading">
            <h2 id="run-heading">Run</h2>
            {!jobId && <p className="muted">Test on a permitted source to preview extracted values before a full run. Results appear here.</p>}
            {jobId && <JobProgress job={job} />}
            {job?.result && <ScrapeResult result={job.result} jobId={job.id} onOpenDataset={(id) => navigate("datasets", { datasetId: id })} />}
          </section>
        </div>
      )}
      {shownView === "signals" && selectorSource && source !== "api" && <SignalsPanel url={signalsUrl} purpose={purpose} />}
      {shownView === "watches" && <WatchesPanel />}
      {shownView === "customize" && selectorSource && preset && <PresetEditor preset={preset} url={url} purpose={purpose} onSaved={(key) => { void presets.reload(); setSelectedKey(key); }} />}
      {shownView === "customize" && selectorSource && !preset && <p className="list-empty">Choose a preset under Collect first.</p>}
    </div>
  );
}

type ScrapingView = "collect" | "signals" | "watches" | "customize";
const SCRAPING_VIEWS: { id: ScrapingView; label: string }[] = [
  { id: "collect", label: "Collect" },
  { id: "signals", label: "Site signals" },
  { id: "watches", label: "Watches" },
  { id: "customize", label: "Customize preset" },
];

function extractionLabel(preset: Preset): string {
  const mode = preset.strategy.preferred === "api" ? "api" : (preset.extraction.mode ?? "selectors");
  const discovery = preset.discovery?.mode && preset.discovery.mode !== "none" ? ` from ${preset.discovery.mode.replace("_", ".")} discovery` : "";
  const labels: Record<string, string> = {
    api: "JSON API fields",
    selectors: "CSS or XPath selectors",
    structured_data: "schema.org structured data (JSON-LD, Microdata, RDFa)",
    article: "Main article text and metadata",
    document_tables: "PDF tables with page and row provenance",
  };
  return (labels[mode] ?? mode) + discovery;
}

function VariableFields({ preset, values, onChange }: { preset: Preset; values: Record<string, string>; onChange: (values: Record<string, string>) => void }) {
  const entries = Object.entries(preset.request?.variables ?? {}) as [string, RequestVariable][];
  if (!entries.length) return null;
  const set = (name: string, value: string) => onChange({ ...values, [name]: value });
  return (
    <fieldset className="field-card">
      <legend className="small">Request</legend>
      {entries.map(([name, spec]) => (
        <label key={name} className="field">
          <span>
            {name.replace(/_/g, " ")}
            {spec.required ? "" : " (optional)"}
            {name === preset.request?.limit_variable ? " — capped by the record limit" : ""}
          </span>
          {spec.type === "enum" ? (
            <select value={values[name] ?? ""} onChange={(e) => set(name, e.target.value)}>
              {spec.choices?.map((choice) => (
                <option key={choice}>{choice}</option>
              ))}
            </select>
          ) : spec.type === "sparql" ? (
            <textarea className="code" rows={6} value={values[name] ?? ""} onChange={(e) => set(name, e.target.value)} spellCheck={false} placeholder="SELECT ?item ?itemLabel WHERE { … }" />
          ) : (
            <input
              type={spec.type === "integer" || spec.type === "number" ? "number" : "text"}
              min={spec.minimum}
              max={spec.maximum}
              step={spec.type === "number" ? "any" : undefined}
              value={values[name] ?? ""}
              onChange={(e) => set(name, e.target.value)}
              spellCheck={false}
            />
          )}
        </label>
      ))}
    </fieldset>
  );
}

type Change = { key: Record<string, unknown>; changes: Record<string, { before: unknown; after: unknown }> };

export function ChangeList({ changes }: { changes: Change[] }) {
  return (
    <div className="table-wrap">
      <table className="data-table compact">
        <caption className="sr-only">Changed records</caption>
        <thead>
          <tr>
            <th scope="col">Record</th>
            <th scope="col">Field</th>
            <th scope="col">Before</th>
            <th scope="col">After</th>
          </tr>
        </thead>
        <tbody>
          {changes.slice(0, 50).flatMap((change) =>
            Object.entries(change.changes).map(([field, value]) => (
              <tr key={`${JSON.stringify(change.key)}-${field}`}>
                <td>{Object.values(change.key).join(" · ")}</td>
                <td>{field}</td>
                <td>{String(value.before ?? "—").slice(0, 120)}</td>
                <td>{String(value.after ?? "—").slice(0, 120)}</td>
              </tr>
            )),
          )}
        </tbody>
      </table>
    </div>
  );
}

function FixtureFromCapture({ jobId }: { jobId: string }) {
  const [saved, setSaved] = useState<{ fixture: string; source_url: string; redactions: Record<string, number> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      setSaved(await call("preset.fixture_from_capture", { job_id: jobId }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <div>
      <button type="button" className="btn btn-small" onClick={() => void save()}>
        Save captured page as a test fixture
      </button>
      {saved && (
        <p className="note small">
          Saved <code>{saved.fixture}</code> from {saved.source_url}. Removed:{" "}
          {Object.entries(saved.redactions)
            .filter(([, count]) => count > 0)
            .map(([kind, count]) => `${count} ${kind.replace(/_/g, " ")}`)
            .join(", ") || "nothing sensitive found"}
          . Review it, then add it to the preset's health.fixture_tests in a new version.
        </p>
      )}
      <ErrorNote message={error} />
    </div>
  );
}

export function ScrapeResult({ result, onOpenDataset, jobId }: { result: Record<string, any>; onOpenDataset: (id: string) => void; jobId?: string }) {
  const samples: Record<string, unknown>[] = result.sample_records ?? [];
  const columns = samples[0]
    ? [...new Set(samples.flatMap((r) => Object.keys(r)))].filter((k) => !["source_retrieved_at", "preset_id", "preset_version", "strategy_used", "text", "markdown"].includes(k)).slice(0, 16)
    : [];
  const diff = result.watch_diff as { counts: Record<string, number> } | null | undefined;
  return (
    <div className="stack">
      <dl className="facts">
        <dt>Strategy</dt>
        <dd>
          {result.strategy_used ?? result.archive ?? "bulk"}
          {result.engine ? ` on the ${result.engine === "scrapy" ? "Scrapy" : "built-in"} engine` : ""}
          {result.strategy_rationale ? ` — ${result.strategy_rationale}` : ""}
        </dd>
        <dt>Pages</dt>
        <dd>
          {result.pages_fetched ?? result.captures_read ?? result.pages_read}
          {result.discovered_urls ? ` · ${result.discovered_urls} discovered` : ""}
          {result.cached_responses ? ` · ${result.cached_responses} unchanged (served from cache)` : ""}
        </dd>
        <dt>Records</dt>
        <dd>
          {result.records_extracted} kept{result.records_rejected !== undefined ? ` · ${result.records_rejected} rejected` : ""}
          {result.records_duplicate !== undefined ? ` · ${result.records_duplicate} duplicates` : ""}
        </dd>
        {result.stop_reason && (
          <>
            <dt>Stopped</dt>
            <dd>{String(result.stop_reason).replace(/_/g, " ")}</dd>
          </>
        )}
        {result.field_coverage && (
          <>
            <dt>Coverage</dt>
            <dd>
              {Object.entries(result.field_coverage)
                .map(([k, v]) => `${k} ${Math.round(Number(v) * 100)}%`)
                .join(" · ")}
            </dd>
          </>
        )}
        {diff && (
          <>
            <dt>Changes</dt>
            <dd>
              {diff.counts.added} added · {diff.counts.removed} removed · {diff.counts.changed} changed · {diff.counts.unchanged} unchanged
            </dd>
          </>
        )}
        {result.warc_capture && (
          <>
            <dt>Capture</dt>
            <dd>
              <code>{result.warc_capture}</code>
            </dd>
          </>
        )}
        {result.archive_diff && (
          <>
            <dt>Since first capture</dt>
            <dd>
              {result.archive_diff.counts.added} added · {result.archive_diff.counts.removed} removed · {result.archive_diff.counts.changed} changed
            </dd>
          </>
        )}
      </dl>
      {(result.warnings ?? []).map((w: string) => (
        <p key={w} className="note note-warning">
          <span aria-hidden="true">! </span>
          {w}
        </p>
      ))}
      <SignalsTable signals={(result.signals ?? []) as HostSignals[]} />
      {(result.archive_diff?.changed ?? result.watch_diff?.changed ?? []).length > 0 && <ChangeList changes={(result.archive_diff ?? result.watch_diff).changed} />}
      {result.warc_capture && jobId && <FixtureFromCapture jobId={jobId} />}
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
                    <td key={c}>{String(r[c] ?? "").slice(0, 200)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(result.file_datasets ?? []).map((f: { url: string; dataset_id?: string; error?: string }) =>
        f.dataset_id ? (
          <button key={f.url} type="button" className="btn btn-small" onClick={() => onOpenDataset(f.dataset_id!)}>
            Open imported file: {f.url.split("/").pop()}
          </button>
        ) : (
          <p key={f.url} className="note note-warning small">
            {f.url.split("/").pop()}: {f.error}
          </p>
        ),
      )}
      {result.dataset_id && (
        <button type="button" className="btn btn-primary" onClick={() => onOpenDataset(result.dataset_id)}>
          Open staged dataset
        </button>
      )}
    </div>
  );
}

type Proposal = { source: string; preset: Preset; evaluation: { records: number; field_coverage: Record<string, number> } };

function PresetEditor({ preset, url, purpose, onSaved }: { preset: Preset; url: string; purpose: string; onSaved: (key: string) => void }) {
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [extraction, setExtraction] = useState("");
  const [pagination, setPagination] = useState("");
  const [discovery, setDiscovery] = useState("");
  const [examples, setExamples] = useState("");
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setExtraction(JSON.stringify(preset.extraction, null, 2));
    setPagination(JSON.stringify(preset.pagination, null, 2));
    setDiscovery(JSON.stringify(preset.discovery ?? { mode: "none" }, null, 2));
    setName(preset.source === "custom" ? preset.id.split(".").slice(2).join("_") : `${preset.id.split(".")[1]}_copy`);
    setVersion(preset.source === "custom" ? bumpPatch(preset.version) : "1.0.0");
    setProposals(null);
    setNotice(null);
    setError(null);
  }, [preset]);

  const fetchPreset = () => ({ ...preset, url_scope: preset.url_scope.user_supplied_host && url ? { ...preset.url_scope, allowed_hosts: [new URL(url).hostname] } : preset.url_scope });

  const suggest = async () => {
    setBusy("suggest");
    try {
      const pairs = Object.fromEntries(
        examples
          .split("\n")
          .map((line) => line.split("="))
          .filter((parts) => parts.length >= 2 && parts[0].trim())
          .map(([key, ...rest]) => [key.trim(), rest.join("=").trim()]),
      );
      const suggestion = await call<{ record_root: { css: string } | null; fields: unknown[]; record_count: number; notes: string[] }>("scrape.suggest_selectors", { url, purpose, preset: fetchPreset(), examples: pairs });
      if (!suggestion.record_root) throw new Error(suggestion.notes.join("; ") || "No repeated item found around those values");
      setExtraction(JSON.stringify({ ...JSON.parse(extraction), mode: "selectors", record_root: suggestion.record_root, fields: (suggestion.fields as Record<string, unknown>[]).map(({ relative_to_root: _r, ...f }) => f) }, null, 2));
      setNotice(`Suggested selectors match ${suggestion.record_count} items${suggestion.notes.length ? ` (${suggestion.notes.join("; ")})` : ""}. Review them, save, then test.`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const propose = async () => {
    setBusy("propose");
    try {
      const result = await call<{ proposals: Proposal[] }>("scrape.propose_presets", { url, purpose, preset: fetchPreset() });
      setProposals(result.proposals);
      setError(result.proposals.length ? null : "No proposal met the coverage bar on this page.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    try {
      const parent = preset.source === "custom" && preset.parent_preset_id ? { id: preset.parent_preset_id as string, version: preset.parent_preset_version as string } : { id: preset.id, version: preset.version };
      const { source: _s, errors: _e, health_status: _h, ...base } = preset;
      const parsedDiscovery = JSON.parse(discovery);
      const draft = {
        ...base,
        id: `custom.local.${name.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_")}`,
        version,
        display_name: `${preset.display_name} (custom: ${name})`,
        parent_preset_id: parent.id,
        parent_preset_version: parent.version,
        extraction: JSON.parse(extraction),
        pagination: JSON.parse(pagination),
        ...(parsedDiscovery.mode && parsedDiscovery.mode !== "none" ? { discovery: parsedDiscovery } : preset.discovery ? { discovery: parsedDiscovery } : {}),
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

  const htmlPreset = preset.strategy.preferred !== "api";
  return (
    <details className="panel" open>
      <summary>
        <h2 className="inline-heading">Customize preset</h2>
      </summary>
      <p className="muted">
        Edit extraction (selectors with <code>::text</code> or <code>::attr(name)</code>, XPath, structured data, or article mode), discovery, and pagination. Saving creates a versioned custom preset derived from{" "}
        <code>
          {preset.id}@{preset.version}
        </code>
        ; its scope, policy, and strategies cannot be broadened. Run a successful test before a full run.
      </p>
      {htmlPreset && (
        <div className="field-card">
          <h3 className="section-label">Suggestions</h3>
          <p className="muted small">
            Type values you can see on the start URL's page, one per line as <code>field=value</code>, and DataForge proposes selectors. Or let it propose a whole draft from the page's structure. Suggestions are
            checked against the page and never saved without you.
          </p>
          <label className="field">
            <span>Example values</span>
            <textarea className="code" rows={3} value={examples} onChange={(e) => setExamples(e.target.value)} placeholder={"title=A Light in the Attic\nprice=£51.77"} spellCheck={false} />
          </label>
          <div className="row-actions">
            <button type="button" className="btn btn-small" disabled={!url || !examples.includes("=") || busy !== null} onClick={() => void suggest()}>
              {busy === "suggest" ? "Suggesting…" : "Suggest selectors"}
            </button>
            <button type="button" className="btn btn-small" disabled={!url || busy !== null} onClick={() => void propose()}>
              {busy === "propose" ? "Analysing…" : "Propose a draft"}
            </button>
          </div>
          {proposals?.map((p, i) => (
            <div key={i} className="list-row">
              <span className="small">
                {p.source === "structured_data" ? "Structured data" : p.source === "model" ? "Model suggestion (AI-assisted)" : "Repeated items"}: {p.evaluation.records} records ·{" "}
                {Object.entries(p.evaluation.field_coverage)
                  .map(([k, v]) => `${k} ${Math.round(v * 100)}%`)
                  .join(" · ")}
              </span>
              <button type="button" className="btn btn-small" onClick={() => { setExtraction(JSON.stringify(p.preset.extraction, null, 2)); setNotice("Draft applied to the editor. Review, save, then test."); }}>
                Use
              </button>
            </div>
          ))}
          {notice && <p className="note small">{notice}</p>}
        </div>
      )}
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
      {htmlPreset && (
        <label className="field">
          <span>Discovery</span>
          <textarea className="code" rows={5} value={discovery} onChange={(e) => setDiscovery(e.target.value)} spellCheck={false} />
        </label>
      )}
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
