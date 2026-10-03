import { useEffect, useState } from "react";
import type { Navigate } from "../../app/App.tsx";
import { call, isTauri, pickFile, pickSaveFile } from "../../lib/ipc.ts";
import { openPath, revealInFolder } from "../../lib/desktop.ts";
import { useJob, useService } from "../../lib/hooks.ts";
import { displayValue, formatCount, formatTime, isActive } from "../../lib/format.ts";
import type { Dataset, Row } from "../../lib/types.ts";
import { ConfirmButton, ErrorNote, JobProgress, PathInput } from "../../components/ui.tsx";
import { MappingEditor } from "../../components/MappingEditor.tsx";

const IMPORT_FILTERS = [{ name: "Data files", extensions: ["csv", "json", "jsonl", "ndjson", "xlsx", "xml", "parquet", "docx", "zip", "gz"] }];

/** Exports a dataset as a formatted workbook: Summary with live formulas, a filterable table, Details, and Photos. */
export function WorkbookExport({ datasetId, datasetName }: { datasetId: string; datasetName: string }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob(jobId);
  const running = !!job && isActive(job.state);
  const saved = job?.state === "completed" ? String(job.result?.path ?? "") : "";
  const start = async () => {
    setError(null);
    try {
      const fileName = `${datasetName.replace(/[\\/:*?"<>|]+/g, " ").trim() || "dataset"}.xlsx`;
      const path = await pickSaveFile(fileName, [{ name: "Excel workbook", extensions: ["xlsx"] }]);
      if (!path) return;
      const started = await call<{ job_id: string }>("dataset.export_workbook", { dataset_id: datasetId, path: path.toLowerCase().endsWith(".xlsx") ? path : `${path}.xlsx` });
      setJobId(started.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <>
      <button type="button" className="btn" disabled={!isTauri() || running} onClick={() => void start()} title={isTauri() ? undefined : "Exporting needs the desktop app"}>
        {running ? "Exporting…" : "Export to Excel"}
      </button>
      {(saved || error || job?.state === "failed") && (
        <div className="note small" role="status">
          {saved ? (
            <>
              Saved {formatCount(Number(job?.result?.rows ?? 0))} rows to {saved}.{" "}
              <button type="button" className="btn btn-small" onClick={() => void openPath(saved)}>Open</button>{" "}
              <button type="button" className="btn btn-small" onClick={() => void revealInFolder(saved)}>Show in folder</button>
            </>
          ) : <ErrorNote message={error ?? job?.error ?? "The workbook could not be written"} />}
        </div>
      )}
    </>
  );
}

export function ImportForm({ onImported }: { onImported: (datasetId: string) => void }) {
  const [path, setPath] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob(jobId, (done) => {
    if (done.state === "completed" && done.result?.dataset_id) onImported(String(done.result.dataset_id));
  });

  return (
    <div>
      <PathInput label="Data file" value={path} onChange={setPath} placeholder="C:\data\listings.csv" onBrowse={isTauri() ? () => pickFile(IMPORT_FILTERS) : undefined} />
      <p className="muted small">CSV, JSON/JSONL, XLSX, XML, Parquet, DOCX, ZIP, and GZIP are supported.</p>
      <p className="muted small">Files are processed locally. The original file is copied into the project unchanged and its hash is recorded.</p>
      <button
        type="button"
        className="btn btn-primary"
        disabled={!path || (!!job && isActive(job.state))}
        onClick={async () => {
          try {
            setJobId((await call<{ job_id: string }>("dataset.import", { path })).job_id);
            setError(null);
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
      >
        Import
      </button>
      <ErrorNote message={error} />
      {jobId && job?.state !== "completed" && <JobProgress job={job} />}
    </div>
  );
}

export function Datasets({ navigate, initialDatasetId }: { navigate: Navigate; initialDatasetId?: string }) {
  const datasets = useService<Dataset[]>("dataset.list");
  const [selected, setSelected] = useState<string | null>(initialDatasetId ?? null);
  useEffect(() => {
    if (initialDatasetId) setSelected(initialDatasetId);
  }, [initialDatasetId]);
  const dataset = datasets.data?.find((d) => d.id === selected) ?? null;

  return (
    <div className="stack">
      <section className="panel">
        <h2>Import a file</h2>
        <ImportForm
          onImported={(id) => {
            void datasets.reload();
            setSelected(id);
          }}
        />
      </section>

      <section className="panel">
        <h2>All datasets</h2>
        <ErrorNote message={datasets.error} />
        {datasets.data?.length === 0 && <p className="muted">No datasets yet. Import a file or complete a scrape first.</p>}
        {!!datasets.data?.length && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Source</th>
                  <th scope="col">Rows</th>
                  <th scope="col">Mapping</th>
                  <th scope="col">Imported</th>
                </tr>
              </thead>
              <tbody>
                {datasets.data.map((d) => (
                  <tr key={d.id} aria-selected={d.id === selected} className={d.id === selected ? "row-selected" : undefined}>
                    <th scope="row">
                      <button type="button" className="link" onClick={() => setSelected(d.id)}>
                        {d.name}
                      </button>
                    </th>
                    <td>
                      {d.kind === "scrape" ? "Scrape" : "File"} · {d.source_filename}
                    </td>
                    <td>{formatCount(d.row_count)}</td>
                    <td>{d.mapping_version ? `v${d.mapping_version}` : "—"}</td>
                    <td>{formatTime(d.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {dataset && (
        <section className="panel" key={dataset.id}>
          <div className="section-head">
            <h2>{dataset.name}</h2>
            <div className="row-actions">
              <button type="button" className="btn btn-primary" onClick={() => navigate("match", { datasetId: dataset.id })}>
                Match & deduplicate
              </button>
              <WorkbookExport datasetId={dataset.id} datasetName={dataset.name} />
              <ConfirmButton
                label="Delete dataset"
                danger
                title={`Delete “${dataset.name}”?`}
                body={
                  <p>
                    This removes its {formatCount(dataset.row_count)} imported rows, mapping versions, and review constraints from the project. Existing export files on disk are
                    not deleted. This cannot be undone.
                  </p>
                }
                confirmLabel="Delete dataset"
                onConfirm={async () => {
                  await call("dataset.delete", { dataset_id: dataset.id });
                  setSelected(null);
                  await datasets.reload();
                }}
              />
            </div>
          </div>
          <p className="muted small">
            SHA-256 {dataset.source_artifact_hash.slice(0, 16)}… · {dataset.column_count} columns · immutable source rows
          </p>
          <MappingEditor datasetId={dataset.id} onSaved={() => void datasets.reload()} />
          <RowSample datasetId={dataset.id} />
        </section>
      )}
    </div>
  );
}

function RowSample({ datasetId }: { datasetId: string }) {
  const [offset, setOffset] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const rows = useService<Row[]>("dataset.rows", { dataset_id: datasetId, offset, limit: 25 });
  const mapping = useService<{ mapping: Record<string, string> } | null>("dataset.mapping", { dataset_id: datasetId });
  const sensitiveRoles = new Set(["phone", "email", "address", "mailing_address", "building_name", "house_number", "street", "unit", "po_box", "neighborhood", "district", "city", "county", "region", "country", "country_code", "postal_code", "latitude", "longitude", "name", "first_name", "last_name"]);
  const columns = rows.data?.[0] ? Object.keys(rows.data[0].raw) : [];
  // Until a mapping exists every column is treated as potentially sensitive.
  const isSensitive = (column: string) => !mapping.data || sensitiveRoles.has(mapping.data.mapping[column] ?? "");

  return (
    <section aria-labelledby="rows-title">
      <div className="section-head">
        <h3 id="rows-title">Source rows</h3>
        <label className="toggle">
          <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} /> Reveal sensitive values
        </label>
      </div>
      <div className="table-wrap">
        <table className="data-table compact">
          <thead>
            <tr>
              <th scope="col">Row</th>
              {columns.map((c) => (
                <th scope="col" key={c}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.data?.map((row) => (
              <tr key={row.id}>
                <td>{row.row_number}</td>
                {columns.map((c) => (
                  <td key={c}>{displayValue(row.raw[c], isSensitive(c), revealed)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row-actions">
        <button type="button" className="btn btn-small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>
          Previous
        </button>
        <button type="button" className="btn btn-small" disabled={(rows.data?.length ?? 0) < 25} onClick={() => setOffset(offset + 25)}>
          Next
        </button>
      </div>
    </section>
  );
}
