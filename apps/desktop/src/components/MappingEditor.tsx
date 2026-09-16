import { useEffect, useMemo, useState } from "react";
import { call } from "../lib/ipc.ts";
import { displayValue, mappingProblems, MATCH_ROLES } from "../lib/format.ts";
import { useService } from "../lib/hooks.ts";
import type { ColumnProfile } from "../lib/types.ts";
import { ErrorNote } from "./ui.tsx";

type Profile = { columns: ColumnProfile[]; sensitive_roles: string[] };
type SavedMapping = { id: string; version: number; mapping: Record<string, string>; entity_type: string | null; export_exclude: string[] } | null;

export const ENTITY_TYPES = ["person", "business", "property", "product", "job", "article", "media", "software", "custom"];

const CONFIDENCE_TEXT: Record<ColumnProfile["confidence"], string> = {
  proposed: "Header matches a known role",
  ambiguous: "Needs review: header partly matches",
  unmapped: "No suggestion",
};

export function MappingEditor({ datasetId, onSaved }: { datasetId: string; onSaved?: (version: number) => void }) {
  const profile = useService<Profile>("dataset.profile", { dataset_id: datasetId });
  const saved = useService<SavedMapping>("dataset.mapping", { dataset_id: datasetId });
  const flags = useService<{ id: string; column_name: string; note: string }[]>("dataset.mapping_flags", { dataset_id: datasetId });
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [entityType, setEntityType] = useState("person");
  const [confirmedAmbiguous, setConfirmedAmbiguous] = useState<Set<string>>(new Set());
  const [revealed, setRevealed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const suggestions = useMemo(() => {
    const result: Record<string, string> = {};
    for (const column of profile.data?.columns ?? []) result[column.name] = column.proposed_role ?? "other";
    return result;
  }, [profile.data]);

  useEffect(() => {
    if (!profile.data || !saved.loaded) return;
    if (saved.data) {
      setMapping({ ...suggestions, ...saved.data.mapping });
      setEntityType(saved.data.entity_type ?? "custom");
      setExcluded(new Set(saved.data.export_exclude ?? []));
      setConfirmedAmbiguous(new Set(profile.data.columns.map((c) => c.name)));
    } else {
      setMapping(suggestions);
    }
  }, [profile.data, saved.data, saved.loaded, suggestions]);

  if (profile.error) return <ErrorNote message={profile.error} />;
  if (!profile.data) return <p className="muted">Profiling columns…</p>;

  const sensitiveRoles = new Set(profile.data.sensitive_roles);
  // Edits are layered over suggestions so an early edit can never drop the other columns' roles.
  const current = { ...suggestions, ...mapping };
  const problems = mappingProblems(current);
  const unconfirmed = profile.data.columns.filter((c) => c.confidence === "ambiguous" && !confirmedAmbiguous.has(c.name));
  const dirty =
    !saved.data ||
    JSON.stringify(saved.data.mapping) !== JSON.stringify(current) ||
    saved.data.entity_type !== entityType ||
    JSON.stringify([...(saved.data.export_exclude ?? [])].sort()) !== JSON.stringify([...excluded].sort());

  const save = async () => {
    try {
      const result = await call<{ version: number }>("dataset.confirm_mapping", { dataset_id: datasetId, mapping: current, entity_type: entityType, export_exclude: [...excluded] });
      setError(null);
      setStatus(`Saved mapping version ${result.version}. Earlier versions are kept unchanged.`);
      await Promise.all([saved.reload(), flags.reload()]);
      onSaved?.(result.version);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section aria-labelledby="mapping-title">
      <div className="section-head">
        <h3 id="mapping-title">Confirm the fields DataForge will compare</h3>
        <label className="toggle">
          <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} /> Reveal sample values
        </label>
      </div>
      <p className="muted">
        {saved.data ? `Current mapping: version ${saved.data.version}.` : "No mapping saved yet."} “Other” keeps a column out of matching evidence. Phone, email, and identifier roles may be used by several columns;
        other roles are compared like-for-like and may be used once.
      </p>
      {!!flags.data?.length && (
        <div className="note note-warning" role="status">
          <strong>
            <span aria-hidden="true">! </span>Reported during review:
          </strong>
          <ul>
            {flags.data.map((f) => (
              <li key={f.id}>
                <code>{f.column_name}</code> — {f.note}
              </li>
            ))}
          </ul>
          <span className="small">Saving a new mapping version resolves these reports.</span>
        </div>
      )}
      <label className="field inline">
        <span>Entity type</span>
        <select value={entityType} onChange={(e) => setEntityType(e.target.value)}>
          {ENTITY_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </label>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Source column</th>
              <th scope="col">Samples</th>
              <th scope="col">Empty</th>
              <th scope="col">Role</th>
              <th scope="col">Confidence</th>
              <th scope="col">Export</th>
            </tr>
          </thead>
          <tbody>
            {profile.data.columns.map((column) => {
              const role = current[column.name] ?? "other";
              const sensitive = sensitiveRoles.has(role) || sensitiveRoles.has(column.proposed_role ?? "") || column.candidates.some((c) => sensitiveRoles.has(c));
              const needsReview = column.confidence === "ambiguous" && !confirmedAmbiguous.has(column.name);
              return (
                <tr key={column.name} className={needsReview ? "row-warning" : undefined}>
                  <th scope="row">
                    {column.name}
                    {sensitive && <span className="tag" title="Sensitive: hidden in previews until revealed">sensitive</span>}
                  </th>
                  <td className="samples">{column.sample_values.slice(0, 3).map((v) => displayValue(v, sensitive, revealed)).join(" · ") || <span className="muted">—</span>}</td>
                  <td>{Math.round(column.null_rate * 100)}%</td>
                  <td>
                    <select
                      aria-label={`Role for ${column.name}`}
                      value={role}
                      onChange={(e) => {
                        setMapping((previous) => ({ ...previous, [column.name]: e.target.value }));
                        setConfirmedAmbiguous(new Set(confirmedAmbiguous).add(column.name));
                      }}
                    >
                      {[...new Set([...column.candidates, ...MATCH_ROLES])].map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    {needsReview ? (
                      <button type="button" className="btn btn-small" onClick={() => setConfirmedAmbiguous(new Set(confirmedAmbiguous).add(column.name))}>
                        <span aria-hidden="true">! </span>Confirm “{role}”
                      </button>
                    ) : (
                      <span className="small">{CONFIDENCE_TEXT[column.confidence]}{column.candidates.length > 1 ? ` (${column.candidates.join(" / ")})` : ""}</span>
                    )}
                  </td>
                  <td>
                    <label className="toggle small" title="Still used as matching evidence; left out of exported files">
                      <input
                        type="checkbox"
                        checked={excluded.has(column.name)}
                        onChange={(e) => {
                          const next = new Set(excluded);
                          if (e.target.checked) next.add(column.name);
                          else next.delete(column.name);
                          setExcluded(next);
                        }}
                      />
                      Exclude
                    </label>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {problems.map((p) => (
        <p key={p} className="note note-warning">
          <span aria-hidden="true">! </span>
          {p}
        </p>
      ))}
      {unconfirmed.length > 0 && (
        <p className="note note-warning">
          <span aria-hidden="true">! </span>
          {unconfirmed.length} column(s) partly match a role and need your confirmation: {unconfirmed.map((c) => c.name).join(", ")}.
        </p>
      )}
      <div className="row-actions">
        <button type="button" className="btn" onClick={() => setMapping(suggestions)}>
          Restore suggestions
        </button>
        <button type="button" className="btn btn-primary" disabled={problems.length > 0 || unconfirmed.length > 0 || !dirty} onClick={() => void save()}>
          {saved.data ? "Save as new mapping version" : "Save mapping"}
        </button>
      </div>
      <ErrorNote message={error} />
      {status && <p className="note" role="status">{status}</p>}
    </section>
  );
}
