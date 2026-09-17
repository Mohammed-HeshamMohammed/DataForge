import { useCallback, useEffect, useMemo, useState } from "react";
import { call, isTauri, pickDirectory, pickFile } from "../lib/ipc.ts";
import { useService } from "../lib/hooks.ts";
import {
  applyTheme, applyZoom, copyText, getZoom, loadSetting, loadTheme, openPath, revealInFolder, saveSetting, stepZoom, toggleFullscreen, updates, windowAction, type Theme, type UpdateInfo,
} from "../lib/desktop.ts";
import { isActive } from "../lib/format.ts";
import { isTextField, matchesShortcut, type Command } from "../lib/commands.ts";
import type { Job, Project } from "../lib/types.ts";
import { ErrorNote, PathInput, useToast } from "../components/ui.tsx";
import { TitleBar, type TitleProject } from "../components/TitleBar.tsx";
import { AboutDialog, ShortcutsDialog } from "../components/CommandPalette.tsx";
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

export const APP_VERSION = "0.2.0";

type Layout = { left: boolean; right: boolean; compact: boolean };
type AppInfo = { app_data_dir: string; logs_dir: string; docs_dir: string | null; plan_file: string | null; project_root: string | null };

export function App() {
  const [theme, setTheme] = useState<Theme>(loadTheme);
  useEffect(() => applyTheme(theme), [theme]);
  const current = useService<Project | null>("project.current");
  const recent = useService<Project[]>("project.recent");
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("dashboard");
  const [context, setContext] = useState<{ datasetId?: string; jobId?: string }>({});
  const [refreshToken, setRefreshToken] = useState(0);
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [layout, setLayout] = useState<Layout>(() => loadSetting<Layout>("layout", { left: true, right: true, compact: false }));
  const [zoom, setZoom] = useState(getZoom);
  const [dialog, setDialog] = useState<"shortcuts" | "about" | null>(null);
  const [centerOpen, setCenterOpen] = useState(false);
  const [info, setInfo] = useState<AppInfo | null>(null);
  const toast = useToast();

  useEffect(() => saveSetting("layout", layout), [layout]);
  useEffect(() => {
    document.documentElement.dataset.density = layout.compact ? "compact" : "comfortable";
  }, [layout.compact]);
  useEffect(() => {
    void applyZoom(zoom).catch(() => {});
  }, [zoom]);

  // Optional startup check, at most once per 24 hours; installing always needs explicit approval.
  useEffect(() => {
    if (!isTauri()) return;
    const prefs = loadSetting<UpdatePrefs>("updates", DEFAULT_UPDATE_PREFS);
    if (!prefs.autoCheck || !prefs.repository || (prefs.lastCheck && Date.now() - prefs.lastCheck < 86_400_000)) return;
    void updates
      .check(prefs.repository, prefs.channel)
      .then((result) => {
        setUpdateInfo(result);
        saveSetting("updates", { ...prefs, lastCheck: Date.now() });
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (current.data) setProject(current.data);
  }, [current.data]);
  useEffect(() => {
    void call<AppInfo>("app.info").then(setInfo).catch(() => {});
  }, [project?.root_path]);

  const navigate: Navigate = useCallback((next, ctx = {}) => {
    setContext(ctx);
    setTab(next);
  }, []);

  const fail = useCallback((err: unknown) => toast.show({ message: err instanceof Error ? err.message : String(err) }), [toast]);

  const openProject = useCallback(
    async (command: "project.open" | "project.create", path: string) => {
      try {
        const opened = await call<Project>(command, { path });
        setProject(opened);
        setTab("dashboard");
        void recent.reload();
        toast.show({ message: `${command === "project.create" ? "Created" : "Opened"} ${opened.name}` });
      } catch (err) {
        fail(err);
      }
    },
    [fail, recent, toast],
  );

  const pickProject = useCallback(
    async (command: "project.open" | "project.create") => {
      if (!isTauri()) {
        setProject(null);
        return;
      }
      const folder = await pickDirectory();
      if (folder) await openProject(command, folder);
    },
    [openProject],
  );

  const commands = useMemo<Command[]>(() => {
    const hasProject = !!project;
    const eachActiveJob = async (command: "job.pause" | "job.cancel") => {
      const jobs = await call<Job[]>("job.list", { limit: 100 });
      const active = jobs.filter((j) => isActive(j.state) && j.state !== (command === "job.pause" ? "paused" : "cancelled"));
      await Promise.allSettled(active.map((j) => call(command, { job_id: j.id })));
      toast.show({ message: active.length ? `${command === "job.pause" ? "Pausing" : "Cancelling"} ${active.length} job(s)` : "No active jobs" });
    };
    const focusRegion = (selector: string) => {
      const region = document.querySelector<HTMLElement>(selector);
      const target = region?.querySelector<HTMLElement>("input, button, select, textarea, [tabindex]:not([tabindex='-1'])") ?? region;
      target?.focus();
    };
    const selectAll = () => {
      const active = document.activeElement;
      if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) {
        active.select();
        return;
      }
      const pane = document.querySelector(".pane-body");
      if (pane) window.getSelection()?.selectAllChildren(pane);
    };
    const editAction = (action: "cut" | "copy") => () => {
      if (!document.execCommand(action)) toast.show({ message: `Select text first, then ${action}.` });
    };
    const paste = async () => {
      try {
        const text = await navigator.clipboard.readText();
        if (isTextField(document.activeElement)) document.execCommand("insertText", false, text);
        else toast.show({ message: "Click into a text field to paste." });
      } catch {
        toast.show({ message: "Paste with Ctrl+V inside a text field." });
      }
    };
    const recentItems: Command[] = (recent.data ?? []).slice(0, 8).map((p, i) => ({
      id: `file.recent.${i}`, label: `${p.name} — ${p.root_path}`, menu: "file", run: () => openProject("project.open", p.root_path),
    }));

    return [
      // File
      { id: "file.newProject", label: "New project…", menu: "file", group: 1, shortcut: "Ctrl+Shift+N", run: () => pickProject("project.create") },
      { id: "file.openProject", label: "Open project…", menu: "file", group: 1, shortcut: "Ctrl+O", run: () => pickProject("project.open") },
      { id: "file.recent", label: "Open recent", menu: "file", group: 1, run: () => {}, children: recentItems, paletteHidden: true },
      {
        id: "file.import", label: "Import data file…", menu: "file", group: 2, shortcut: "Ctrl+I", enabled: hasProject,
        run: async () => {
          const path = await pickFile([{ name: "Data files", extensions: ["csv", "json", "xlsx"] }]);
          if (!path) return navigate("datasets");
          try {
            await call("dataset.import", { path });
            toast.show({ message: `Importing ${path.split(/[\\/]/).pop()}` });
            navigate("datasets");
          } catch (err) {
            fail(err);
          }
        },
      },
      { id: "file.newCollection", label: "New collection…", menu: "file", group: 2, enabled: hasProject, run: () => navigate("scraping") },
      { id: "file.revealProject", label: "Open project folder", menu: "file", group: 3, enabled: hasProject && isTauri(), run: () => revealInFolder(project!.root_path) },
      { id: "file.copyPath", label: "Copy project path", menu: "file", group: 3, enabled: hasProject, run: async () => { await copyText(project!.root_path); toast.show({ message: "Project path copied" }); } },
      { id: "file.closeProject", label: "Close project", menu: "file", group: 4, enabled: hasProject, run: () => setProject(null) },
      { id: "file.exit", label: "Exit", menu: "file", group: 5, shortcut: "Alt+F4", run: () => windowAction("close") },

      // Edit
      { id: "edit.undo", label: "Undo last review decision", menu: "edit", group: 1, shortcut: "Ctrl+Z", nativeInFields: true, enabled: hasProject, run: () => { window.dispatchEvent(new Event("dataforge:undo")); } },
      { id: "edit.cut", label: "Cut", menu: "edit", group: 2, shortcut: "Ctrl+X", nativeInFields: true, run: editAction("cut") },
      { id: "edit.copy", label: "Copy", menu: "edit", group: 2, shortcut: "Ctrl+C", nativeInFields: true, run: editAction("copy") },
      { id: "edit.paste", label: "Paste", menu: "edit", group: 2, shortcut: "Ctrl+V", nativeInFields: true, run: paste },
      { id: "edit.find", label: "Find dataset…", menu: "edit", group: 3, shortcut: "Ctrl+F", enabled: hasProject, run: () => { setLayout((l) => ({ ...l, left: true })); window.setTimeout(() => document.getElementById("dataset-filter")?.focus(), 0); } },
      { id: "edit.settings", label: "Settings", menu: "edit", group: 4, shortcut: "Ctrl+,", enabled: hasProject, run: () => navigate("settings") },

      // Selection
      { id: "selection.all", label: "Select all", menu: "selection", group: 1, shortcut: "Ctrl+A", nativeInFields: true, run: selectAll },
      { id: "selection.clear", label: "Clear selection", menu: "selection", group: 1, run: () => { window.getSelection()?.removeAllRanges(); (document.activeElement as HTMLElement | null)?.blur(); } },
      { id: "selection.datasets", label: "Focus datasets list", menu: "selection", group: 2, shortcut: "Ctrl+Shift+1", enabled: hasProject, run: () => { setLayout((l) => ({ ...l, left: true })); window.setTimeout(() => focusRegion(".sidebar-datasets"), 0); } },
      { id: "selection.main", label: "Focus main pane", menu: "selection", group: 2, shortcut: "Ctrl+Shift+2", enabled: hasProject, run: () => focusRegion(".pane-body") },
      { id: "selection.jobs", label: "Focus jobs panel", menu: "selection", group: 2, shortcut: "Ctrl+Shift+3", enabled: hasProject, run: () => { setLayout((l) => ({ ...l, right: true })); window.setTimeout(() => focusRegion(".sidebar-right"), 0); } },

      // View
      { id: "view.palette", label: "Search commands…", menu: "view", group: 1, shortcut: "Ctrl+Shift+P", run: () => setCenterOpen(true) },
      { id: "view.paletteK", label: "Quick search…", menu: "view", group: 1, shortcut: "Ctrl+K", run: () => setCenterOpen(true), paletteHidden: true },
      { id: "view.left", label: layout.left ? "Hide left sidebar" : "Show left sidebar", menu: "view", group: 2, shortcut: "Ctrl+B", enabled: hasProject, run: () => setLayout((l) => ({ ...l, left: !l.left })) },
      { id: "view.right", label: layout.right ? "Hide jobs sidebar" : "Show jobs sidebar", menu: "view", group: 2, shortcut: "Ctrl+J", enabled: hasProject, run: () => setLayout((l) => ({ ...l, right: !l.right })) },
      { id: "view.density", label: layout.compact ? "Comfortable density" : "Compact density", menu: "view", group: 2, run: () => setLayout((l) => ({ ...l, compact: !l.compact })) },
      { id: "view.theme", label: theme === "dark" ? "Light theme" : "Dark theme", menu: "view", group: 3, shortcut: "Ctrl+Shift+L", run: () => setTheme(theme === "dark" ? "light" : "dark") },
      { id: "view.zoomIn", label: "Zoom in", menu: "view", group: 4, shortcut: "Ctrl+=", run: () => setZoom((z) => stepZoom(z, 1)) },
      { id: "view.zoomOut", label: "Zoom out", menu: "view", group: 4, shortcut: "Ctrl+-", run: () => setZoom((z) => stepZoom(z, -1)) },
      { id: "view.zoomReset", label: `Reset zoom (${Math.round(zoom * 100)}%)`, menu: "view", group: 4, shortcut: "Ctrl+0", run: () => setZoom(1) },
      { id: "view.fullscreen", label: "Toggle full screen", menu: "view", group: 5, shortcut: "F11", run: () => toggleFullscreen().catch(fail) },

      // Go
      ...TABS.map((t, i) => ({ id: `go.${t.id}`, label: t.label, menu: "go" as const, group: 1, shortcut: `Ctrl+${i + 1}`, enabled: hasProject, run: () => navigate(t.id) })),
      { id: "go.next", label: "Next tab", menu: "go", group: 2, shortcut: "Ctrl+Tab", enabled: hasProject, run: () => navigate(TABS[(TABS.findIndex((t) => t.id === tab) + 1) % TABS.length].id) },
      { id: "go.previous", label: "Previous tab", menu: "go", group: 2, shortcut: "Ctrl+Shift+Tab", enabled: hasProject, run: () => navigate(TABS[(TABS.findIndex((t) => t.id === tab) - 1 + TABS.length) % TABS.length].id) },

      // Run
      { id: "run.match", label: "Start matching…", menu: "run", group: 1, enabled: hasProject, run: () => navigate("match") },
      { id: "run.collect", label: "New collection…", menu: "run", group: 1, enabled: hasProject, run: () => navigate("scraping") },
      { id: "run.studio", label: "Open Scrape Studio", menu: "run", group: 1, enabled: hasProject, run: () => navigate("studio") },
      {
        id: "run.health", label: "Run preset health checks", menu: "run", group: 2, enabled: hasProject,
        run: async () => {
          try {
            const results = await call<{ status: string }[]>("preset.health_check");
            toast.show({ message: `Health checks: ${results.filter((r) => r.status === "passed").length} passed, ${results.filter((r) => r.status !== "passed").length} failed` });
          } catch (err) {
            fail(err);
          }
        },
      },
      {
        id: "run.systemCheck", label: "Run system check job", menu: "run", group: 2, enabled: hasProject,
        run: async () => {
          try {
            await call("job.start_fixture", { steps: 8 });
            toast.show({ message: "System check started; follow it in the jobs sidebar" });
            setLayout((l) => ({ ...l, right: true }));
          } catch (err) {
            fail(err);
          }
        },
      },
      { id: "run.pauseAll", label: "Pause all jobs", menu: "run", group: 3, enabled: hasProject, run: () => eachActiveJob("job.pause").catch(fail) },
      { id: "run.cancelAll", label: "Cancel all jobs", menu: "run", group: 3, enabled: hasProject, run: () => eachActiveJob("job.cancel").catch(fail) },
      { id: "run.refresh", label: "Refresh", menu: "run", group: 4, shortcut: "F5", enabled: hasProject, run: () => setRefreshToken((n) => n + 1) },

      // Help
      { id: "help.palette", label: "Show all commands", menu: "help", group: 1, run: () => setCenterOpen(true) },
      { id: "help.shortcuts", label: "Keyboard shortcuts", menu: "help", group: 1, shortcut: "Ctrl+/", run: () => setDialog("shortcuts") },
      { id: "help.plan", label: "Open the master plan", menu: "help", group: 2, enabled: !!info?.plan_file && isTauri(), run: () => openPath(info!.plan_file!).catch(fail) },
      { id: "help.docs", label: "Open documentation folder", menu: "help", group: 2, enabled: !!info?.docs_dir && isTauri(), run: () => openPath(info!.docs_dir!).catch(fail) },
      { id: "help.logs", label: "Open logs folder", menu: "help", group: 2, enabled: !!info && isTauri(), run: () => openPath(info!.logs_dir).catch(fail) },
      { id: "help.updates", label: "Check for updates", menu: "help", group: 3, enabled: hasProject, run: () => { saveSetting("settingsSection", "updates"); navigate("settings"); } },
      { id: "help.about", label: "About DataForge", menu: "help", group: 3, run: () => setDialog("about") },
    ];
  }, [project, recent.data, layout, theme, zoom, tab, info, navigate, pickProject, openProject, fail, toast]);

  // Global shortcuts. Text fields keep their native editing keys (copy, paste, undo, select all).
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const inField = isTextField(event.target);
      const all = commands.flatMap((c) => [c, ...(c.children ?? [])]);
      const command = all.find((c) => c.shortcut && c.shortcut !== "Alt+F4" && matchesShortcut(event, c.shortcut));
      if (!command || command.enabled === false) return;
      if (inField && command.nativeInFields) return;
      if (!inField && command.nativeInFields && ["edit.cut", "edit.copy", "edit.paste"].includes(command.id)) return; // native page copy still works
      if (inField && !event.ctrlKey && !event.metaKey && event.key !== "F5" && event.key !== "F11") return;
      event.preventDefault();
      void command.run();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [commands]);

  const titleProject: TitleProject | null = project ? { name: project.name, path: project.root_path } : null;

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
    body = current.loaded ? <ProjectGate onOpen={(p) => { setProject(p); void recent.reload(); }} /> : <div className="gate muted">Loading…</div>;
  } else {
    body = (
      <Workspace
        key={project.root_path}
        project={project}
        tab={tab}
        context={context}
        navigate={navigate}
        layout={layout}
        refreshToken={refreshToken}
        onUpdateInfo={setUpdateInfo}
      />
    );
  }

  return (
    <div className="window">
      <TitleBar
        theme={theme}
        onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
        updateAvailable={!!updateInfo?.available}
        onUpdate={() => {
          if (!project) return;
          saveSetting("settingsSection", "updates");
          navigate("settings");
        }}
        commands={commands}
        project={titleProject}
        onSettings={project ? () => navigate("settings") : undefined}
        commandCenterOpen={centerOpen}
        onCommandCenterChange={setCenterOpen}
      />
      {body}
      {toast.node}
      {dialog === "shortcuts" && <ShortcutsDialog commands={commands} onClose={() => setDialog(null)} />}
      {dialog === "about" && (
        <AboutDialog
          version={APP_VERSION}
          onClose={() => setDialog(null)}
          details={[
            ["Project", project?.root_path ?? "none open"],
            ["App data", info?.app_data_dir ?? "—"],
            ["Logs", info?.logs_dir ?? "—"],
            ["Runtime", isTauri() ? "Desktop app" : "Browser development bridge"],
          ]}
        />
      )}
    </div>
  );
}

