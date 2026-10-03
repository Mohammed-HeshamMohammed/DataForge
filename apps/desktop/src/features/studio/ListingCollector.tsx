import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { call } from "../../lib/ipc.ts";
import { loadSetting, saveSetting, studioHost } from "../../lib/desktop.ts";

type Row = Record<string, string>;
type Kind = "listings" | "products";
type Preset = Record<string, any> & { id: string; version: string; request_limits: Record<string, number> };
type PageRead = {
  url: string; records: Row[]; next_url: string | null; error: string | null;
  on_page?: number; from_browsing?: number; map_pins?: number; reported_total?: number | null; last_page?: number | null;
};
type DetailRead = { url: string; kind: Kind | null; record: Row; error: string | null };
type PageInfo = { url: string; ready_state: string; challenge_detected: boolean; password_fields: number };
type Saved = { pages: Record<string, Row[]>; savedAt: number };
type Coverage = { onPage: number; mapPins: number; reportedTotal: number | null };

/** Sites whose terms forbid automated browsing: only pages the user opens are read, never visited in sequence. */
const PAGE_ONLY_HOSTS = /(^|\.)craigslist\.org$/i;
/** Collections larger than this stay in memory for the session but are not mirrored to app storage. */
const MIRROR_LIMIT = 3_000_000;
const WATCH_INTERVAL_MS = 4000;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function collectorKind(url: string | null): Kind {
  try {
    return /(^|\.)amazon\.[a-z.]+$/i.test(new URL(url ?? "").hostname) ? "products" : "listings";
  } catch {
    return "listings";
  }
}

export function rowKey(row: Row, kind: Kind): string {
  return kind === "products" ? row.asin ?? "" : row.listing_url || row.listing_id || [row.address, row.zip || row.city].filter(Boolean).join("|");
}

/** Rows from every collected page, de-duplicated across pages; values already filled are kept. */
export function mergedRows(pages: Record<string, Row[]>, kind: Kind): Row[] {
  const rows = new Map<string, Row>();
  for (const records of Object.values(pages)) {
    for (const record of records) {
      const key = rowKey(record, kind);
      if (!key) continue;
      const existing = rows.get(key);
      rows.set(key, existing ? { ...record, ...Object.fromEntries(Object.entries(existing).filter(([, value]) => value !== "")) } : record);
    }
  }
  return [...rows.values()];
}

/** Zillow ZIP pages for every ZIP code already collected: /houston-tx/… → /houston-tx-77002/. Each carries its own map pins. */
export function zillowZipPages(currentUrl: string, rows: Row[]): string[] {
  let origin = "https://www.zillow.com";
  let area = "";
  try {
    const url = new URL(currentUrl);
    origin = url.origin;
    area = (url.pathname.split("/").filter(Boolean)[0] ?? "").replace(/-\d{5}$/, "");
  } catch {
    return [];
  }
  if (!/^[a-z0-9-]+-[a-z]{2}$/i.test(area)) return [];
  const zips = [...new Set(rows.map((row) => (row.zip ?? "").slice(0, 5)).filter((zip) => /^\d{5}$/.test(zip)))].sort();
  return zips.map((zip) => `${origin}/${area}-${zip}/`);
}

/** Merge a detail-page record into the row it belongs to, wherever that row was collected. */
export function withDetails(pages: Record<string, Row[]>, kind: Kind, key: string, details: Row): Record<string, Row[]> {
  const next: Record<string, Row[]> = {};
  for (const [url, records] of Object.entries(pages)) {
    next[url] = records.map((row) => (rowKey(row, kind) === key ? { ...row, ...Object.fromEntries(Object.entries(details).filter(([, value]) => value !== "")) } : row));
  }
  return next;
}

/**
 * Scrape Studio's built-in collector. Reads listings (real-estate sites, Zillow included) or Amazon products from the
 * page in the embedded browser, sweeps result pages or a list of pages with the preset's delay, opens each item for
 * its details, and stages everything as one dataset. Collected rows live in the app, not in the incognito page, so
 * they survive navigation and closing the Studio browser.
 */
