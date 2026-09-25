import { useEffect, useMemo, useRef, useState } from "react";
import { call } from "../lib/ipc.ts";
import { displayValue, mappingProblems, MATCH_ROLES } from "../lib/format.ts";
import { useService } from "../lib/hooks.ts";
import type { ColumnProfile } from "../lib/types.ts";
import { ErrorNote } from "./ui.tsx";
import { CustomSelect, type SelectOption } from "./CustomSelect.tsx";

type Profile = { columns: ColumnProfile[]; sensitive_roles: string[] };
type SavedMapping = { id: string; version: number; mapping: Record<string, string>; entity_type: string | null; export_exclude: string[] } | null;

export const ENTITY_TYPE_OPTIONS: SelectOption[] = [
  { value: "person", label: "People or contacts", description: "Customers, owners, applicants, or individual leads" },
  { value: "lead", label: "Sales leads", description: "Prospects collected from directories, forms, or campaigns" },
  { value: "real_estate", label: "Real-estate leads", description: "Owners, agents, properties, parcels, or listings" },
  { value: "business", label: "Companies or organizations", description: "Businesses, vendors, accounts, or local places" },
  { value: "property", label: "Properties", description: "Homes, apartments, land, or commercial listings" },
  { value: "product", label: "Products", description: "Catalog items, offers, SKUs, UPCs, or ASINs" },
  { value: "job", label: "Jobs", description: "Vacancies, employers, recruiters, or applications" },
  { value: "article", label: "Articles or publications", description: "News, papers, posts, authors, or publishers" },
  { value: "media", label: "Movies or media", description: "Videos, films, shows, creators, or channels" },
  { value: "software", label: "Software or repositories", description: "GitHub projects, packages, releases, or maintainers" },
  { value: "custom", label: "Something else", description: "Use the general-purpose field choices" },
];
export const ENTITY_TYPES = ENTITY_TYPE_OPTIONS.map((option) => option.value);

const BASE_ROLE_OPTIONS: Record<string, Omit<SelectOption, "value">> = {
  identifier: { label: "Unique ID", description: "A stable ID such as account ID, parcel/APN, SKU, ASIN, or listing ID", group: "Best evidence" },
  phone: { label: "Phone number", description: "Several phone columns are allowed", group: "Best evidence" },
  email: { label: "Email address", description: "Several email columns are allowed", group: "Best evidence" },
  url: { label: "Profile or source URL", description: "Listing, profile, product, company, or repository address", group: "Best evidence" },
  address: { label: "Full or formatted address", description: "A complete physical, property, office, or venue address", group: "Address details" },
  mailing_address: { label: "Mailing address", description: "Postal or correspondence address", group: "Address details" },
  name: { label: "Full name or title", description: "Person, company, property, product, job, or item name", group: "Names and places" },
  first_name: { label: "First name", description: "Given name for a person or lead", group: "Names and places" },
  last_name: { label: "Last name", description: "Surname or family name", group: "Names and places" },
  building_name: { label: "Building or property name", group: "Address details" },
  house_number: { label: "House or street number", group: "Address details" },
  street: { label: "Street or road name", group: "Address details" },
  unit: { label: "Apartment, suite, or unit", group: "Address details" },
  po_box: { label: "PO box", group: "Address details" },
  neighborhood: { label: "Neighborhood or suburb", group: "Address details" },
  district: { label: "District or borough", group: "Address details" },
  city: { label: "City, town, or locality", group: "Address details" },
  county: { label: "County", group: "Address details" },
  region: { label: "State, region, or province", group: "Address details" },
  country: { label: "Country", group: "Address details" },
  country_code: { label: "Country code", description: "ISO code such as US, GB, or EG", group: "Address details" },
  postal_code: { label: "ZIP or postal code", group: "Address details" },
  latitude: { label: "Latitude", group: "Address details" },
  longitude: { label: "Longitude", group: "Address details" },
  other: { label: "Keep, but do not compare", description: "Preserve this column without using it to find duplicates", group: "Do not use for matching" },
  ignore: { label: "Ignore completely", description: "Do not compare or use this column", group: "Do not use for matching" },
};

