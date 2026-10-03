import { useEffect, useState, type ReactNode } from "react";
import type { Navigate } from "../../app/App.tsx";
import { call, isTauri, pickDirectory } from "../../lib/ipc.ts";
import { useJob, useService } from "../../lib/hooks.ts";
import { formatCount, isActive } from "../../lib/format.ts";
import { ErrorNote, JobProgress, PathInput } from "../../components/ui.tsx";
import { CustomSelect } from "../../components/CustomSelect.tsx";
import { PURPOSES } from "../scraping/sources.ts";

const OWNER_TYPES = ["individual", "company", "trust", "estate", "government"] as const;
const thisMonth = () => new Date().toISOString().slice(0, 7);

/** Starts one public-records job and shows its progress and the dataset it created. */
function RecordJob({ label, disabled, start, navigate }: { label: string; disabled: boolean; start: () => Promise<{ job_id: string }>; navigate: Navigate }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob(jobId);
  const running = !!job && isActive(job.state);
  const result = job?.state === "completed" ? (job.result as { dataset_id?: string | null; rows?: number; message?: string } | null) : null;
  return (
    <div className="records-run">
      <button type="button" className="btn btn-primary" disabled={disabled || running} onClick={async () => {
        setError(null);
        try {
          setJobId((await start()).job_id);
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }}>
        {running ? "Collecting…" : label}
      </button>
      <ErrorNote message={error ?? (job?.state === "failed" ? job.error ?? "The collection failed" : null)} />
      {jobId && running && <JobProgress job={job} />}
      {result && (
        <p className="note small" role="status">
          {result.dataset_id ? (
            <>
              Saved {formatCount(Number(result.rows ?? 0))} rows as a dataset.{" "}
              <button type="button" className="btn btn-small" onClick={() => navigate("datasets", { datasetId: String(result.dataset_id) })}>Open dataset</button>
            </>
          ) : result.message ?? "Nothing was found."}
        </p>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

/**
 * Public property records for Harris County, TX (Houston): the next delinquent-tax sale, trustee foreclosure notices,
 * and the appraisal roll from the HCAD bulk files. Each collection becomes a dataset (export, matching, dedup).
 */
export function PublicRecords({ navigate }: { navigate: Navigate }) {
  const settings = useService<{ default_purpose: string }>("settings.get");
  const [purpose, setPurpose] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  useEffect(() => {
    if (!purpose && settings.data) setPurpose(settings.data.default_purpose);
  }, [settings.data, purpose]);
  const ready = acknowledged && !!purpose;
  const base = { policy_acknowledgement: acknowledged, purpose };

  const [taxHcad, setTaxHcad] = useState("");
  const [fromMonth, setFromMonth] = useState(thisMonth());
  const [toMonth, setToMonth] = useState(thisMonth());
  const [hcadDir, setHcadDir] = useState("");
  const [zips, setZips] = useState("");
  const [classes, setClasses] = useState("A1");
  const [absentee, setAbsentee] = useState(false);
  const [outOfState, setOutOfState] = useState(false);
  const [noHomestead, setNoHomestead] = useState(false);
  const [ownerTypes, setOwnerTypes] = useState<string[]>([]);
  const [ownedYears, setOwnedYears] = useState("");
  const [builtBefore, setBuiltBefore] = useState("");
  const [minValue, setMinValue] = useState("");
  const [maxValue, setMaxValue] = useState("");
  const browse = isTauri() ? pickDirectory : undefined;
  const list = (text: string) => text.split(/[\s,;]+/).map((part) => part.trim()).filter(Boolean);
  const number = (text: string) => (text.trim() ? Number(text) : undefined);

  return (
    <div className="stack records">
      <section className="panel">
        <h2>Public property records · Harris County, TX</h2>
        <p className="muted">
          Collected from the county&apos;s own public sites with DataForge&apos;s user agent, robots.txt, a 5-second pause between requests, and an immediate stop on any error page.
          Tax-sale and foreclosure lists carry no owner or borrower names. Each collection is saved as a dataset you can export to Excel, match, and deduplicate.
        </p>
        <label className="toggle block small">
          <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /> I am authorized to collect and use this data and accept the sites&apos; terms.
        </label>
        <label className="field">
          <span>Purpose of this collection</span>
          <CustomSelect value={purpose} onChange={setPurpose} options={PURPOSES.map((option) => ({ value: option.id, label: option.label }))} />
        </label>
      </section>

      <Section title="Delinquent-tax sale (Tax Office)">
        <p className="muted small">The properties in the next monthly sale: address, status, precinct, minimum bid, adjudged value, judgment, tax years, and HCAD account. Resales show no minimum bid on the Tax Office list.</p>
        <PathInput label="HCAD files folder (optional: adds year built, area, rooms, market value)" value={taxHcad} onChange={setTaxHcad} placeholder="C:\Users\you\Downloads\hcad_2026" onBrowse={browse} />
        <RecordJob label="Collect the tax sale list" disabled={!ready} navigate={navigate}
          start={() => call("records.tax_sales", { ...base, hcad_dir: taxHcad.trim() || undefined })} />
      </Section>

      <Section title="Trustee foreclosure notices (County Clerk)">
        <p className="muted small">Notices by sale month: document number, sale date, filing date, pages, and a link to the notice. The property address is inside each scanned notice.</p>
        <div className="split tight">
          <label className="field"><span>First sale month</span><input type="month" value={fromMonth} onChange={(event) => setFromMonth(event.target.value)} /></label>
          <label className="field"><span>Last sale month</span><input type="month" value={toMonth} onChange={(event) => setToMonth(event.target.value)} /></label>
        </div>
        <RecordJob label="Collect the notices" disabled={!ready || !fromMonth} navigate={navigate}
          start={() => call("records.foreclosures", { ...base, from: fromMonth, to: toMonth || fromMonth })} />
      </Section>

      <Section title="Appraisal roll (HCAD bulk files)">
        <p className="muted small">
          Download Real_acct_owner.zip, Real_building_land.zip, Real_jur_exempt.zip, and Code_description_real.zip from hcad.org (Public Data, Download Property Data) into one folder.
          The list keeps the owner name and mailing address as HCAD publishes them; of the exemptions only Homestead is shown. The county has about 1.6 million accounts, so choose at least one filter.
        </p>
        <PathInput label="HCAD files folder" value={hcadDir} onChange={setHcadDir} placeholder="C:\Users\you\Downloads\hcad_2026" onBrowse={browse} />
        <div className="split tight">
          <label className="field"><span>ZIP codes (or prefixes such as 770)</span><input value={zips} onChange={(event) => setZips(event.target.value)} placeholder="77009 77018" /></label>
          <label className="field"><span>Property classes</span><input value={classes} onChange={(event) => setClasses(event.target.value)} placeholder="A1 (single-family), B1, C1 (vacant lots)…" /></label>
        </div>
        <div className="row-actions">
          <label className="toggle small"><input type="checkbox" checked={absentee} onChange={(event) => setAbsentee(event.target.checked)} /> Owner mails elsewhere</label>
          <label className="toggle small"><input type="checkbox" checked={outOfState} onChange={(event) => setOutOfState(event.target.checked)} /> Owner outside Texas</label>
          <label className="toggle small"><input type="checkbox" checked={noHomestead} onChange={(event) => setNoHomestead(event.target.checked)} /> No homestead exemption</label>
        </div>
        <fieldset className="row-actions records-owner-types">
          <legend className="small">Owner types (any)</legend>
          {OWNER_TYPES.map((type) => (
            <label className="toggle small" key={type}>
              <input type="checkbox" checked={ownerTypes.includes(type)} onChange={(event) => setOwnerTypes(event.target.checked ? [...ownerTypes, type] : ownerTypes.filter((t) => t !== type))} /> {type}
            </label>
          ))}
        </fieldset>
        <div className="split tight">
          <label className="field"><span>Owned at least (years)</span><input type="number" min={0} value={ownedYears} onChange={(event) => setOwnedYears(event.target.value)} /></label>
          <label className="field"><span>Built before (year)</span><input type="number" min={1800} value={builtBefore} onChange={(event) => setBuiltBefore(event.target.value)} /></label>
          <label className="field"><span>Market value from ($)</span><input type="number" min={0} value={minValue} onChange={(event) => setMinValue(event.target.value)} /></label>
          <label className="field"><span>Market value to ($)</span><input type="number" min={0} value={maxValue} onChange={(event) => setMaxValue(event.target.value)} /></label>
        </div>
        <RecordJob label="Build the property list" disabled={!ready || !hcadDir.trim()} navigate={navigate}
          start={() => call("records.hcad", {
            ...base, data_dir: hcadDir.trim(), zip: list(zips), property_class: list(classes.toUpperCase()), absentee, out_of_state: outOfState,
            no_homestead: noHomestead, owner_type: ownerTypes, owned_years: number(ownedYears), built_before: number(builtBefore),
            min_value: number(minValue), max_value: number(maxValue),
          })} />
      </Section>
    </div>
  );
}
