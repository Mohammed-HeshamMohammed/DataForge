import { useEffect, useMemo, useRef, useState } from "react";
import { CustomSelect } from "../../components/CustomSelect.tsx";
import { ErrorNote } from "../../components/ui.tsx";
import { automationHost, getZoom, type Bounds } from "../../lib/desktop.ts";
import { useService } from "../../lib/hooks.ts";
import { call, isTauri } from "../../lib/ipc.ts";
import type { Dataset, Row } from "../../lib/types.ts";

type StepType = "click" | "fill" | "wait" | "read";
type Step = { type: StepType; selector?: string; value?: string; milliseconds?: number };
type Workflow = { id: string; name: string; start_url: string; allowed_host: string; dataset_id: string | null; steps: Step[]; created_at: string; updated_at: string };
type Pick = { selector: string; text: string; tag: string };
type ActionResult = { ok: boolean; value?: string | null; error?: string };

const EMPTY_STEP: Step = { type: "click", selector: "", value: "" };
const ACTIONS = [
  { value: "click", label: "Click", description: "Click a visible link, button, checkbox, or radio control." },
  { value: "fill", label: "Fill", description: "Type text or a {{column}} value into a non-secret field." },
  { value: "wait", label: "Wait", description: "Pause between page actions." },
  { value: "read", label: "Read", description: "Capture visible text into this run's results." },
];

function boundsFor(node: HTMLElement): Bounds {
  const rect = node.getBoundingClientRect();
  const zoom = getZoom();
  return { x: rect.left / zoom, y: rect.top / zoom, width: rect.width / zoom, height: rect.height / zoom };
}

function renderTemplate(value: string | undefined, row: Record<string, unknown>): string {
  return (value ?? "").replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (_all, key: string) => String(row[key] ?? ""));
}

