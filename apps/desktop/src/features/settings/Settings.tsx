import { useEffect, useState } from "react";
import type { Project } from "../../lib/types.ts";
import { call, isTauri, pickFile } from "../../lib/ipc.ts";
import { useService } from "../../lib/hooks.ts";
import { credentials, loadSetting, saveSetting, updates, type UpdateInfo } from "../../lib/desktop.ts";
import { formatTime } from "../../lib/format.ts";
import { ConfirmButton, ErrorNote, PathInput } from "../../components/ui.tsx";

type PresetRow = { id: string; version: string; display_name: string; source: string; status: string; declared_status: string; successor?: string; package?: string | null; health_status: { status: string; checked_at: string; failures: string[]; suggestions?: { field: string; suggested: string; score: number }[] } | null; extraction?: { record_root?: unknown } };
type PackageRow = { name: string; version: string; key_id: string; installed_at: string; removed_at: string | null };

export type UpdatePrefs = { repository: string; channel: "stable" | "beta"; autoCheck: boolean; lastCheck: number | null };
export const DEFAULT_UPDATE_PREFS: UpdatePrefs = { repository: "", channel: "stable", autoCheck: false, lastCheck: null };

const SETTINGS_SECTIONS = [
  { id: "project", label: "Project" },
  { id: "collection", label: "Collection" },
  { id: "presets", label: "Presets" },
  { id: "credentials", label: "Credentials" },
  { id: "updates", label: "Updates" },
] as const;
type SettingsSection = (typeof SETTINGS_SECTIONS)[number]["id"];

/** One section at a time with a vertical section list, so settings never need a long page scroll. */
export function Settings({ project, onUpdateInfo }: { project: Project; onUpdateInfo?: (info: UpdateInfo | null) => void }) {
  const [section, setSection] = useState<SettingsSection>(() => loadSetting<SettingsSection>("settingsSection", "project"));
  useEffect(() => saveSetting("settingsSection", section), [section]);
  return (
    <div className="settings-layout">
      <nav className="settings-nav" role="tablist" aria-label="Settings sections" aria-orientation="vertical">
        {SETTINGS_SECTIONS.map((s) => (
          <button key={s.id} type="button" role="tab" id={`settings-tab-${s.id}`} aria-controls="settings-panel" aria-selected={section === s.id} onClick={() => setSection(s.id)}>
            {s.label}
          </button>
        ))}
      </nav>
      <div id="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${section}`}>
        {section === "project" && <ProjectSection project={project} />}
        {section === "collection" && <CollectionSection />}
        {section === "presets" && <PresetsSection />}
        {section === "credentials" && <CredentialsSection />}
        {section === "updates" && <UpdatesSection onUpdateInfo={onUpdateInfo} />}
      </div>
    </div>
  );
}

function ProjectSection({ project }: { project: Project }) {
  const health = useService<{ status: string; checked_at: string }>("health.check");
  return (
    <section className="panel">
      <h2>Project</h2>
      <dl className="facts">
        <dt>Name</dt>
        <dd>{project.name}</dd>
        <dt>Folder</dt>
        <dd>
          <code>{project.root_path}</code>
        </dd>
        <dt>Database</dt>
        <dd>{project.schema_version ?? "—"} migrations applied · a backup is written to backups\ before each schema change</dd>
        <dt>Service</dt>
        <dd>{health.data ? `${health.data.status} (checked ${new Date(health.data.checked_at).toLocaleTimeString()})` : "…"}</dd>
      </dl>
    </section>
  );
}

type CollectionSettings = {
  contact_identity: { organization: string; email: string };
  default_purpose: string;
  http_cache: { enabled: boolean };
  warc_capture: { enabled: boolean; retention_days: number };
  ai_suggestions: { provider: "local_heuristic" | "model"; endpoint: string; model: string; remote_consent: boolean };
};

const PURPOSE_OPTIONS: [string, string][] = [
  ["internal_analysis", "Internal analysis"], ["lead_research", "Lead research"], ["dataset_building", "Building a dataset"], ["price_monitoring", "Price monitoring"],
  ["research", "Research"], ["archival", "Archiving"], ["search_indexing", "Search indexing"], ["ai_training", "AI training"],
];

