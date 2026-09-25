import { useState } from "react";
import { ErrorNote, PathInput } from "../../components/ui.tsx";
import { toErrorMessage } from "../../lib/errors.ts";
import { useService } from "../../lib/hooks.ts";
import { call, isTauri, pickDirectory } from "../../lib/ipc.ts";
import type { Project } from "../../lib/types.ts";

export function ProjectGate({ onOpen }: { onOpen: (project: Project) => void }) {
  const recent = useService<Project[]>("project.recent");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sampleBusy, setSampleBusy] = useState(false);

  const open = async (command: "project.open" | "project.create", target = path) => {
    try {
      onOpen(await call<Project>(command, { path: target, name: name || undefined }));
    } catch (err) {
      setError(toErrorMessage(err));
    }
  };

  const openSample = async () => {
    setSampleBusy(true);
    try {
      onOpen(await call<Project>("project.create_sample"));
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setSampleBusy(false);
    }
  };

  return (
    <div className="shell shell-gate">
      <main className="main-pane gate">
        <p className="eyebrow">Local-first data workspace</p>
        <h1 className="pane-title">Start with DataForge</h1>
        <p className="lede">Keep datasets, collection runs, matching decisions, evidence, and exports together in a project on this computer.</p>
        <section className="sample-project-card" aria-labelledby="sample-project-title">
          <div>
            <h2 id="sample-project-title">New to DataForge?</h2>
            <p>Open a safe example with fictional contacts and explore the full workflow. You can remove it whenever you like.</p>
          </div>
          <button type="button" className="btn btn-primary" disabled={sampleBusy} onClick={() => void openSample()}>
            {sampleBusy ? "Preparing example…" : "Try the sample project"}
          </button>
        </section>
        <div className="gate-grid">
          <section>
            <h2 className="section-label">Open or create your own</h2>
            <PathInput label="Project folder" value={path} onChange={setPath} placeholder="C:\Users\you\Documents\DataForge\My project" onBrowse={isTauri() ? pickDirectory : undefined} />
            <label className="field">
              <span>Name (new projects)</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Defaults to the folder name" />
            </label>
            <div className="row-actions">
              <button type="button" className="btn" disabled={!path} onClick={() => void open("project.open")}>Open existing</button>
              <button type="button" className="btn btn-primary" disabled={!path} onClick={() => void open("project.create")}>Create project</button>
            </div>
            <ErrorNote message={error} />
          </section>
          {recent.data && recent.data.length > 0 && (
            <section>
              <h2 className="section-label">Recent projects</h2>
              <ul className="plain-list scroll-list">
                {recent.data.map((project) => (
                  <li key={project.root_path}>
                    <button type="button" className="list-item" onClick={() => void open("project.open", project.root_path)}>
                      <span className="list-item-title">{project.name}</span>
                      <span className="list-item-sub">{project.root_path}</span>
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