export function ListingCollector({ scopeUrl, pageReady, acknowledged, purpose, presets, onStaged, siteNote }: {
  scopeUrl: string | null;
  pageReady: boolean;
  acknowledged: boolean;
  purpose: string;
  presets: Preset[];
  onStaged: (jobId: string) => void;
  /** The site catalog's tested note for this site, if any. */
  siteNote?: string;
}) {
  const host = useMemo(() => {
    try {
      return new URL(scopeUrl ?? "").hostname.replace(/^www\./, "");
    } catch {
      return "";
    }
  }, [scopeUrl]);
  const kind = collectorKind(scopeUrl);
  const noun = kind === "products" ? "products" : "listings";
  const onZillow = /(^|\.)zillow\.com$/i.test(host);
  const preset = useMemo(() => {
    const id = kind === "products" ? "generic.products" : "generic.listings";
    return presets.filter((p) => p.id === id).sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }))[0];
  }, [kind, presets]);
  const storageKey = `studio.collected.${kind}.${host}`;
  const [pages, setPages] = useState<Record<string, Row[]>>({});
  const pagesRef = useRef(pages);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [maxPages, setMaxPages] = useState(5);
  const [sweepList, setSweepList] = useState("");
  const [sweepDepth, setSweepDepth] = useState(1);
  const [detailLimit, setDetailLimit] = useState(25);
  const [watching, setWatching] = useState(false);
  const [running, setRunning] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stop = useRef(false);
  const rows = useMemo(() => mergedRows(pages, kind), [pages, kind]);
  const withoutDetails = rows.filter((row) => !row.details_collected_at && (row.listing_url || row.product_url));
  const pageOnly = PAGE_ONLY_HOSTS.test(host);
  const pageCap = Math.max(1, Number(preset?.request_limits.max_pages_default) || 1);
  const delay = Math.max(Number(preset?.request_limits.min_delay_ms) || 0, 1000);
  const ready = !!preset && acknowledged && !!purpose && !running;

  // Restore an unsaved collection for this site from app storage (the Studio page itself is incognito).
  useEffect(() => {
    if (!host) return;
    const saved = loadSetting<Saved | null>(storageKey, null);
    pagesRef.current = saved?.pages ?? {};
    setPages(pagesRef.current);
    setStatus(saved && Object.keys(saved.pages).length ? `Restored ${mergedRows(saved.pages, kind).length} unsaved ${noun} collected on ${host}.` : null);
    setError(null);
    setWatching(false);
  }, [host, kind, noun, storageKey]);

  const remember = useCallback((next: Record<string, Row[]>) => {
    pagesRef.current = next;
    setPages(next);
    const payload: Saved = { pages: next, savedAt: Date.now() };
    saveSetting(storageKey, JSON.stringify(payload).length <= MIRROR_LIMIT ? payload : null);
  }, [storageKey]);

  const showCoverage = (read: PageRead) => setCoverage({ onPage: read.on_page ?? read.records.length, mapPins: read.map_pins ?? 0, reportedTotal: read.reported_total ?? null });

  // Count what the collector can read whenever a page finishes loading.
  useEffect(() => {
    if (!pageReady || !scopeUrl) {
      setCoverage(null);
      return;
    }
    let cancelled = false;
    void studioHost.call<PageRead>(kind).then((read) => {
      if (!cancelled && !read.error) showCoverage(read);
    }).catch(() => {
      if (!cancelled) setCoverage(null);
    });
    return () => {
      cancelled = true;
    };
  }, [kind, pageReady, scopeUrl]);

  const checkUrl = async (target: string) => {
    const result = await call<{ allowed: boolean; reason: string | null }>("scrape.check_url", { preset, url: target, scope_url: scopeUrl, purpose });
    if (!result.allowed) throw new Error(result.reason ?? "The site's signals do not allow this collection");
  };

  /** Stops on access challenges and login pages; checks scope and the site's signals for the page being read. */
  const guardPage = async (): Promise<PageInfo> => {
    const info = await studioHost.call<PageInfo>("pageInfo");
    if (info.challenge_detected) throw new Error("The page shows an access challenge (CAPTCHA or bot check). Collection stopped; DataForge never bypasses these.");
    if (info.password_fields > 0) throw new Error("This page asks for a login. Collection stopped.");
    await checkUrl(info.url);
    return info;
  };

  const readPage = async (): Promise<PageRead> => {
    await guardPage();
    const read = await studioHost.call<PageRead>(kind);
    if (read.error) throw new Error(read.error);
    showCoverage(read);
    return read;
  };

  const add = (read: PageRead) => {
    const before = new Set(mergedRows(pagesRef.current, kind).map((row) => rowKey(row, kind)));
    const next = { ...pagesRef.current, [read.url]: read.records };
    if (Object.keys(next).length > pageCap) throw new Error(`This collection already has ${pageCap} pages, the preset's limit. Save it as a dataset, then continue.`);
    remember(next);
    return read.records.filter((row) => !before.has(rowKey(row, kind))).length;
  };

  const waitForPage = async (previousUrl: string) => {
    for (let waited = 0; waited < 30000; waited += 250) {
      await sleep(250);
      const info = await studioHost.call<PageInfo>("pageInfo").catch(() => null);
      if (info && info.url !== previousUrl && info.ready_state === "complete") return;
    }
    throw new Error("The page did not finish loading in time");
  };

  const visit = async (target: string, fromUrl: string) => {
    await checkUrl(target);
    await sleep(delay);
    if (stop.current) return false;
    await studioHost.navigate(target);
    await waitForPage(fromUrl);
    return true;
  };

  /** Read the current page, then up to `limit - 1` following result pages. Returns the last URL read. */
  const readPages = async (limit: number, label: string): Promise<string> => {
    let current = (await studioHost.call<PageInfo>("pageInfo")).url;
    for (let page = 1; page <= limit && !stop.current; page++) {
      const read = await readPage();
      current = read.url;
      const fresh = add(read);
      const total = mergedRows(pagesRef.current, kind).length;
      setStatus(`${label}page ${page}: ${read.records.length} ${noun} (${fresh} new). ${total} collected.`);
      if (page >= limit) break;
      if (page > 1 && read.records.length && fresh < read.records.length * 0.2) {
        setStatus(`${label}page ${page} repeated ${noun} already collected: this site loads its next pages in the browser. Open them yourself and use Add this page.`);
        break;
      }
      if (!read.next_url) break;
      if (!(await visit(read.next_url, read.url))) break;
    }
    return current;
  };

  const run = async (name: string, task: () => Promise<void>) => {
    if (!preset || !scopeUrl) return;
    stop.current = false;
    setRunning(name);
    setError(null);
    try {
      await task();
      if (stop.current) setStatus(`Stopped. ${mergedRows(pagesRef.current, kind).length} ${noun} collected so far are kept.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(null);
    }
  };

  const collectPages = (followPages: boolean) => run("pages", async () => {
    await readPages(followPages && !pageOnly ? Math.min(maxPages, pageCap) : 1, "");
    if (pageOnly && followPages) setStatus(`${host}'s terms do not allow automated browsing, so only the page you opened was read.`);
  });

  const sweep = () => run("sweep", async () => {
    const targets = [...new Set(sweepList.split(/\s+/).map((line) => line.trim()).filter(Boolean))];
    let current = (await studioHost.call<PageInfo>("pageInfo")).url;
    for (const [index, target] of targets.entries()) {
      if (stop.current) break;
      let url: URL;
      try {
        url = new URL(target);
      } catch {
        setStatus(`Skipped ${target}: not a full web address.`);
        continue;
      }
      if (url.hostname.replace(/^www\./, "") !== host) {
        setStatus(`Skipped ${target}: outside ${host}.`);
        continue;
      }
      try {
        if (url.href !== current && !(await visit(url.href, current))) break;
        current = await readPages(Math.min(sweepDepth, pageCap), `Page ${index + 1} of ${targets.length}, result `);
      } catch (err) {
        // A page the site's signals exclude is skipped; challenges and logins stop the sweep.
        const message = err instanceof Error ? err.message : String(err);
        if (/challenge|login/i.test(message)) throw err;
        setStatus(`Skipped ${url.href}: ${message}`);
      }
    }
    if (!stop.current) setStatus(`Sweep finished: ${targets.length} page(s) visited, ${mergedRows(pagesRef.current, kind).length} ${noun} collected.`);
  });

  const collectDetails = () => run("details", async () => {
    const todo = mergedRows(pagesRef.current, kind).filter((row) => !row.details_collected_at && (row.listing_url || row.product_url)).slice(0, detailLimit);
    let current = (await studioHost.call<PageInfo>("pageInfo")).url;
    let done = 0;
    for (const row of todo) {
      if (stop.current) break;
      const target = row.listing_url || row.product_url;
      let url: URL;
      try {
        url = new URL(target);
      } catch {
        continue;
      }
      if (url.hostname.replace(/^www\./, "") !== host) continue;
      try {
        if (!(await visit(url.href, current))) break;
        current = url.href;
        await guardPage();
        // Some sites load facts, histories, and schools as the page scrolls.
        for (let step = 0; step < 3; step++) {
          await studioHost.call("scrollPage", [1]);
          await sleep(600);
        }
        const read = await studioHost.call<DetailRead>("details", [{ url: target, id: row.listing_id ?? row.asin ?? "" }]);
        if (read.error) throw new Error(read.error);
        remember(withDetails(pagesRef.current, kind, rowKey(row, kind), { ...read.record, details_collected_at: read.record.details_collected_at || new Date().toISOString() }));
        done += 1;
        setStatus(`Details ${done} of ${todo.length}: ${row.address || row.title || target}`);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (/challenge|login/i.test(message)) throw err;
        remember(withDetails(pagesRef.current, kind, rowKey(row, kind), { details_collected_at: `skipped: ${message.slice(0, 120)}` }));
      }
    }
    if (!stop.current) setStatus(`Details collected for ${done} of ${todo.length} ${noun}.`);
  });

  // Keep adding what the site shows while the user browses (map moves, filters, scrolling).
  useEffect(() => {
    if (!watching || running) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const read = await readPage();
        if (cancelled) return;
        const fresh = add(read);
        if (fresh) setStatus(`Added ${fresh} ${noun} while browsing. ${mergedRows(pagesRef.current, kind).length} collected.`);
      } catch (err) {
        if (!cancelled) setStatus(`Not adding this view: ${err instanceof Error ? err.message : String(err)}`);
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), WATCH_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // readPage/add read the latest collection through refs; restarting on every page change is not needed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watching, running, kind, host]);

  const save = async () => {
    if (!preset || !rows.length) return;
    setError(null);
    try {
      const retrieved = new Date().toISOString();
      const staged = Object.entries(pagesRef.current).filter(([, records]) => records.length).map(([url, records]) => ({ url, records, retrieved_at: retrieved }));
      const result = await call<{ job_id: string }>("scrape.stage_rendered", {
        preset, pages: staged, run_mode: "full", policy_acknowledgement: acknowledged, purpose, dataset_name: `${host} ${noun} (Scrape Studio)`,
      });
      onStaged(result.job_id);
      remember({});
      setWatching(false);
      setStatus(`Saving ${rows.length} ${noun} as a dataset…`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!scopeUrl) return null;
  const columns = kind === "products" ? ["title", "price", "rating"] : ["address", "price", "beds", "baths"];
  const zipPages = onZillow ? zillowZipPages(scopeUrl, rows) : [];
  return (
    <section className="studio-recommendation studio-collector" aria-labelledby="studio-collector-heading">
      <span className="studio-recommendation-badge">Built-in collector</span>
      <strong id="studio-collector-heading">Collect {noun} from {host}</strong>
      <p className="small">
        {coverage === null ? `Reads ${noun} from the page you are viewing and the results the site loads while you browse.` : (
          <>
            {coverage.onPage} {noun} on this page
            {coverage.mapPins ? ` · ${coverage.mapPins} map pins` : ""}
            {coverage.reportedTotal ? ` · the site reports ${coverage.reportedTotal.toLocaleString()} for this search` : ""}.
          </>
        )}
        {pageOnly ? ` ${host}'s terms do not allow automated browsing: only pages you open are read.` : ""}
      </p>
      {siteNote && <p className="muted small">Tested: {siteNote}</p>}
      {!preset && <p className="note small" role="status">The {kind === "products" ? "generic.products" : "generic.listings"} preset is not installed.</p>}
      <div className="row-actions">
        <button type="button" className="btn btn-small" disabled={!ready || !pageReady} onClick={() => void collectPages(false)}>Add this page</button>
        {!pageOnly && (
          <>
            <button type="button" className="btn btn-small btn-primary" disabled={!ready || !pageReady} onClick={() => void collectPages(true)}>Collect {noun}</button>
            <label className="field inline small">
              <span>Pages</span>
              <input type="number" min={1} max={pageCap} value={maxPages} onChange={(event) => setMaxPages(Math.max(1, Math.min(pageCap, Number(event.target.value) || 1)))} />
            </label>
          </>
        )}
        {running && <button type="button" className="btn btn-small btn-danger" onClick={() => (stop.current = true)}>Stop</button>}
      </div>
      <label className="toggle block small">
        <input type="checkbox" checked={watching} disabled={!preset || !acknowledged || !purpose} onChange={(event) => setWatching(event.target.checked)} />
        Keep adding while I browse (map moves, filters, scrolling)
      </label>
      {(!acknowledged || !purpose) && <p className="muted small">Confirm the authorization statement and choose a purpose below to collect.</p>}
      {!pageOnly && (
        <details className="studio-collector-more">
          <summary>Collect from a list of pages{onZillow ? " (more map pins)" : ""}</summary>
          {onZillow && (
            <p className="muted small">
              Each Zillow area page carries up to 500 map pins. Sweeping the ZIP pages of an area collects far more homes than one map view.
              Zillow&apos;s robots.txt allows area, ZIP, result-page, and path-filter pages (for example /houses/, /3-_beds/, /300000-500000_price/), but not map-move or ?searchQueryState addresses.
            </p>
          )}
          <textarea className="code" rows={4} aria-label="Pages to visit, one per line" value={sweepList} onChange={(event) => setSweepList(event.target.value)}
            placeholder={onZillow ? "https://www.zillow.com/houston-tx-77002/" : `https://${host}/…`} spellCheck={false} />
          <div className="row-actions">
            {onZillow && <button type="button" className="btn btn-small" disabled={!zipPages.length || !!running} onClick={() => setSweepList(zipPages.join("\n"))}>Fill with {zipPages.length} ZIP pages</button>}
            <label className="field inline small">
              <span>Result pages each</span>
              <input type="number" min={1} max={20} value={sweepDepth} onChange={(event) => setSweepDepth(Math.max(1, Math.min(20, Number(event.target.value) || 1)))} />
            </label>
            <button type="button" className="btn btn-small btn-primary" disabled={!ready || !pageReady || !sweepList.trim()} onClick={() => void sweep()}>Visit these pages</button>
          </div>
        </details>
      )}
      {status && <p className="small" role="status">{status}</p>}
      {error && <p className="note note-warning small" role="alert">{error}</p>}
      {rows.length > 0 && (
        <>
          <table className="studio-collector-preview small">
            <thead><tr>{columns.map((column) => <th key={column} scope="col">{column.replace(/_/g, " ")}</th>)}</tr></thead>
            <tbody>{rows.slice(0, 6).map((row) => <tr key={rowKey(row, kind)}>{columns.map((column) => <td key={column}>{row[column] ?? ""}</td>)}</tr>)}</tbody>
          </table>
          <p className="muted small">{rows.length} {noun} collected{rows.some((row) => row.collected_from === "map") ? ` (${rows.filter((row) => row.collected_from === "map").length} from map pins)` : ""}.</p>
          {!pageOnly && (
            <div className="row-actions">
              <button type="button" className="btn btn-small" disabled={!ready || !withoutDetails.length} onClick={() => void collectDetails()}>
                Collect details ({Math.min(detailLimit, withoutDetails.length)} of {withoutDetails.length})
              </button>
              <label className="field inline small">
                <span>At most</span>
                <input type="number" min={1} max={1000} value={detailLimit} onChange={(event) => setDetailLimit(Math.max(1, Math.min(1000, Number(event.target.value) || 1)))} />
              </label>
            </div>
          )}
          <div className="row-actions">
            <button type="button" className="btn btn-small btn-primary" disabled={!!running || !acknowledged || !purpose} onClick={() => void save()}>Save {rows.length} {noun} as a dataset</button>
            <button type="button" className="btn btn-small" disabled={!!running} onClick={() => { remember({}); setStatus(null); }}>Clear</button>
          </div>
          <p className="muted small">Kept in DataForge until you save or clear them, even when the Studio browser closes. Detail pages open one at a time with the preset&apos;s {Math.round(delay / 1000)}-second delay.</p>
        </>
      )}
    </section>
  );
}