function Workspace({
  project,
  tab,
  context,
  navigate,
  layout,
  refreshToken,
  onUpdateInfo,
}: {
  project: Project;
  tab: Tab;
  context: { datasetId?: string; jobId?: string };
  navigate: Navigate;
  layout: Layout;
  refreshToken: number;
  onUpdateInfo: (info: UpdateInfo | null) => void;
}) {
  const [localRefresh, setLocalRefresh] = useState(0);
  const summary = useService<ProjectSummary>("project.summary", {}, 2500);
  const active = TABS.find((t) => t.id === tab)!;
  useEffect(() => {
    if (refreshToken) void summary.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  const shellClass = ["shell", layout.left ? "" : "hide-left", layout.right ? "" : "hide-right"].filter(Boolean).join(" ");
  return (
    <div className={shellClass}>
      {layout.left && <Sidebar project={project} summary={summary.data} navigate={navigate} />}
      <main className="main-pane" aria-labelledby="pane-title">
        <PaneHeader
          title={active.title}
          tabs={TABS}
          activeTab={tab}
          onTab={(id) => navigate(id)}
          chip={{ label: "Needs review", value: summary.data?.totals.pending_review ?? 0, onClick: () => navigate("match") }}
          onRefresh={() => {
            setLocalRefresh((k) => k + 1);
            void summary.reload();
          }}
        />
        <div className={tab === "studio" ? "pane-body pane-body-fill" : "pane-body"} key={`${tab}-${localRefresh}-${refreshToken}`}>
          {tab === "dashboard" && <Dashboard navigate={navigate} />}
          {tab === "scraping" && <Scraping navigate={navigate} />}
          {tab === "studio" && <Studio navigate={navigate} />}
          {tab === "datasets" && <Datasets navigate={navigate} initialDatasetId={context.datasetId} />}
          {tab === "match" && <MatchTab key={`${context.datasetId}-${context.jobId}`} initialDatasetId={context.datasetId} initialJobId={context.jobId} />}
          {tab === "settings" && <Settings project={project} onUpdateInfo={onUpdateInfo} />}
        </div>
      </main>
      {layout.right && <RightSidebar navigate={navigate} onChanged={() => void summary.reload()} />}
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
        <p className="lede">Projects keep datasets, jobs, review decisions, evidence, and exports in one folder on this computer.</p>
        <div className="gate-grid">
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
          {recent.data && recent.data.length > 0 && (
            <section>
              <h2 className="section-label">Recent projects</h2>
              <ul className="plain-list scroll-list">
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
        </div>
      </main>
    </div>
  );
}
