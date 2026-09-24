import { Fragment, useState } from "react";
import { DETAIL_LEVELS, GROUP_LABELS, groupRecord, shortKey, type DetailLevel } from "../lib/details.ts";

const PREVIEW_LENGTH = 400;

function LongValue({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  if (text.length <= PREVIEW_LENGTH) return <>{text}</>;
  return (
    <>
      {expanded ? text : `${text.slice(0, PREVIEW_LENGTH)}…`}{" "}
      <button type="button" className="btn-link small" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {expanded ? "Show less" : `Show all ${text.length.toLocaleString("en-US")} characters`}
      </button>
    </>
  );
}

/** Every value of one record, grouped into the preset's fields, value details, element, detail page, page, and
 *  provenance. `display` lets callers mask sensitive values until the user reveals them. */
export function RecordInspector({
  record,
  title = "Record details",
  onClose,
  display = (_key, value) => String(value),
}: {
  record: Record<string, unknown>;
  title?: string;
  onClose?: () => void;
  display?: (key: string, value: unknown) => string;
}) {
  const groups = groupRecord(record);
  return (
    <section className="record-inspector" aria-label={title}>
      <div className="section-head">
        <h3>{title}</h3>
        {onClose && (
          <button type="button" className="btn btn-small" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      {groups.map(({ group, entries }) => (
        <details key={group} className="inspector-group" open={group !== "provenance" && group !== "page"}>
          <summary>
            {GROUP_LABELS[group]} <span className="muted">({entries.length})</span>
          </summary>
          <dl className="facts inspector-facts">
            {entries.map(([key, value]) => (
              <Fragment key={key}>
                <dt title={key}>{shortKey(key, group)}</dt>
                <dd>
                  <LongValue text={display(key, value)} />
                </dd>
              </Fragment>
            ))}
          </dl>
        </details>
      ))}
    </section>
  );
}

export function DetailLevelField({ value, onChange, id = "detail-level" }: { value: DetailLevel; onChange: (level: DetailLevel) => void; id?: string }) {
  const hint = DETAIL_LEVELS.find((level) => level.id === value)?.hint;
  return (
    <div className="field">
      <label htmlFor={id}>Record detail</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value as DetailLevel)} aria-describedby={`${id}-hint`}>
        {DETAIL_LEVELS.map((level) => (
          <option key={level.id} value={level.id}>
            {level.label}
          </option>
        ))}
      </select>
      <span id={`${id}-hint`} className="small muted field-hint">
        {hint}
      </span>
    </div>
  );
}