const DOMAIN_LABELS: Record<string, Partial<Record<(typeof MATCH_ROLES)[number], string>>> = {
  person: { identifier: "Person, customer, or lead ID", name: "Full person name", address: "Home or contact address", url: "Person or profile URL" },
  lead: { identifier: "Lead, contact, or CRM ID", name: "Lead or contact name", address: "Lead street address", url: "Lead or profile URL" },
  real_estate: { identifier: "Parcel, APN, property, or listing ID", name: "Owner, lead, or property name", first_name: "Owner or lead first name", last_name: "Owner or lead last name", address: "Full property address", mailing_address: "Owner mailing address", building_name: "Building or development name", unit: "Apartment or unit number", district: "Property district", county: "Property county", url: "Property or listing URL" },
  property: { identifier: "Parcel, APN, property, or listing ID", name: "Property name", address: "Full property address", mailing_address: "Owner mailing address", building_name: "Building or development name", unit: "Apartment or unit number", district: "Property district", county: "Property county", url: "Property or listing URL" },
  business: { identifier: "Company, account, or place ID", name: "Company or organization name", address: "Office or business address", url: "Company website or profile URL" },
  product: { identifier: "SKU, UPC, EAN, GTIN, MPN, or ASIN", name: "Product name", address: "Seller or store address", url: "Product URL" },
  job: { identifier: "Job, vacancy, or application ID", name: "Job title", address: "Job location or office address", url: "Job listing URL" },
  article: { identifier: "Article, DOI, ISBN, or publication ID", name: "Article or publication title", first_name: "Author first name", last_name: "Author last name", url: "Article or source URL" },
  media: { identifier: "Video, IMDb, or media ID", name: "Title or channel name", url: "Video or media URL" },
  software: { identifier: "Repository, package, or release ID", name: "Repository, package, or project name", url: "Repository or package URL" },
};

function roleOptions(entityType: string, candidates: string[]): SelectOption[] {
  const ordered = [...new Set([...candidates, ...MATCH_ROLES])];
  return ordered.map((role) => ({
    value: role,
    ...BASE_ROLE_OPTIONS[role],
    label: DOMAIN_LABELS[entityType]?.[role as (typeof MATCH_ROLES)[number]] ?? BASE_ROLE_OPTIONS[role]?.label ?? role.replaceAll("_", " "),
  }));
}

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
  const initializedDataset = useRef<string | null>(null);

  const suggestions = useMemo(() => {
    const result: Record<string, string> = {};
    for (const column of profile.data?.columns ?? []) result[column.name] = column.proposed_role ?? "other";
    return result;
  }, [profile.data]);

  useEffect(() => {
    if (!profile.data || !saved.loaded || initializedDataset.current === datasetId) return;
    initializedDataset.current = datasetId;
    if (saved.data) {
      setMapping({ ...suggestions, ...saved.data.mapping });
      setEntityType(saved.data.entity_type ?? "custom");
      setExcluded(new Set(saved.data.export_exclude ?? []));
      setConfirmedAmbiguous(new Set(profile.data.columns.map((c) => c.name)));
    } else {
      setMapping(suggestions);
    }
  }, [datasetId, profile.data, saved.data, saved.loaded, suggestions]);

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
        <h3 id="mapping-title">Tell DataForge what each column means</h3>
        <label className="toggle">
          <input type="checkbox" checked={revealed} onChange={(e) => setRevealed(e.target.checked)} /> Reveal sample values
        </label>
      </div>
      <p className="muted">Choose the kind of records first. DataForge changes the wording and recommendations for that work—real-estate leads get property roles, while software data gets repository and package roles. Suggestions never merge anything until you preview the result.</p>
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
        <span>What does one row represent?</span>
        <CustomSelect value={entityType} onChange={setEntityType} options={ENTITY_TYPE_OPTIONS} searchable searchPlaceholder="Search people, real estate, products, software…" />
      </label>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Source column</th>
              <th scope="col">Samples</th>
              <th scope="col">Empty</th>
              <th scope="col">What this column means</th>
              <th scope="col">Suggestion</th>
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
                    <CustomSelect
                      ariaLabel={`Role for ${column.name}`}
                      value={role}
                      onChange={(value) => {
                        setMapping((previous) => ({ ...previous, [column.name]: value }));
                        setConfirmedAmbiguous(new Set(confirmedAmbiguous).add(column.name));
                      }}
                      searchable
                      searchPlaceholder="Search ID, street, county, country, URL…"
                      options={roleOptions(entityType, column.candidates)}
                    />
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
          Check {unconfirmed.length} uncertain suggestion{unconfirmed.length === 1 ? "" : "s"}: {unconfirmed.map((c) => c.name).join(", ")}.
        </p>
      )}
      <div className="row-actions">
        <button type="button" className="btn" onClick={() => setMapping(suggestions)}>
          Restore automatic suggestions
        </button>
        <button type="button" className="btn btn-primary" disabled={problems.length > 0 || unconfirmed.length > 0 || !dirty} onClick={() => void save()}>
          {saved.data ? "Save these column meanings" : "Save and continue"}
        </button>
      </div>
      <ErrorNote message={error} />
      {status && <p className="note" role="status">{status}</p>}
    </section>
  );
}
