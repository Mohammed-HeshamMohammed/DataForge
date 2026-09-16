import { useEffect, useMemo, useRef, useState } from "react";
import { call } from "../../lib/ipc.ts";
import { useJob, useKeyboardShortcuts, useService } from "../../lib/hooks.ts";
import { displayValue, formatCount, formatTime, isActive } from "../../lib/format.ts";
import type { Dataset, Job, MatchResults, ReviewItem } from "../../lib/types.ts";
import { ConfirmButton, ErrorNote, JobProgress, Metric, StateBadge, useToast } from "../../components/ui.tsx";
import { MappingEditor } from "../../components/MappingEditor.tsx";
import { ImportForm } from "../datasets/Datasets.tsx";

type Step = 1 | 2 | 3 | 4 | 5;
const STEPS: { step: Step; label: string }[] = [
  { step: 1, label: "Data" },
  { step: 2, label: "Map fields" },
  { step: 3, label: "Preview" },
  { step: 4, label: "Review" },
  { step: 5, label: "Export" },
];

type Settings = { strictness: "conservative" | "balanced"; max_block_size: number; preview_size: number };
const DEFAULT_SETTINGS: Settings = { strictness: "conservative", max_block_size: 200, preview_size: 10000 };

export function MatchTab({ initialDatasetId, initialJobId }: { initialDatasetId?: string; initialJobId?: string }) {
  const datasets = useService<Dataset[]>("dataset.list");
  const jobs = useService<Job[]>("job.list", { limit: 50 }, 2000);
  const [datasetId, setDatasetId] = useState<string | null>(initialDatasetId ?? null);
  const [step, setStep] = useState<Step>(initialJobId ? 4 : initialDatasetId ? 2 : 1);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [previewJobId, setPreviewJobId] = useState<string | null>(null);
  const [fullJobId, setFullJobId] = useState<string | null>(initialJobId ?? null);
  const [mappingVersion, setMappingVersion] = useState<number | null>(null);
  const [compareId, setCompareId] = useState<string | null>(null);
  const [trustCompareFirst, setTrustCompareFirst] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dataset = datasets.data?.find((d) => d.id === datasetId) ?? null;
  const compareDataset = datasets.data?.find((d) => d.id === compareId) ?? null;
  const sourceTrust = compareId && datasetId ? (trustCompareFirst ? [compareId, datasetId] : [datasetId, compareId]) : [];
  const effectiveSettings = { ...settings, source_trust: sourceTrust };
  const preview = useJob(previewJobId);
  const full = useJob(fullJobId);
  const previewResults = useService<MatchResults>(preview?.state === "completed" ? "match.results" : null, { job_id: previewJobId });
  const fullResults = useService<MatchResults>(full?.state === "completed" ? "match.results" : null, { job_id: fullJobId });

  // A mapping or settings change makes an earlier preview stale.
  const previewStale =
    !!preview &&
    (preview.params.mapping_version !== (mappingVersion ?? dataset?.mapping_version) ||
      (preview.params.compare_dataset_id ?? null) !== compareId ||
      JSON.stringify(preview.params.settings && pickSettings(preview.params.settings as Settings & { source_trust?: string[] })) !== JSON.stringify(pickSettings(effectiveSettings)));
  const history = (jobs.data ?? []).filter((j) => j.kind === "match" && j.params.dataset_id === datasetId);
  const completed = new Set<Step>([...(dataset ? [1 as Step] : []), ...(dataset?.mapping_version ? [2 as Step] : []), ...(preview?.state === "completed" && !previewStale ? [3 as Step] : []), ...(fullResults.data && fullResults.data.pending_review === 0 ? [4 as Step] : [])]);

  const startJob = async (runMode: "preview" | "full") => {
    try {
      const { job_id } = await call<{ job_id: string }>("match.create_job", { dataset_id: datasetId, compare_dataset_id: compareId, run_mode: runMode, settings: effectiveSettings });
      setError(null);
      if (runMode === "preview") setPreviewJobId(job_id);
      else {
        setFullJobId(job_id);
        setStep(4);
      }
      void jobs.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="match">
      <header className="match-header">
        <p className="muted">
          {dataset ? `Dataset: ${dataset.name} — ${formatCount(dataset.row_count)} rows` : "No dataset selected"}
          {full && (
            <>
              {" "}
              · Full job <StateBadge state={full.state} />
            </>
          )}
        </p>
        <ol className="stepper" aria-label="Progress">
          {STEPS.map(({ step: s, label }) => {
            const reachable = s === 1 || (s === 2 && !!dataset) || (s === 3 && !!dataset?.mapping_version) || (s >= 4 && !!fullJobId);
            return (
              <li key={s}>
                <button type="button" className="step" aria-current={step === s ? "step" : undefined} disabled={!reachable} onClick={() => setStep(s)}>
                  <span className="step-index" aria-hidden="true">
                    {completed.has(s) ? "✓" : s}
                  </span>
                  {label}
                  <span className="sr-only">{completed.has(s) ? " (complete)" : ""}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </header>

      <div className="match-grid">
        <section className="match-main">
          <ErrorNote message={error} />
          {step === 1 && (
            <SourceStep
              datasets={datasets.data ?? []}
              selected={datasetId}
              onSelect={(id) => {
                setDatasetId(id);
                setPreviewJobId(null);
                setFullJobId(null);
              }}
              onImported={async (id) => {
                await datasets.reload();
                setDatasetId(id);
                setStep(2);
              }}
              compareId={compareId}
              onCompare={(id) => {
                setCompareId(id);
                setPreviewJobId(null);
              }}
              trustCompareFirst={trustCompareFirst}
              onTrustCompareFirst={setTrustCompareFirst}
            />
          )}
          {step === 2 && dataset && (
            <>
              {compareDataset && <h2>{dataset.name}</h2>}
              <MappingEditor
                datasetId={dataset.id}
                onSaved={(version) => {
                  setMappingVersion(version);
                  void datasets.reload();
                  if (!compareDataset) setStep(3);
                }}
              />
              {compareDataset && (
                <>
                  <h2 className="section-head">Compare with: {compareDataset.name}</h2>
                  <p className="muted small">Both mappings must use the same entity type and share at least one identifier, contact, address, or URL role. Column names may differ.</p>
                  <MappingEditor datasetId={compareDataset.id} onSaved={() => void datasets.reload()} />
                  <button type="button" className="btn btn-primary" disabled={!dataset.mapping_version || !compareDataset.mapping_version} onClick={() => setStep(3)}>
                    Continue to preview
                  </button>
                </>
              )}
            </>
          )}
          {step === 3 && dataset && (
            <PreviewStep
              settings={settings}
              setSettings={setSettings}
              preview={preview}
              stale={previewStale}
              results={previewResults.data}
              onPreview={() => void startJob("preview")}
              onFull={() => void startJob("full")}
              rowCount={dataset.row_count}
            />
          )}
          {step === 4 && fullJobId && (
            <>
              {full && full.state !== "completed" ? (
                <section>
                  <h2>Running full job</h2>
                  <JobProgress job={full} />
                  <p className="muted">Pause finishes the current safe unit of work. Cancel stops the job; raw rows stay unchanged and no canonical output is published.</p>
                  {isActive(full.state) && (
                    <div className="row-actions">
                      {full.state === "running" && <button type="button" className="btn" onClick={() => void call("job.pause", { job_id: fullJobId })}>Pause</button>}
                      {full.state === "paused" && <button type="button" className="btn" onClick={() => void call("job.resume", { job_id: fullJobId })}>Resume</button>}
                      <ConfirmButton
                        label="Cancel job"
                        danger
                        title="Cancel this match job?"
                        body={<p>Work stops at the next safe checkpoint. Imported rows remain unchanged and no partial canonical dataset is published.</p>}
                        confirmLabel="Cancel job"
                        onConfirm={() => void call("job.cancel", { job_id: fullJobId })}
                      />
                    </div>
                  )}
                </section>
              ) : (
                <ReviewQueue jobId={fullJobId} onChanged={() => void fullResults.reload()} onDone={() => setStep(5)} onBadMapping={() => setStep(2)} />
              )}
            </>
          )}
          {step === 5 && fullJobId && fullResults.data && <ExportStep results={fullResults.data} onExported={() => void fullResults.reload()} />}
        </section>

        <aside className="match-rail" aria-label="Job summary">
          <h2>Job summary</h2>
          <SummaryCounts dataset={dataset} results={fullResults.data ?? (previewStale ? null : previewResults.data)} />
          <h3>History</h3>
          {history.length === 0 && <p className="muted small">No match jobs for this dataset yet.</p>}
          <ul className="plain-list">
            {history.slice(0, 8).map((job) => (
              <li key={job.id}>
                <button
                  type="button"
                  className="card-button"
                  disabled={job.state !== "completed"}
                  onClick={() => {
                    if (job.params.run_mode === "full") {
                      setFullJobId(job.id);
                      setStep(4);
                    } else {
                      setPreviewJobId(job.id);
                      setStep(3);
                    }
                  }}
                >
                  <span>
                    {String(job.params.run_mode)} · mapping v{String(job.params.mapping_version)} <StateBadge state={job.state} />
                  </span>
                  <span className="muted small">{formatTime(job.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}

function pickSettings(settings: Settings & { source_trust?: string[] }) {
  return { strictness: settings.strictness, max_block_size: Number(settings.max_block_size), source_trust: settings.source_trust ?? [] };
}

function SummaryCounts({ dataset, results }: { dataset: Dataset | null; results: MatchResults | null | undefined }) {
  const d = results?.decisions ?? {};
  return (
    <div className="metrics vertical">
      <Metric label="Input rows" value={results?.metrics.input_rows ?? dataset?.row_count} />
      <Metric label="Candidate pairs" value={results?.metrics.candidate_pairs} />
      <Metric label="Safe matches" value={results ? d.match ?? 0 : null} icon="✓" />
      <Metric label="Needs review" value={results ? results.pending_review : null} icon="?" />
      <Metric label="Kept separate" value={results ? d.non_match ?? 0 : null} icon="≠" />
      {results?.run_mode === "full" && <Metric label="Canonical records" value={results.canonical_records} />}
    </div>
  );
}

function SourceStep({
  datasets,
  selected,
  onSelect,
  onImported,
  compareId,
  onCompare,
  trustCompareFirst,
  onTrustCompareFirst,
}: {
  datasets: Dataset[];
  selected: string | null;
  onSelect: (id: string) => void;
  onImported: (id: string) => void;
  compareId: string | null;
  onCompare: (id: string | null) => void;
  trustCompareFirst: boolean;
  onTrustCompareFirst: (value: boolean) => void;
}) {
  const [tab, setTab] = useState<"scrape" | "datasets" | "import">(datasets.some((d) => d.kind === "scrape") ? "scrape" : "datasets");
  const shown = tab === "scrape" ? datasets.filter((d) => d.kind === "scrape") : datasets;
  return (
    <section>
      <h2>Choose data</h2>
      <div className="segmented" role="tablist">
        {(
          [
            ["scrape", "Recent scrape outputs"],
            ["datasets", "Datasets"],
            ["import", "Import file"],
          ] as const
        ).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "import" ? (
        <ImportForm onImported={onImported} />
      ) : shown.length === 0 ? (
        <p className="muted">No data is ready to match yet. Import a file or complete a scrape first.</p>
      ) : (
        <div className="card-grid">
          {shown.map((d) => (
            <button key={d.id} type="button" className="card-button" aria-pressed={d.id === selected} onClick={() => onSelect(d.id)}>
              <strong>{d.name}</strong>
              <span className="muted small">
                {d.kind === "scrape" ? "Scrape output" : "Imported file"} · {formatCount(d.row_count)} rows
              </span>
              <span className="muted small">{d.mapping_version ? `Mapping v${d.mapping_version}` : "No mapping yet"} · {formatTime(d.created_at)}</span>
            </button>
          ))}
        </div>
      )}
      {selected && (
        <fieldset className="scope">
          <legend>Comparison scope</legend>
          <label className="toggle block">
            <input type="radio" name="scope" checked={!compareId} onChange={() => onCompare(null)} /> Within this dataset
          </label>
          <label className="toggle block">
            <input type="radio" name="scope" checked={!!compareId} disabled={datasets.length < 2} onChange={() => onCompare(datasets.find((d) => d.id !== selected)?.id ?? null)} /> Compare with another dataset
          </label>
          {compareId && (
            <div className="split tight-2">
              <label className="field">
                <span>Other dataset</span>
                <select value={compareId} onChange={(e) => onCompare(e.target.value)}>
                  {datasets.filter((d) => d.id !== selected).map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name} ({formatCount(d.row_count)} rows)
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Most trusted source</span>
                <select value={trustCompareFirst ? "compare" : "primary"} onChange={(e) => onTrustCompareFirst(e.target.value === "compare")}>
                  <option value="primary">{datasets.find((d) => d.id === selected)?.name}</option>
                  <option value="compare">{datasets.find((d) => d.id === compareId)?.name}</option>
                </select>
              </label>
            </div>
          )}
          <p className="muted small">The trusted source supplies the surviving record and wins field conflicts; other sources only fill empty fields.</p>
        </fieldset>
      )}
    </section>
  );
}

function PreviewStep({
  settings,
  setSettings,
  preview,
  stale,
  results,
  onPreview,
  onFull,
  rowCount,
}: {
  settings: Settings;
  setSettings: (s: Settings) => void;
  preview: Job | null;
  stale: boolean;
  results: MatchResults | null;
  onPreview: () => void;
  onFull: () => void;
  rowCount: number;
}) {
  const running = !!preview && isActive(preview.state);
  const guardReasons = Object.entries((results?.metrics.guard_reasons ?? {}) as Record<string, number>).sort((a, b) => b[1] - a[1]);
  return (
    <section className="stack">
      <h2>Preview matching</h2>
      <fieldset disabled={running} className="settings">
        <label className="field inline">
          <span>Strictness</span>
          <select value={settings.strictness} onChange={(e) => setSettings({ ...settings, strictness: e.target.value as Settings["strictness"] })}>
            <option value="conservative">Conservative (auto-match ≥ 0.95)</option>
            <option value="balanced">Balanced (auto-match ≥ 0.90)</option>
          </select>
        </label>
        <label className="field inline">
          <span>Preview size</span>
          <input type="number" min={100} value={settings.preview_size} onChange={(e) => setSettings({ ...settings, preview_size: Number(e.target.value) })} />
        </label>
        <details>
          <summary>Advanced settings</summary>
          <label className="field inline">
            <span>Max block size</span>
            <input type="number" min={2} max={5000} value={settings.max_block_size} onChange={(e) => setSettings({ ...settings, max_block_size: Number(e.target.value) })} />
          </label>
          <p className="muted small">Candidate groups larger than this are not compared (they would require too many comparisons) and are reported so you can add a stronger field.</p>
        </details>
      </fieldset>
      {(!preview || stale || preview.state !== "completed") && (
        <div className="row-actions">
          <button type="button" className="btn btn-primary" disabled={running} onClick={onPreview}>
            {stale ? "Run preview again" : "Run preview"}
          </button>
          <span className="muted small">Analyzes the first {formatCount(Math.min(settings.preview_size, rowCount))} rows. Nothing is changed.</span>
        </div>
      )}
      {stale && <p className="note note-warning"><span aria-hidden="true">! </span>Mapping or settings changed since the last preview. Run the preview again before starting a full job.</p>}
      {preview && preview.state !== "completed" && <JobProgress job={preview} />}
      {results && !stale && (
        <div className="stack">
          <h3>Preview complete: {formatCount(results.metrics.input_rows)} rows analyzed</h3>
          <div className="metrics">
            <Metric label="safe matches" value={results.decisions.match ?? 0} icon="✓" hint="Strong evidence, no contradiction" />
            <Metric label="need review" value={results.decisions.possible_match ?? 0} icon="?" hint="Similar but not certain; you decide" />
            <Metric label="kept separate" value={results.decisions.non_match ?? 0} icon="≠" hint="Low similarity or a hard contradiction" />
            <Metric label="unmatchable rows" value={results.metrics.unmatchable_rows} icon="∅" hint="No usable evidence fields" />
          </div>
          {guardReasons.length > 0 && (
            <div>
              <h4>Why some records were not merged</h4>
              <ul className="chips">
                {guardReasons.map(([reason, count]) => (
                  <li key={reason}>
                    <strong>{formatCount(count)}</strong> {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {results.metrics.oversized_block_count > 0 && (
            <p className="note note-warning">
              <span aria-hidden="true">! </span>
              {results.metrics.oversized_block_count} very common values were capped for safety and not compared; this may reduce recall. Map a stronger field or raise the cap in advanced settings.
            </p>
          )}
          {results.samples.length > 0 && (
            <div>
              <h4>Sample decisions</h4>
              <ul className="plain-list">
                {results.samples.map((s) => (
                  <li key={s.id} className="sample">
                    <span className={`badge ${s.decision === "match" ? "badge-completed" : "badge-paused"}`}>{s.decision === "match" ? "✓ High" : "? Review"}</span> {s.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="row-actions">
            <button type="button" className="btn" onClick={onPreview}>
              Re-run preview
            </button>
            <ConfirmButton
              label="Start full job"
              title="Start the full match job?"
              body={<p>All rows are analyzed. Raw rows remain unchanged; DataForge creates a derived canonical dataset and a review queue for uncertain pairs.</p>}
              confirmLabel="Start full job"
              onConfirm={onFull}
            />
          </div>
        </div>
      )}
    </section>
  );
}

function ReviewQueue({ jobId, onChanged, onDone, onBadMapping }: { jobId: string; onChanged: () => void; onDone: () => void; onBadMapping: () => void }) {
  const [choosing, setChoosing] = useState(false);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [flagColumn, setFlagColumn] = useState("");
  const [flagNote, setFlagNote] = useState("");
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [shortcuts, setShortcuts] = useState(true);
  const [lastAction, setLastAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<"score" | "model">("score");
  const queue = useService<{ total: number; items: ReviewItem[]; sensitive_columns: string[]; ranking_model: RankingModel | null }>("match.review_queue", { job_id: jobId, limit: 50, order });
  const toast = useToast();
  const keepRef = useRef<HTMLButtonElement>(null);
  const items = queue.data?.items ?? [];
  const item = items[Math.min(index, Math.max(items.length - 1, 0))];
  const sensitive = useMemo(() => new Set(queue.data?.sensitive_columns ?? []), [queue.data]);

  useEffect(() => {
    keepRef.current?.focus();
    setChosen({});
    setChoosing(false);
  }, [item?.decision_id]);

  const decide = async (action: "merge" | "keep_separate") => {
    if (!item || (action === "merge" && !item.can_merge)) return;
    try {
      const values = action === "merge" && choosing && Object.keys(chosen).length ? chosen : undefined;
      const result = await call<{ review_action_id: string }>("match.submit_review", { job_id: jobId, decision_id: item.decision_id, action, expected_version: item.review_version, values });
      setError(null);
      setLastAction(result.review_action_id);
      toast.show({ message: action === "merge" ? "Merged. Canonical records updated." : "Kept separate. This pair will not be linked.", action: { label: "Undo", run: () => void undo(result.review_action_id) } });
      await queue.reload();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await queue.reload();
    }
  };

  const undo = async (reviewActionId: string | null = lastAction) => {
    if (!reviewActionId) return;
    try {
      await call("match.undo_review", { job_id: jobId, review_action_id: reviewActionId });
      setLastAction(null);
      toast.show({ message: "Decision undone." });
      await queue.reload();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useKeyboardShortcuts(
    {
      j: () => setIndex((i) => Math.min(i + 1, items.length - 1)),
      k: () => setIndex((i) => Math.max(i - 1, 0)),
      m: () => void decide("merge"),
      s: () => void decide("keep_separate"),
      u: () => void undo(),
    },
    shortcuts,
  );

  if (queue.error) return <ErrorNote message={queue.error} />;
  if (!queue.data) return <p className="muted">Loading review queue…</p>;

  const columns = item ? [...new Set([...Object.keys(item.left.raw), ...Object.keys(item.right.raw)])] : [];

  return (
    <section className="stack" aria-labelledby="review-title">
      <div className="section-head">
        <h2 id="review-title">Review matches</h2>
        <span>{formatCount(queue.data.total)} remaining</span>
      </div>
      <div className="row-actions">
        <label className="toggle">
          <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} /> Reveal sensitive values
        </label>
        <label className="toggle">
          <input type="checkbox" checked={shortcuts} onChange={(e) => setShortcuts(e.target.checked)} /> Keyboard shortcuts (J/K next/previous, M merge, S keep separate, U undo)
        </label>
      </div>
      <RankingControls
        jobId={jobId}
        model={queue.data.ranking_model}
        order={order}
        onOrder={(next) => {
          setOrder(next);
          setIndex(0);
        }}
        onTrained={() => void queue.reload()}
      />
      <ErrorNote message={error} />
      {!item ? (
        <div className="panel">
          <p>
            <span aria-hidden="true">✓ </span>No review items remain. Uncertain pairs have been resolved.
          </p>
          <button type="button" className="btn btn-primary" onClick={onDone}>
            Continue to export
          </button>
        </div>
      ) : (
        <>
          <p className="muted">
            Item {Math.min(index, items.length - 1) + 1} of {items.length} loaded · score {item.score.toFixed(2)} · {item.reason}
          </p>
          <div className="table-wrap">
            <table className="data-table comparison">
              <caption className="sr-only">Record A compared with record B</caption>
              <thead>
                <tr>
                  <th scope="col">Field</th>
                  <th scope="col">
                    Record A · {item.left.source_name ? `${item.left.source_name}, ` : ""}row {item.left.row_number}
                  </th>
                  <th scope="col">
                    Record B · {item.right.source_name ? `${item.right.source_name}, ` : ""}row {item.right.row_number}
                  </th>
                </tr>
              </thead>
              <tbody>
                {columns.map((column) => {
                  const a = String(item.left.raw[column] ?? "");
                  const b = String(item.right.raw[column] ?? "");
                  const state = !a || !b ? "missing" : a.trim().toLowerCase() === b.trim().toLowerCase() ? "same" : "different";
                  const isSensitive = sensitive.has(column);
                  return (
                    <tr key={column} className={`cmp-${state}`}>
                      <th scope="row">
                        {column}
                        <span className="sr-only"> ({state})</span>
                      </th>
                      {[{ row: item.left, value: a }, { row: item.right, value: b }].map(({ row, value }) => (
                        <td key={row.id}>
                          {choosing && state === "different" ? (
                            <label className="toggle">
                              <input
                                type="radio"
                                name={`choose-${column}`}
                                checked={chosen[column] === row.id}
                                onChange={() => setChosen({ ...chosen, [column]: row.id })}
                                aria-label={`Use record ${row === item.left ? "A" : "B"} for ${column}`}
                              />
                              {displayValue(value, isSensitive, revealed)}
                            </label>
                          ) : (
                            displayValue(value, isSensitive, revealed) || <span className="muted">missing</span>
                          )}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <h3>Evidence</h3>
          <ul className="plain-list evidence">
            {item.evidence.map((e, i) => (
              <li key={i} className={`evidence-${e.strength}`}>
                <span aria-hidden="true">{e.strength === "strong" ? "✓" : e.strength === "guard" ? "✕" : e.result === "different" ? "≠" : "•"}</span>{" "}
                <strong>{e.strength === "guard" ? "Contradiction" : e.strength === "strong" ? "Strong evidence" : "Supporting"}</strong> — {e.explanation}
              </li>
            ))}
          </ul>
          <div className="decision-bar">
            {!item.can_merge && <strong className="note-error">✕ Cannot auto-merge</strong>}
            <button ref={keepRef} type="button" className="btn" onClick={() => void decide("keep_separate")}>
              Keep separate <kbd>S</kbd>
            </button>
            <button type="button" className={item.can_merge ? "btn btn-primary" : "btn"} disabled={!item.can_merge} onClick={() => void decide("merge")}>
              Merge <kbd>M</kbd>
            </button>
            {item.can_merge && (
              <button type="button" className="btn" aria-pressed={choosing} onClick={() => setChoosing(!choosing)} title="Pick which record supplies each differing field, then merge">
                {choosing ? `Choosing values (${Object.keys(chosen).length})` : "Choose values"}
              </button>
            )}
            <button type="button" className="btn" disabled={index <= 0} onClick={() => setIndex(index - 1)}>
              Previous <kbd>K</kbd>
            </button>
            <button type="button" className="btn" disabled={index >= items.length - 1} onClick={() => setIndex(index + 1)}>
              Next <kbd>J</kbd>
            </button>
          </div>
          <details className="panel">
            <summary>More actions</summary>
            <p className="muted small">If a field role is creating poor candidates (for example two different address columns mapped as one), report it and fix the mapping. The pair stays in the queue.</p>
            <div className="split tight-3">
              <label className="field">
                <span>Field</span>
                <select value={flagColumn} onChange={(e) => setFlagColumn(e.target.value)}>
                  <option value="">Choose a field…</option>
                  {columns.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>What is wrong</span>
                <input value={flagNote} onChange={(e) => setFlagNote(e.target.value)} placeholder="e.g. mailing and property addresses pooled" />
              </label>
              <button
                type="button"
                className="btn"
                disabled={!flagColumn}
                onClick={async () => {
                  try {
                    await call("match.flag_mapping", { job_id: jobId, column: flagColumn, note: flagNote, decision_id: item.decision_id });
                    setFlagColumn("");
                    setFlagNote("");
                    toast.show({ message: "Mapping issue recorded.", action: { label: "Fix mapping", run: onBadMapping } });
                  } catch (err) {
                    setError(err instanceof Error ? err.message : String(err));
                  }
                }}
              >
                Mark bad mapping
              </button>
            </div>
          </details>
        </>
      )}
      {toast.node}
    </section>
  );
}

type RankingModel = { id: string; model_version: string; label_count: number; created_at: string; evaluation: { accuracy: number; precision: number | null; recall: number | null; holdout_size: number } };

function RankingControls({ jobId, model, order, onOrder, onTrained }: { jobId: string; model: RankingModel | null; order: "score" | "model"; onOrder: (order: "score" | "model") => void; onTrained: () => void }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <details className="panel">
      <summary>
        Queue order: <strong>{order === "model" ? "learned from your decisions" : "deterministic score"}</strong>
      </summary>
      <p className="muted small">
        A ranking model trained only on your merge and keep-separate decisions can put the most likely merges first. It changes the order only: every item still needs your decision and nothing is merged automatically.
      </p>
      {model ? (
        <p className="small">
          Model {model.model_version} · {model.label_count} labels · holdout of {model.evaluation.holdout_size}: accuracy {model.evaluation.accuracy}
          {model.evaluation.precision !== null && `, precision ${model.evaluation.precision}`}
          {model.evaluation.recall !== null && `, recall ${model.evaluation.recall}`}
        </p>
      ) : (
        <p className="small muted">No model yet. Train one after reviewing at least 20 pairs with both outcomes.</p>
      )}
      <div className="segmented" role="tablist" aria-label="Queue order">
        <button type="button" role="tab" aria-selected={order === "score"} onClick={() => onOrder("score")}>
          Score
        </button>
        <button type="button" role="tab" aria-selected={order === "model"} disabled={!model} onClick={() => onOrder("model")}>
          Learned ranking
        </button>
      </div>
      <button
        type="button"
        className="btn btn-small"
        onClick={async () => {
          try {
            await call("match.train_ranking", { job_id: jobId });
            setError(null);
            onTrained();
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
      >
        {model ? "Retrain from decisions" : "Train from decisions"}
      </button>
      <ErrorNote message={error} />
    </details>
  );
}

type ClusterItem = {
  cluster_id: string;
  member_count: number;
  confidence: number;
  status: string;
  survivor_row_id: string;
  lock_action_id: string | null;
  members: { id: string; row_number: number; raw: Record<string, unknown>; source?: string }[];
  canonical_values: Record<string, unknown>;
  field_provenance: Record<string, { row_id: string; rule: string }>;
  conflicts: Record<string, string[]>;
};

function GroupsPanel({ jobId, onChanged }: { jobId: string; onChanged: () => void }) {
  const groups = useService<{ total: number; items: ClusterItem[]; sensitive_columns: string[] }>("match.clusters", { job_id: jobId, limit: 25 });
  const [selected, setSelected] = useState<Record<string, Set<string>>>({});
  const [revealed, setRevealed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const run = async (command: string, payload: Record<string, unknown>, message: string) => {
    try {
      const result = await call<{ cluster_action_id?: string; override_id?: string }>(command, { job_id: jobId, ...payload });
      setError(null);
      setSelected({});
      toast.show({
        message,
        action: result.cluster_action_id
          ? { label: "Undo", run: () => void run("match.undo_cluster_action", { cluster_action_id: result.cluster_action_id }, "Group action undone.") }
          : result.override_id
            ? { label: "Undo", run: () => void run("match.undo_canonical_value", { override_id: result.override_id }, "Value choice undone.") }
            : undefined,
      });
      await groups.reload();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await groups.reload();
    }
  };

  if (!groups.data) return <ErrorNote message={groups.error} />;
  const sensitive = new Set(groups.data.sensitive_columns);

  return (
    <details className="panel">
      <summary>
        Merged groups <span className="count-badge">{formatCount(groups.data.total)}</span>
      </summary>
      <p className="muted small">Split a group to separate members that should not be together, or lock a correct group so future runs keep it exactly as it is. Both apply to this dataset in future runs and can be undone.</p>
      <label className="toggle">
        <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} /> Reveal sensitive values
      </label>
      <ErrorNote message={error} />
      {groups.data.items.length === 0 && <p className="muted">No records were merged.</p>}
      {groups.data.items.map((group) => {
        const picked = selected[group.cluster_id] ?? new Set<string>();
        const columns = Object.keys(group.members[0]?.raw ?? {}).slice(0, 6);
        const locked = !!group.lock_action_id;
        return (
          <div key={group.cluster_id} className="group-card">
            <div className="section-head">
              <strong>
                {group.member_count} records · confidence {group.confidence.toFixed(2)} {locked && <span className="role-badge">Locked</span>}
              </strong>
              <div className="row-actions">
                {locked ? (
                  <button type="button" className="btn btn-small" onClick={() => void run("match.undo_cluster_action", { cluster_action_id: group.lock_action_id }, "Group unlocked.")}>
                    Unlock
                  </button>
                ) : (
                  <>
                    <button type="button" className="btn btn-small" onClick={() => void run("match.lock_cluster", { cluster_id: group.cluster_id }, "Group locked for future runs.")}>
                      Lock group
                    </button>
                    <button
                      type="button"
                      className="btn btn-small btn-danger"
                      disabled={picked.size === 0 || picked.size === group.member_count}
                      onClick={() => void run("match.split_cluster", { cluster_id: group.cluster_id, row_ids: [...picked] }, `Separated ${picked.size} record(s) from the group.`)}
                    >
                      Split selected ({picked.size})
                    </button>
                  </>
                )}
              </div>
            </div>
            {Object.keys(group.conflicts).length > 0 && (
              <div className="conflicts">
                <span className="small muted">Conflicting values — choose the canonical value:</span>
                {Object.entries(group.conflicts).map(([column, rowIds]) => (
                  <label key={column} className="field inline small">
                    <span>{column}</span>
                    <select
                      value={group.field_provenance[column]?.row_id ?? ""}
                      onChange={(e) => void run("match.set_canonical_value", { cluster_id: group.cluster_id, column, row_id: e.target.value }, `Canonical ${column} updated.`)}
                    >
                      {rowIds.map((rowId) => {
                        const member = group.members.find((m) => m.id === rowId);
                        return (
                          <option key={rowId} value={rowId}>
                            {displayValue(member?.raw[column], sensitive.has(column), revealed)} (row {member?.row_number})
                          </option>
                        );
                      })}
                    </select>
                    {group.field_provenance[column]?.rule === "reviewer_choice" && <span className="tag">chosen</span>}
                  </label>
                ))}
              </div>
            )}
            <div className="table-wrap">
              <table className="data-table compact">
                <thead>
                  <tr>
                    <th scope="col">
                      <span className="sr-only">Select</span>
                    </th>
                    <th scope="col">Row</th>
                    {columns.map((c) => (
                      <th scope="col" key={c}>
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {group.members.map((member) => (
                    <tr key={member.id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select row ${member.row_number}`}
                          disabled={locked}
                          checked={picked.has(member.id)}
                          onChange={(e) => {
                            const next = new Set(picked);
                            if (e.target.checked) next.add(member.id);
                            else next.delete(member.id);
                            setSelected({ ...selected, [group.cluster_id]: next });
                          }}
                        />
                      </td>
                      <td>
                        {member.row_number}
                        {member.id === group.survivor_row_id && <span className="tag">survivor</span>}
                      </td>
                      {columns.map((c) => (
                        <td key={c}>{displayValue(member.raw[c], sensitive.has(c), revealed)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      {toast.node}
    </details>
  );
}

function ExportStep({ results, onExported }: { results: MatchResults; onExported: () => void }) {
  const [includeProvenance, setIncludeProvenance] = useState(true);
  const [allowUnresolved, setAllowUnresolved] = useState(false);
  const [exported, setExported] = useState<{ directory: string; is_final: boolean; files: { kind: string; path: string; row_count: number }[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const unresolved = results.pending_review;

  return (
    <section className="stack">
      <h2>Results and export</h2>
      <GroupsPanel jobId={results.job_id} onChanged={onExported} />
      <div className="metrics">
        <Metric label="Input rows" value={results.metrics.input_rows} />
        <Metric label="Canonical records" value={results.canonical_records} />
        <Metric label="Rows suppressed in clean export" value={results.rows_suppressed} />
        <Metric label="Unresolved review items" value={unresolved} icon={unresolved ? "!" : "✓"} />
      </div>
      <p className="muted small">
        Policy {results.policy_version} · mapping {results.mapping_version_id.slice(0, 8)} · job {results.job_id.slice(0, 8)}
        {results.compare_dataset_id ? " · compared with a second dataset" : ""}
      </p>
      {results.mapping_flags.length > 0 && (
        <p className="note note-warning">
          <span aria-hidden="true">! </span>
          {results.mapping_flags.length} mapping issue(s) reported during review: {results.mapping_flags.map((f) => `${f.column_name} (${f.note})`).join("; ")}. Save a new mapping version and re-run to address them.
        </p>
      )}
      <details>
        <summary>Run metrics</summary>
        <dl className="facts small">
          <dt>Throughput</dt>
          <dd>
            {formatCount(results.metrics.rows_per_second)} rows/s · {results.metrics.total_seconds}s total
          </dd>
          <dt>Stages</dt>
          <dd>
            {Object.entries((results.metrics.stage_seconds ?? {}) as Record<string, number>)
              .map(([stage, seconds]) => `${stage.replace(/_/g, " ")} ${seconds}s`)
              .join(" · ")}
          </dd>
          <dt>Group sizes</dt>
          <dd>
            {Object.entries((results.metrics.cluster_size_distribution ?? {}) as Record<string, number>)
              .map(([size, count]) => `${size}: ${formatCount(count)}`)
              .join(" · ")}
          </dd>
          <dt>Decisions by scope</dt>
          <dd>
            {Object.entries((results.metrics.decisions_by_scope ?? {}) as Record<string, Record<string, number>>)
              .map(([scope, counts]) => `${scope.startsWith("within") ? "within dataset" : "across datasets"}: ${Object.entries(counts).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ")}`)
              .join(" · ")}
          </dd>
          <dt>Review turnaround</dt>
          <dd>{results.review_turnaround.median_seconds === null ? "no decisions yet" : `median ${results.review_turnaround.median_seconds}s over ${results.review_turnaround.decisions} decisions`}</dd>
        </dl>
      </details>
      <label className="toggle block">
        <input type="checkbox" checked={includeProvenance} onChange={(e) => setIncludeProvenance(e.target.checked)} /> Include provenance
      </label>
      {!includeProvenance && <p className="note note-warning">Traceability fields (field provenance, survivor row IDs) will be omitted from the CSV files.</p>}
      {unresolved > 0 && (
        <label className="toggle block">
          <input type="checkbox" checked={allowUnresolved} onChange={(e) => setAllowUnresolved(e.target.checked)} /> Export with {unresolved} unresolved records (files are marked as not final)
        </label>
      )}
      <button
        type="button"
        className="btn btn-primary"
        disabled={unresolved > 0 && !allowUnresolved}
        onClick={async () => {
          try {
            setExported(await call("export.create", { job_id: results.job_id, include_provenance: includeProvenance, allow_unresolved: allowUnresolved }));
            setError(null);
            onExported();
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
      >
        {unresolved > 0 ? "Export with unresolved records" : "Export canonical, clean, original, and audit files"}
      </button>
      <ErrorNote message={error} />
      {exported && (
        <div className="panel" role="status">
          <p>
            <span aria-hidden="true">{exported.is_final ? "✓" : "!"} </span>
            {exported.is_final ? "Final export created" : "Export created and marked as not final"} in <code>{exported.directory}</code>
          </p>
          <ul>
            {exported.files.map((f) => (
              <li key={f.kind}>
                {f.kind}: {formatCount(f.row_count)} {f.kind === "audit" ? "decisions" : "rows"}
              </li>
            ))}
          </ul>
        </div>
      )}
      {results.exports.length > 0 && (
        <details>
          <summary>Previous exports ({results.exports.length} files)</summary>
          <ul className="small">
            {results.exports.map((e) => (
              <li key={e.id}>
                {formatTime(e.created_at)} · {e.kind} · {e.is_final ? "final" : "not final"} · <code>{e.path}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