export function Automation({ active = true }: { active?: boolean }) {
  const datasets = useService<Dataset[]>("dataset.list");
  const workflows = useService<Workflow[]>("automation.list");
  const previewRef = useRef<HTMLDivElement>(null);
  const opened = useRef(false);
  const [id, setId] = useState<string | null>(null);
  const [name, setName] = useState("New browser workflow");
  const [startUrl, setStartUrl] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [steps, setSteps] = useState<Step[]>([{ ...EMPTY_STEP }]);
  const [selectedStep, setSelectedStep] = useState(0);
  const [status, setStatus] = useState("Choose data and define the page actions.");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [authorized, setAuthorized] = useState(false);
  const [results, setResults] = useState<{ row: number; values: string[]; error?: string }[]>([]);
  const selected = steps[selectedStep] ?? steps[0];
  const dataset = datasets.data?.find((item) => item.id === datasetId);
  const columns = useService<{ name: string }[]>(datasetId ? "dataset.profile" : null, datasetId ? { dataset_id: datasetId } : {});

  const workflowOptions = useMemo(() => (workflows.data ?? []).map((item) => ({ value: item.id, label: item.name, description: `${item.steps.length} steps · ${item.allowed_host}` })), [workflows.data]);
  const datasetOptions = useMemo(() => (datasets.data ?? []).map((item) => ({ value: item.id, label: item.name, description: `${item.row_count.toLocaleString()} rows · ${item.source_filename}` })), [datasets.data]);

  const setStep = (patch: Partial<Step>) => setSteps((current) => current.map((step, index) => index === selectedStep ? { ...step, ...patch } : step));
  const loadWorkflow = (workflowId: string) => {
    const workflow = workflows.data?.find((item) => item.id === workflowId);
    if (!workflow) return;
    setId(workflow.id); setName(workflow.name); setStartUrl(workflow.start_url); setDatasetId(workflow.dataset_id ?? ""); setSteps(workflow.steps); setSelectedStep(0); setResults([]); setError(null);
  };

  useEffect(() => {
    if (!isTauri() || !opened.current) return;
    void automationHost.setVisible(active);
  }, [active]);
  useEffect(() => () => { if (opened.current) void automationHost.close().catch(() => {}); }, []);
  useEffect(() => {
    if (!active || !opened.current || !previewRef.current) return;
    const resize = new ResizeObserver(() => previewRef.current && void automationHost.setBounds(boundsFor(previewRef.current)).catch(() => {}));
    resize.observe(previewRef.current);
    return () => resize.disconnect();
  }, [active]);

  const openPage = async () => {
    try {
      if (!authorized) throw new Error("Confirm that you are authorized to automate this website");
      const url = new URL(startUrl);
      if (url.protocol !== "https:") throw new Error("Use an HTTPS start URL");
      if (!previewRef.current) return;
      await automationHost.open(url.href, [url.hostname], boundsFor(previewRef.current));
      opened.current = true; setStatus(`Test page open: ${url.hostname}`); setError(null);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  };

  const pickTarget = async () => {
    try {
      if (!opened.current) throw new Error("Open the test page first");
      await automationHost.call("setMode", ["element", null]);
      setStatus("Click the target element in the page, then choose “Use picked target”.");
      setError(null);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  };
  const usePick = async () => {
    try {
      const picks = await automationHost.call<Pick[]>("takePicks");
      const pick = picks.at(-1);
      if (!pick) throw new Error("No element has been picked yet");
      setStep({ selector: pick.selector }); setStatus(`Target selected: ${pick.tag}${pick.text ? ` · ${pick.text}` : ""}`); setError(null);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  };

  const save = async () => {
    try {
      const saved = await call<Workflow>("automation.save", { id, name, start_url: startUrl, dataset_id: datasetId || null, steps });
      setId(saved.id); await workflows.reload(); setStatus("Workflow saved locally in this project."); setError(null);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  };

  const remove = async () => {
    if (!id) return;
    try {
      await call("automation.delete", { workflow_id: id });
      setId(null); setName("New browser workflow"); setStartUrl(""); setDatasetId(""); setSteps([{ ...EMPTY_STEP }]); setSelectedStep(0); setResults([]);
      await workflows.reload(); setStatus("Workflow deleted."); setError(null);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  };

  const waitForPage = async () => {
    for (let attempt = 0; attempt < 30; attempt++) {
      const info = await automationHost.call<{ ready_state: string }>("pageInfo").catch(() => null);
      if (info?.ready_state === "complete") return;
      await new Promise((resolve) => window.setTimeout(resolve, 200));
    }
    throw new Error("The page did not finish loading in time");
  };

  const run = async (allRows: boolean) => {
    if (!opened.current) return setError("Open the test page first");
    if (!datasetId) return setError("Choose a dataset first");
    if (!authorized) return setError("Confirm that you are authorized to automate this website");
    setBusy(true); setResults([]); setError(null);
    try {
      const rows = await call<Row[]>("dataset.rows", { dataset_id: datasetId, offset: 0, limit: allRows ? Math.min(dataset?.row_count ?? 1, 500) : 1 });
      const output: { row: number; values: string[]; error?: string }[] = [];
      for (const row of rows) {
        const values: string[] = [];
        try {
          await automationHost.navigate(startUrl);
          await waitForPage();
          for (const step of steps) {
            if (step.type === "wait") { await new Promise((resolve) => window.setTimeout(resolve, step.milliseconds ?? 1000)); continue; }
            const result = await automationHost.call<ActionResult>("automation", [{ ...step, value: renderTemplate(step.value, row.raw) }]);
            if (!result.ok) throw new Error(result.error || "Action failed");
            if (step.type === "read") values.push(result.value ?? "");
            if (step.type === "click") await new Promise((resolve) => window.setTimeout(resolve, 500));
            else await new Promise((resolve) => window.setTimeout(resolve, 150));
          }
          output.push({ row: row.row_number, values });
        } catch (err) { output.push({ row: row.row_number, values, error: err instanceof Error ? err.message : String(err) }); }
        setResults([...output]);
      }
      setStatus(`Finished ${output.length} row${output.length === 1 ? "" : "s"}.`);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  if (!isTauri()) return <section className="panel"><h2>Automation needs the desktop app</h2><p className="muted">Browser actions run in DataForge's isolated native WebView and are not available in browser preview mode.</p></section>;

  return <div className="automation-shell">
    <aside className="automation-builder">
      <div><p className="eyebrow">Row-driven browser automation</p><h2>Workflow builder</h2><p className="muted small">Each CSV or Excel row supplies values to the same safe sequence of page actions.</p></div>
      <label className="field"><span>Saved workflow</span><CustomSelect value={id ?? ""} options={workflowOptions} onChange={loadWorkflow} placeholder="New workflow" ariaLabel="Saved workflow" /></label>
      <label className="field"><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field"><span>Dataset</span><CustomSelect value={datasetId} options={datasetOptions} onChange={setDatasetId} placeholder="Choose imported CSV or Excel data" ariaLabel="Workflow dataset" searchable /></label>
      <label className="field"><span>Start URL</span><div className="input-action"><input value={startUrl} onChange={(e) => setStartUrl(e.target.value)} placeholder="https://portal.example/tasks" /><button className="btn btn-small" type="button" onClick={() => void openPage()}>Open test page</button></div></label>
      <label className="check-row"><input type="checkbox" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} /><span>I am authorized to automate actions on this website. The workflow will not bypass sign-in, CAPTCHA, access controls, or rate limits.</span></label>
      <div className="automation-columns"><span className="section-label">Available variables</span><div className="chip-row">{(columns.data ?? []).map((column) => <button type="button" className="chip" key={column.name} onClick={() => selected?.type === "fill" && setStep({ value: `${selected.value ?? ""}{{${column.name}}}` })}>{`{{${column.name}}}`}</button>)}</div></div>
      <div className="section-label-row"><h3 className="section-label">Actions</h3><button className="btn btn-small" type="button" onClick={() => { setSteps((value) => [...value, { ...EMPTY_STEP }]); setSelectedStep(steps.length); }}>Add action</button></div>
      <ol className="automation-steps">{steps.map((step, index) => <li key={index}><button type="button" className={index === selectedStep ? "is-selected" : ""} onClick={() => setSelectedStep(index)}><strong>{index + 1}. {step.type}</strong><span>{step.type === "wait" ? `${step.milliseconds ?? 1000} ms` : step.selector || "Choose a target"}</span></button></li>)}</ol>
      {selected && <section className="automation-step-editor"><CustomSelect value={selected.type} options={ACTIONS} onChange={(value) => setStep({ type: value as StepType })} ariaLabel="Action type" />
        {selected.type === "wait" ? <label className="field"><span>Milliseconds</span><input type="number" min={100} max={30000} value={selected.milliseconds ?? 1000} onChange={(e) => setStep({ milliseconds: Number(e.target.value) })} /></label> : <>
          <label className="field"><span>CSS selector</span><input className="code" value={selected.selector ?? ""} onChange={(e) => setStep({ selector: e.target.value })} /></label>
          <div className="row-actions"><button className="btn btn-small" type="button" onClick={() => void pickTarget()}>Pick on page</button><button className="btn btn-small" type="button" onClick={() => void usePick()}>Use picked target</button></div>
          {selected.type === "fill" && <label className="field"><span>Value or template</span><input value={selected.value ?? ""} onChange={(e) => setStep({ value: e.target.value })} placeholder="{{email}}" /></label>}
        </>}
        <button className="link-quiet" type="button" disabled={steps.length === 1} onClick={() => { setSteps((value) => value.filter((_step, index) => index !== selectedStep)); setSelectedStep(Math.max(0, selectedStep - 1)); }}>Remove action</button>
      </section>}
      <ErrorNote message={error ?? workflows.error ?? datasets.error} /><p className="note">{status}</p>
      <div className="row-actions"><button className="btn" type="button" onClick={() => void save()}>Save workflow</button>{id && <button className="btn btn-danger" type="button" onClick={() => void remove()}>Delete</button>}<button className="btn btn-primary" type="button" disabled={busy} onClick={() => void run(false)}>Test first row</button><button className="btn btn-primary" type="button" disabled={busy} onClick={() => void run(true)}>{busy ? "Running…" : dataset && dataset.row_count > 500 ? "Run first 500 rows" : "Run rows"}</button></div>
    </aside>
    <section className="automation-stage"><div className="automation-preview" ref={previewRef}><div><strong>Browser preview</strong><p className="muted small">Open an HTTPS page to record targets and test this workflow.</p></div></div>
      <div className="automation-results"><div className="section-label-row"><h3>Run results</h3><span className="count-badge">{results.length}</span></div>{!results.length ? <p className="muted small">Read actions and per-row errors appear here.</p> : <div className="table-wrap"><table className="data-table"><thead><tr><th>Row</th><th>Captured values</th><th>Status</th></tr></thead><tbody>{results.map((result) => <tr key={result.row}><td>{result.row}</td><td>{result.values.join(" · ") || "—"}</td><td>{result.error ?? "Completed"}</td></tr>)}</tbody></table></div>}</div>
    </section>
  </div>;
}