function CollectionSection() {
  const saved = useService<CollectionSettings>("settings.get");
  const [draft, setDraft] = useState<CollectionSettings | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (saved.data) setDraft(saved.data);
  }, [saved.data]);
  if (!draft) return null;
  const remote = draft.ai_suggestions.provider === "model" && !!draft.ai_suggestions.endpoint && !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(draft.ai_suggestions.endpoint);
  const save = async () => {
    try {
      await call("settings.update", { changes: draft });
      setNotice("Collection settings saved.");
      setError(null);
      void saved.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const purge = async () => {
    try {
      const { bytes_removed } = await call<{ bytes_removed: number }>("cache.purge");
      setNotice(`Cache cleared (${Math.round(bytes_removed / 1024)} KB).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <section className="panel">
      <h2>Collection</h2>
      <h3 className="section-label">Contact identity</h3>
      <p className="muted small">
        Some sources (SEC EDGAR, Wikidata) require a way to contact you in each request's User-Agent. It is sent only to presets that require it and stored in this project only.
      </p>
      <div className="split tight-2">
        <label className="field">
          <span>Organization or name</span>
          <input value={draft.contact_identity.organization} onChange={(e) => setDraft({ ...draft, contact_identity: { ...draft.contact_identity, organization: e.target.value } })} />
        </label>
        <label className="field">
          <span>Contact email</span>
          <input type="email" value={draft.contact_identity.email} onChange={(e) => setDraft({ ...draft, contact_identity: { ...draft.contact_identity, email: e.target.value } })} />
        </label>
      </div>
      <label className="field">
        <span>Default purpose</span>
        <select value={draft.default_purpose} onChange={(e) => setDraft({ ...draft, default_purpose: e.target.value })}>
          {PURPOSE_OPTIONS.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <h3 className="section-label">Caching and capture</h3>
      <label className="toggle block">
        <input type="checkbox" checked={draft.http_cache.enabled} onChange={(e) => setDraft({ ...draft, http_cache: { enabled: e.target.checked } })} /> Cache responses and revalidate them on re-runs
        (unchanged pages cost a 304)
      </label>
      <label className="toggle block">
        <input type="checkbox" checked={draft.warc_capture.enabled} onChange={(e) => setDraft({ ...draft, warc_capture: { ...draft.warc_capture, enabled: e.target.checked } })} /> Keep a WARC copy of
        fetched pages for reproducibility and fixtures (cookies and authorization headers are removed)
      </label>
      {draft.warc_capture.enabled && (
        <label className="field inline">
          <span>Delete captures after (days)</span>
          <input type="number" min={1} max={3650} value={draft.warc_capture.retention_days} onChange={(e) => setDraft({ ...draft, warc_capture: { ...draft.warc_capture, retention_days: Number(e.target.value) } })} />
        </label>
      )}
      <h3 className="section-label">Suggestions</h3>
      <label className="field">
        <span>Draft preset proposals</span>
        <select value={draft.ai_suggestions.provider} onChange={(e) => setDraft({ ...draft, ai_suggestions: { ...draft.ai_suggestions, provider: e.target.value as "local_heuristic" | "model" } })}>
          <option value="local_heuristic">On this computer, without AI (structured data and page structure)</option>
          <option value="model">Also ask a language model (OpenAI-compatible endpoint)</option>
        </select>
      </label>
      {draft.ai_suggestions.provider === "model" && (
        <>
          <div className="split tight-2">
            <label className="field">
              <span>Endpoint</span>
              <input value={draft.ai_suggestions.endpoint} onChange={(e) => setDraft({ ...draft, ai_suggestions: { ...draft.ai_suggestions, endpoint: e.target.value.trim() } })} placeholder="http://127.0.0.1:11434/v1" spellCheck={false} />
            </label>
            <label className="field">
              <span>Model</span>
              <input value={draft.ai_suggestions.model} onChange={(e) => setDraft({ ...draft, ai_suggestions: { ...draft.ai_suggestions, model: e.target.value.trim() } })} placeholder="llama3.1" spellCheck={false} />
            </label>
          </div>
          {remote && (
            <label className="toggle block">
              <input type="checkbox" checked={draft.ai_suggestions.remote_consent} onChange={(e) => setDraft({ ...draft, ai_suggestions: { ...draft.ai_suggestions, remote_consent: e.target.checked } })} /> Allow
              sending page text to this remote endpoint. Emails, phone numbers, and long numbers are removed first.
            </label>
          )}
        </>
      )}
      <div className="row-actions">
        <button type="button" className="btn btn-primary" onClick={() => void save()}>
          Save collection settings
        </button>
        <button type="button" className="btn" onClick={() => void purge()}>
          Clear HTTP cache
        </button>
      </div>
      {notice && <p className="note small">{notice}</p>}
      <ErrorNote message={error} />
    </section>
  );
}

function UpdatesSection({ onUpdateInfo }: { onUpdateInfo?: (info: UpdateInfo | null) => void }) {
  const [prefs, setPrefs] = useState<UpdatePrefs>(() => loadSetting("updates", DEFAULT_UPDATE_PREFS));
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => saveSetting("updates", prefs), [prefs]);

  const check = async () => {
    setBusy("check");
    setError(null);
    try {
      const result = await updates.check(prefs.repository, prefs.channel);
      setInfo(result);
      onUpdateInfo?.(result);
      setPrefs((p) => ({ ...p, lastCheck: Date.now() }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel">
      <h2>Updates</h2>
      <p className="muted small">
        Releases are discovered on GitHub. An update is installed only after its signature verifies against the key built into this app, only with your approval, and never while jobs are running.
      </p>
      <div className="split tight-2">
        <label className="field">
          <span>GitHub repository</span>
          <input value={prefs.repository} onChange={(e) => setPrefs({ ...prefs, repository: e.target.value.trim() })} placeholder="owner/dataforge" spellCheck={false} />
        </label>
        <label className="field">
          <span>Channel</span>
          <select value={prefs.channel} onChange={(e) => setPrefs({ ...prefs, channel: e.target.value as UpdatePrefs["channel"] })}>
            <option value="stable">Stable</option>
            <option value="beta">Beta (prereleases, may be unstable)</option>
          </select>
        </label>
      </div>
      <label className="toggle block">
        <input type="checkbox" checked={prefs.autoCheck} onChange={(e) => setPrefs({ ...prefs, autoCheck: e.target.checked })} /> Check at startup (at most once every 24 hours)
      </label>
      <div className="row-actions">
        <button type="button" className="btn" disabled={!isTauri() || !prefs.repository || busy !== null} onClick={() => void check()}>
          {busy === "check" ? "Checking…" : "Check now"}
        </button>
        {prefs.lastCheck && <span className="muted small">Last checked {formatTime(new Date(prefs.lastCheck).toISOString())}</span>}
        {!isTauri() && <span className="muted small">Available in the desktop app.</span>}
      </div>
      {info && !info.available && <p className="note">You are on the latest version.</p>}
      {info?.available && (
        <div className="note">
          <p>
            <strong>Version {info.version}</strong> is available (you have {info.current_version}){info.published_at ? `, published ${info.published_at}` : ""}.
          </p>
          {info.notes && <pre className="release-notes">{info.notes}</pre>}
          <ConfirmButton
            label="Download and install"
            title={`Install DataForge ${info.version}?`}
            body={<p>The download is verified before installing. DataForge restarts to finish; database changes are backed up before they run. Active jobs block installation.</p>}
            confirmLabel="Install and restart"
            onConfirm={async () => {
              setBusy("install");
              try {
                await updates.install();
              } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
                setBusy(null);
              }
            }}
          />
        </div>
      )}
      <ErrorNote message={error} />
    </section>
  );
}

function CredentialsSection() {
  const [list, setList] = useState<{ name: string; stored: boolean }[] | null>(null);
  const [name, setName] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const reload = async () => {
    if (!isTauri()) return;
    try {
      setList(await credentials.list());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  useEffect(() => void reload(), []);

  return (
    <section className="panel">
      <h2>API credentials</h2>
      <p className="muted small">Stored in the Windows Credential Manager. DataForge never shows a saved value again, never writes it to project files or logs, and sends it only to the hosts of the API preset that uses it.</p>
      {!isTauri() && <p className="muted">Available in the desktop app.</p>}
      {list && list.length > 0 && (
        <ul className="plain-list">
          {list.map((c) => (
            <li key={c.name} className="list-row">
              <code>{c.name}</code> <span className="muted small">{c.stored ? "stored" : "missing from credential store"}</span>
              <ConfirmButton
                label={`Delete ${c.name}`}
                danger
                title={`Delete credential “${c.name}”?`}
                body={<p>Presets that reference it will stop until you save it again.</p>}
                confirmLabel="Delete"
                onConfirm={async () => {
                  await credentials.remove(c.name);
                  await reload();
                }}
              />
            </li>
          ))}
        </ul>
      )}
      {isTauri() && (
        <form
          className="split tight-3"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await credentials.save(name, secret);
              setSecret("");
              setName("");
              setError(null);
              await reload();
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            }
          }}
        >
          <label className="field">
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="vendor-api" autoComplete="off" />
          </label>
          <label className="field">
            <span>Secret</span>
            <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" />
          </label>
          <button type="submit" className="btn btn-primary" disabled={!name || !secret}>
            Save
          </button>
        </form>
      )}
      <ErrorNote message={error} />
    </section>
  );
}

type MaintenanceReport = { records: number; coverage: Record<string, number>; drift: { field: string; baseline: number; current: number }[]; suggestions: { field: string; suggested: string; score: number; sample?: string }[] };

/** Compare a live page with a preset's stored fingerprints: coverage drift and suggested selectors. Nothing is changed. */
function MaintenanceCheck({ presets }: { presets: PresetRow[] }) {
  const candidates = presets.filter((p) => p.extraction?.record_root);
  const [key, setKey] = useState("");
  const [url, setUrl] = useState("");
  const [purpose, setPurpose] = useState("internal_analysis");
  const [report, setReport] = useState<MaintenanceReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const preset = candidates.find((p) => `${p.id}@${p.version}` === key) ?? candidates[0];
  const check = async () => {
    if (!preset) return;
    setBusy(true);
    try {
      setReport(await call<MaintenanceReport>("preset.maintenance_report", { preset_id: preset.id, preset_version: preset.version, url, purpose }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <details>
      <summary className="small">Check a preset against a live page</summary>
      <p className="muted small">Fetches one permitted page and compares it with the fingerprints saved by the preset's last passing health check.</p>
      <div className="split tight-2">
        <label className="field">
          <span>Preset</span>
          <select value={preset ? `${preset.id}@${preset.version}` : ""} onChange={(e) => setKey(e.target.value)}>
            {candidates.map((p) => (
              <option key={`${p.id}@${p.version}`} value={`${p.id}@${p.version}`}>
                {p.display_name} — {p.id}@{p.version}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Page URL</span>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/listings" spellCheck={false} />
        </label>
      </div>
      <label className="field inline">
        <span>Purpose</span>
        <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
          {PURPOSE_OPTIONS.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="btn btn-small" disabled={!preset || !url.startsWith("https://") || busy} onClick={() => void check()}>
        {busy ? "Checking…" : "Check page"}
      </button>
      {report && (
        <div className="stack small">
          <p>
            {report.records} records ·{" "}
            {Object.entries(report.coverage)
              .map(([field, value]) => `${field} ${Math.round(value * 100)}%`)
              .join(" · ")}
          </p>
          {!report.drift.length && !report.suggestions.length && <p className="note">No drift: the saved selectors still match this page.</p>}
          {report.drift.map((d) => (
            <p key={d.field} className="note note-warning">
              {d.field}: coverage fell from {Math.round(d.baseline * 100)}% to {Math.round(d.current * 100)}%.
            </p>
          ))}
          {report.suggestions.map((s) => (
            <p key={`${s.field}-${s.suggested}`} className="note">
              Suggested selector for {s.field === "__root__" ? "the record root" : s.field}: <code>{s.suggested}</code> ({Math.round(s.score * 100)}% similar){s.sample ? `, e.g. "${s.sample}"` : ""}
            </p>
          ))}
        </div>
      )}
      <ErrorNote message={error} />
    </details>
  );
}

function PresetsSection() {
  const presets = useService<PresetRow[]>("preset.list");
  const packages = useService<PackageRow[]>("preset.packages");
  const [packagePath, setPackagePath] = useState("");
  const [exportText, setExportText] = useState("");
  const [importText, setImportText] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<string>) => {
    try {
      setStatus(await fn());
      setError(null);
      await Promise.all([presets.reload(), packages.reload()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const activePackages = [...new Set((packages.data ?? []).filter((p) => !p.removed_at).map((p) => p.name))];

  return (
    <section className="panel settings-presets">
      <h2>Presets</h2>
      <div className="row-actions">
        <button type="button" className="btn" onClick={() => void run(async () => {
          const results = await call<{ status: string }[]>("preset.health_check");
          return `Health checks: ${results.filter((r) => r.status === "passed").length} passed, ${results.filter((r) => r.status === "failed").length} failed.`;
        })}>
          Run fixture health checks
        </button>
      </div>
      <MaintenanceCheck presets={presets.data ?? []} />
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Preset</th>
              <th scope="col">Source</th>
              <th scope="col">Status</th>
              <th scope="col">Health</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {presets.data?.map((p) => (
              <tr key={`${p.id}@${p.version}`}>
                <th scope="row">
                  {p.display_name}
                  <div className="muted small">
                    <code>
                      {p.id}@{p.version}
                    </code>
                  </div>
                </th>
                <td>{p.package ? `package ${p.package}` : p.source}</td>
                <td>
                  <span className={`badge ${p.status === "active" ? "badge-completed" : p.status === "disabled" ? "badge-failed" : "badge-paused"}`}>{p.status}</span>
                  {p.status === "deprecated" && p.successor && <div className="small muted">use {p.successor}</div>}
                </td>
                <td className="small">
                  {p.health_status ? (
                    <>
                      {p.health_status.status === "passed" ? "✓ passed" : `✕ ${p.health_status.failures[0] ?? "failed"}`}
                      {(p.health_status.suggestions ?? []).map((s) => (
                        <div key={`${s.field}-${s.suggested}`} className="note note-warning small">
                          Suggested fix for {s.field === "__root__" ? "the record root" : s.field}: <code>{s.suggested}</code> ({Math.round(s.score * 100)}% similar). Save a new version to apply it.
                        </div>
                      ))}
                      <div className="muted">{formatTime(p.health_status.checked_at)}</div>
                    </>
                  ) : (
                    <span className="muted">not checked</span>
                  )}
                </td>
                <td>
                  {p.source === "custom" && (
                    <button type="button" className="btn btn-small" onClick={() => void run(async () => {
                      setExportText(JSON.stringify(await call("preset.export_custom", { preset_id: p.id, preset_version: p.version }), null, 2));
                      return `Exported ${p.id}@${p.version} below. It contains no credentials or collected data.`;
                    })}>
                      Export
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <details className="settings-subsection">
      <summary className="section-label">Signed preset packages</summary>
      <p className="muted small">Packages must be signed by a trusted Ed25519 key and pass their fixture tests before they install. Rolling back removes the newest version; jobs keep the exact preset version they started with.</p>
      <PathInput label="Package file (.dfpreset)" value={packagePath} onChange={setPackagePath} placeholder="C:\presets\vendor-pack-1.0.0.dfpreset" onBrowse={isTauri() ? () => pickFile([{ name: "DataForge preset package", extensions: ["dfpreset"] }]) : undefined} />
      <div className="row-actions">
        <button type="button" className="btn" disabled={!packagePath} onClick={() => void run(async () => {
          const result = await call<{ name: string; version: string; presets: string[] }>("preset.install_package", { path: packagePath });
          return `Installed ${result.name}@${result.version}: ${result.presets.join(", ")}`;
        })}>
          Verify and install
        </button>
        {activePackages.map((name) => (
          <ConfirmButton
            key={name}
            label={`Roll back ${name}`}
            title={`Roll back ${name}?`}
            body={<p>The newest installed version is removed and the previous version becomes active.</p>}
            confirmLabel="Roll back"
            onConfirm={() => void run(async () => {
              const result = await call<{ removed: string; active: string }>("preset.rollback_package", { name });
              return `Rolled back ${name} from ${result.removed} to ${result.active}.`;
            })}
          />
        ))}
      </div>
      {!!packages.data?.length && (
        <ul className="small">
          {packages.data.map((p) => (
            <li key={`${p.name}@${p.version}`}>
              {p.name}@{p.version} · key {p.key_id} · installed {formatTime(p.installed_at)} {p.removed_at ? `· rolled back ${formatTime(p.removed_at)}` : "· active"}
            </li>
          ))}
        </ul>
      )}

      </details>

      <details className="settings-subsection">
      <summary className="section-label">Import or export a custom preset</summary>
      {exportText && <textarea className="code full" rows={8} readOnly value={exportText} aria-label="Exported preset" />}
      <label className="field">
        <span>Paste an exported preset</span>
        <textarea className="code" rows={5} value={importText} onChange={(e) => setImportText(e.target.value)} spellCheck={false} />
      </label>
      <button type="button" className="btn" disabled={!importText} onClick={() => void run(async () => {
        const result = await call<{ id: string; version: string }>("preset.import_custom", { document: JSON.parse(importText) });
        setImportText("");
        return `Imported ${result.id}@${result.version}.`;
      })}>
        Validate and import
      </button>
      </details>
      {status && <p className="note" role="status">{status}</p>}
      <ErrorNote message={error ?? presets.error} />
    </section>
  );
}
