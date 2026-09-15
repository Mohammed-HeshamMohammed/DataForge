import { useEffect, useState } from "react";
import type { Project } from "../../lib/types.ts";
import { call, isTauri, pickFile } from "../../lib/ipc.ts";
import { useService } from "../../lib/hooks.ts";
import { credentials, loadSetting, saveSetting, updates, type UpdateInfo } from "../../lib/desktop.ts";
import { formatTime } from "../../lib/format.ts";
import { ConfirmButton, ErrorNote, PathInput } from "../../components/ui.tsx";

type PresetRow = { id: string; version: string; display_name: string; source: string; status: string; declared_status: string; successor?: string; package?: string | null; health_status: { status: string; checked_at: string; failures: string[] } | null };
type PackageRow = { name: string; version: string; key_id: string; installed_at: string; removed_at: string | null };

export type UpdatePrefs = { repository: string; channel: "stable" | "beta"; autoCheck: boolean; lastCheck: number | null };
export const DEFAULT_UPDATE_PREFS: UpdatePrefs = { repository: "", channel: "stable", autoCheck: false, lastCheck: null };

export function Settings({ project, onUpdateInfo }: { project: Project; onUpdateInfo?: (info: UpdateInfo | null) => void }) {
  return (
    <div className="stack">
      <ProjectSection project={project} />
      <UpdatesSection onUpdateInfo={onUpdateInfo} />
      <CredentialsSection />
      <PresetsSection />
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
    <section className="panel">
      <h2>Presets</h2>
      <div className="row-actions">
        <button type="button" className="btn" onClick={() => void run(async () => {
          const results = await call<{ status: string }[]>("preset.health_check");
          return `Health checks: ${results.filter((r) => r.status === "passed").length} passed, ${results.filter((r) => r.status === "failed").length} failed.`;
        })}>
          Run fixture health checks
        </button>
      </div>
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

      <h3 className="section-head">Signed preset packages</h3>
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

      <h3 className="section-head">Import or export a custom preset</h3>
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
      {status && <p className="note" role="status">{status}</p>}
      <ErrorNote message={error ?? presets.error} />
    </section>
  );
}
