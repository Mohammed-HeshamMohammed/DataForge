import { useEffect, useState } from "react";
import { call, isTauri, pickDirectory } from "../lib/ipc.ts";
import { useService } from "../lib/hooks.ts";
import { applyTheme, loadSetting, loadTheme, saveSetting, updates, type Theme, type UpdateInfo } from "../lib/desktop.ts";
import type { Project } from "../lib/types.ts";
import { ErrorNote, PathInput } from "../components/ui.tsx";
import { TitleBar } from "../components/TitleBar.tsx";
import { Sidebar, type ProjectSummary } from "../components/Sidebar.tsx";
import { PaneHeader } from "../components/PaneHeader.tsx";
import { RightSidebar } from "../components/RightSidebar.tsx";
import { Dashboard } from "../features/dashboard/Dashboard.tsx";
import { Datasets } from "../features/datasets/Datasets.tsx";
import { Scraping } from "../features/scraping/Scraping.tsx";
import { MatchTab } from "../features/matching/MatchTab.tsx";
import { DEFAULT_UPDATE_PREFS, Settings, type UpdatePrefs } from "../features/settings/Settings.tsx";
import { Studio } from "../features/studio/Studio.tsx";

export type Tab = "dashboard" | "scraping" | "studio" | "datasets" | "match" | "settings";
export type Navigate = (tab: Tab, context?: { datasetId?: string; jobId?: string }) => void;

export const TABS: { id: Tab; label: string; title: string }[] = [
  { id: "dashboard", label: "Overview", title: "Overview" },
  { id: "scraping", label: "Scraping", title: "Scraping" },
  { id: "studio", label: "Scrape Studio", title: "Scrape Studio" },
  { id: "datasets", label: "Datasets", title: "Datasets" },
  { id: "match", label: "Match & Deduplicate", title: "Match & Deduplicate" },
  { id: "settings", label: "Settings", title: "Settings" },
];

export const APP_VERSION = "0.1.0";

export function App() {
  const [theme, setTheme] = useState<Theme>(loadTheme);
  useEffect(() => applyTheme(theme), [theme]);
  const current = useService<Project | null>("project.current");
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("dashboard");
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);

  // Optional startup check, at most once per 24 hours; installing always needs explicit approval.
  useEffect(() => {
    if (!isTauri()) return;
    const prefs = loadSetting<UpdatePrefs>("updates", DEFAULT_UPDATE_PREFS);
    if (!prefs.autoCheck || !prefs.repository || (prefs.lastCheck && Date.now() - prefs.lastCheck < 86_400_000)) return;
    void updates
      .check(prefs.repository, prefs.channel)
      .then((info) => {
        setUpdateInfo(info);
        saveSetting("updates", { ...prefs, lastCheck: Date.now() });
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (current.data) setProject(current.data);
  }, [current.data]);

  let body;
  if (current.error) {
    body = (
      <div className="gate">
        <ErrorNote message={`The DataForge service is unavailable: ${current.error}`} />
        <button type="button" className="btn" onClick={() => void current.reload()}>
          Try again
        </button>
      </div>
    );
  } else if (!project) {
    body = current.loaded ? <ProjectGate onOpen={setProject} /> : <div className="gate muted">Loading…</div>;
  } else {
    body = <Workspace key={project.root_path} project={project} tab={tab} setTab={setTab} onUpdateInfo={setUpdateInfo} onSwitchProject={() => setProject(null)} />;
  }

  return (
    <div className="window">
      <TitleBar theme={theme} onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")} updateAvailable={!!updateInfo?.available} onUpdate={() => setTab("settings")} />
      {body}
    </div>
  );
}

function Workspace({ project, tab, setTab, onUpdateInfo, onSwitchProject }: { project: Project; tab: Tab; setTab: (tab: Tab) => void; onUpdateInfo: (info: UpdateInfo | null) => void; onSwitchProject: () => void }) {
  const [context, setContext] = useState<{ datasetId?: string; jobId?: string }>({});
  const [refreshKey, setRefreshKey] = useState(0);
  const summary = useService<ProjectSummary>("project.summary", {}, 2500);

  const navigate: Navigate = (next, ctx = {}) => {
    setContext(ctx);
    setTab(next);
  };
  const active = TABS.find((t) => t.id === tab)!;

  return (
    <div className="shell">
      <Sidebar project={project} summary={summary.data} navigate={navigate} onSwitchProject={onSwitchProject} />
      <main className="main-pane" aria-labelledby="pane-title">
        <PaneHeader
          title={active.title}
          tabs={TABS}
          activeTab={tab}
          onTab={(id) => navigate(id)}
          chip={{ label: "Needs review", value: summary.data?.totals.pending_review ?? 0, onClick: () => navigate("match") }}
          onRefresh={() => {
            setRefreshKey((k) => k + 1);
            void summary.reload();
          }}
        />
        <div className={tab === "studio" ? "pane-body pane-body-fill" : "pane-body"} key={`${tab}-${refreshKey}`}>
          {tab === "dashboard" && <Dashboard navigate={navigate} />}
          {tab === "scraping" && <Scraping navigate={navigate} />}
          {tab === "studio" && <Studio navigate={navigate} />}
          {tab === "datasets" && <Datasets navigate={navigate} initialDatasetId={context.datasetId} />}
          {tab === "match" && <MatchTab key={`${context.datasetId}-${context.jobId}`} initialDatasetId={context.datasetId} initialJobId={context.jobId} />}
          {tab === "settings" && <Settings project={project} onUpdateInfo={onUpdateInfo} />}
        </div>
        <span className="version-badge">v{APP_VERSION}</span>
      </main>
      <RightSidebar navigate={navigate} onChanged={() => void summary.reload()} />
    </div>
  );
}

function ProjectGate({ onOpen }: { onOpen: (project: Project) => void }) {
  const recent = useService<Project[]>("project.recent");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const open = async (command: "project.open" | "project.create", target = path) => {
    try {
      onOpen(await call<Project>(command, { path: target, name: name || undefined }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="shell shell-gate">
      <main className="main-pane gate">
        <p className="eyebrow">Local-first data workspace</p>
        <h1 className="pane-title">Open a project</h1>
        <p className="lede">Projects keep datasets, jobs, review decisions, and exports in one folder on this computer.</p>
        {recent.data && recent.data.length > 0 && (
          <section>
            <h2 className="section-label">Recent projects</h2>
            <ul className="plain-list">
              {recent.data.map((p) => (
                <li key={p.root_path}>
                  <button type="button" className="list-item" onClick={() => void open("project.open", p.root_path)}>
                    <span className="list-item-title">{p.name}</span>
                    <span className="list-item-sub">{p.root_path}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section>
          <h2 className="section-label">Open or create</h2>
          <PathInput label="Project folder" value={path} onChange={setPath} placeholder="C:\Users\you\Documents\DataForge\My project" onBrowse={isTauri() ? pickDirectory : undefined} />
          <label className="field">
            <span>Name (new projects)</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Defaults to the folder name" />
          </label>
          <div className="row-actions">
            <button type="button" className="btn" disabled={!path} onClick={() => void open("project.open")}>
              Open existing
            </button>
            <button type="button" className="btn btn-primary" disabled={!path} onClick={() => void open("project.create")}>
              Create project
            </button>
          </div>
          <ErrorNote message={error} />
        </section>
      </main>
    </div>
  );
}
