import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { Navigate } from "../../app/App.tsx";
import { call, isTauri } from "../../lib/ipc.ts";
import { getZoom, studioHost, type Bounds } from "../../lib/desktop.ts";
import { useJob, useService } from "../../lib/hooks.ts";
import { isActive } from "../../lib/format.ts";
import { ErrorNote, JobProgress } from "../../components/ui.tsx";
import { ScrapeResult } from "../scraping/Scraping.tsx";
import { PURPOSES } from "../scraping/sources.ts";

type Selector = { css?: string; xpath?: string; attribute?: string };
type Field = { key: string; type: "string" | "url" | "decimal" | "integer"; required: boolean; selectors: Selector[]; transforms: string[] };
type Preset = Record<string, any> & { id: string; version: string; display_name: string; strategy: { preferred: string; allowed: string[] }; request_limits: Record<string, number> };
type Pick = { fallback_xpaths?: string[]; mode: string; tag: string; text: string; attributes: Record<string, string>; suggested_attribute: string | null; selector: string; relative_selector?: string | null; inside_record_root?: boolean; repeated?: { selector: string; count: number } | null };
type Extracted = { url: string; candidates: number; records: Record<string, string>[]; next_url: string | null; error: string | null };
type PageInfo = { url: string; title: string; ready_state: string; challenge_detected: boolean; password_fields: number; inaccessible_frames: number };

const TRANSFORM_DEFAULTS: Record<Field["type"], string[]> = {
  string: ["trim", "collapse_whitespace"],
  url: ["to_absolute_url"],
  decimal: ["parse_currency_amount"],
  integer: ["parse_integer"],
};

function hostOf(url: string): string | null {
  try {
    return new URL(url).hostname;
  } catch {
    return null;
  }
}

function slug(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "field";
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function Studio({ navigate }: { navigate: Navigate }) {
  const presets = useService<Preset[]>("preset.list");
  const bases = (presets.data ?? []).filter((p) => p.strategy.allowed.includes("webview") && p.status !== "disabled" && p.status !== "deprecated" && !((p.extraction as { mode?: string })?.mode ?? "").match(/structured_data|article|document_tables/));
  const structuredPreset = (presets.data ?? []).find((p) => p.id === "generic.structured_data");
  const [structured, setStructured] = useState<{ url: string; types: Record<string, number>; suggested_type: string | null } | null>(null);
  const [baseKey, setBaseKey] = useState("");
  const base = bases.find((p) => `${p.id}@${p.version}` === baseKey) ?? bases[0];

  const [url, setUrl] = useState("");
  const [scopeUrl, setScopeUrl] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<{ url: string; state: string } | null>(null);
  const [pageInfo, setPageInfo] = useState<PageInfo | null>(null);
  const [mode, setMode] = useState<"none" | "element" | "repeated" | "next" | "detail">("none");
  const [recordRoot, setRecordRoot] = useState("");
  const [rootCount, setRootCount] = useState<number | null>(null);
  const [fields, setFields] = useState<Field[]>([]);
  const [nextCss, setNextCss] = useState("");
  const [pageMode, setPageMode] = useState<"none" | "next_link" | "infinite_scroll" | "detail_links">("none");
  const [detailCss, setDetailCss] = useState("");
  const [maxScrolls, setMaxScrolls] = useState(10);
  const [name, setName] = useState("my_cards");
  const [version, setVersion] = useState("1.0.0");
  const [maxPages, setMaxPages] = useState(3);
  const [acknowledged, setAcknowledged] = useState(false);
  const settings = useService<{ default_purpose: string }>("settings.get");
  const [purpose, setPurpose] = useState("");
  useEffect(() => {
    if (!purpose && settings.data) setPurpose(settings.data.default_purpose);
  }, [settings.data, purpose]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const stopRequested = useRef(false);
  const previewRef = useRef<HTMLDivElement>(null);
  const opened = useRef(false);

  const desktop = isTauri();

  const bounds = (): Bounds | null => {
    const rect = previewRef.current?.getBoundingClientRect();
    // The child WebView is positioned in window units; CSS pixels are scaled by the page zoom (View → Zoom).
    const zoom = getZoom();
    return rect ? { x: rect.left * zoom, y: rect.top * zoom, width: rect.width * zoom, height: rect.height * zoom } : null;
  };

  // Keep the native child WebView positioned over the preview placeholder.
  useEffect(() => {
    if (!desktop) return;
    const sync = () => {
      const b = bounds();
      if (b && opened.current) void studioHost.setBounds(b).catch(() => {});
    };
    const observer = new ResizeObserver(sync);
    if (previewRef.current) observer.observe(previewRef.current);
    window.addEventListener("resize", sync);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", sync);
      opened.current = false;
      void studioHost.close().catch(() => {});
    };
  }, [desktop]);

  useEffect(() => {
    if (!desktop) return;
    let unlisten: () => void = () => {};
    void studioHost
      .onEvent((event) => {
        if (event.type === "page_load") setLoaded({ url: event.url ?? "", state: event.event ?? "" });
        if (event.type === "navigation_blocked") setNotice(`Blocked navigation outside this preset's scope: ${event.url}`);
      })
      .then((fn) => (unlisten = fn));
    return () => unlisten();
  }, [desktop]);

  // Offer selector-free extraction when the loaded page carries schema.org structured data.
  useEffect(() => {
    if (!desktop || loaded?.state !== "finished") return;
    let cancelled = false;
    void (async () => {
      try {
        const snapshot = await studioHost.call<{ url: string; html: string }>("html");
        const found = await call<{ types: Record<string, number>; suggested_type: string | null; mapped_types: string[] }>("scrape.detect_structured", { html: snapshot.html, url: snapshot.url });
        if (!cancelled) setStructured(found.mapped_types.length ? { url: snapshot.url, types: found.types, suggested_type: found.suggested_type } : null);
      } catch {
        if (!cancelled) setStructured(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [desktop, loaded]);

  const testStructured = async () => {
    if (!structuredPreset || !scopeUrl) return;
    try {
      setError(null);
      const snapshot = await studioHost.call<{ url: string; html: string }>("html");
      const { source: _s, errors: _e, health_status: _h, declared_status: _d, package: _p, ...clean } = structuredPreset;
      const result = await call<{ job_id: string }>("scrape.stage_rendered", {
        preset: clean, pages: [{ url: snapshot.url, html: snapshot.html, retrieved_at: new Date().toISOString() }], run_mode: "test", policy_acknowledgement: acknowledged, purpose,
      });
      setJobId(result.job_id);
    } catch (err) {
      fail(err);
    }
  };

  const draftPreset = useCallback((): Preset | null => {
    if (!base) return null;
    const { source: _s, errors: _e, health_status: _h, declared_status: _d, package: _p, ...clean } = base;
    return {
      ...clean,
      id: `custom.local.${slug(name)}`,
      version,
      display_name: `${name} (Scrape Studio)`,
      status: "active",
      parent_preset_id: base.id,
      parent_preset_version: base.version,
      strategy: { preferred: "webview", allowed: ["webview"] },
      extraction: { ...clean.extraction, record_root: { css: recordRoot }, fields },
      pagination:
        pageMode === "next_link" && nextCss
          ? { type: "next_link", next: { css: nextCss, attribute: "href" }, stop_conditions: ["max_pages", "max_records", "repeated_canonical_url", "no_new_records"] }
          : pageMode === "infinite_scroll"
            ? { type: "infinite_scroll", max_scrolls: maxScrolls, stop_conditions: ["max_records", "no_new_records", "max_duration"] }
            : pageMode === "detail_links" && detailCss
              ? { type: "detail_links", links: { css: detailCss, attribute: "href" }, stop_conditions: ["max_records", "repeated_canonical_url"] }
              : { type: "none" },
      validation: { ...clean.validation, unique_by: fields.some((f) => f.type === "url") ? [fields.find((f) => f.type === "url")!.key] : [] },
    };
  }, [base, name, version, recordRoot, fields, nextCss, pageMode, detailCss, maxScrolls]);

  const fail = (err: unknown) => setError(err instanceof Error ? err.message : String(err));

  /** Scope check for browsing; with `collecting`, also the site's robots.txt, TDMRep, and AIPREF signals for the purpose. */
  const checkUrl = async (target: string, scope: string, collecting = false) => {
    const result = await call<{ allowed: boolean; reason: string | null; skippable: boolean }>("scrape.check_url", { preset: draftPreset(), url: target, scope_url: scope, ...(collecting ? { purpose } : {}) });
    if (!result.allowed) throw new Error(result.reason ?? "URL is not allowed");
  };

  const load = async () => {
    try {
      setError(null);
      setNotice(null);
      const host = hostOf(url);
      if (!host) throw new Error("Enter a full URL, for example https://example.com/listings");
      await checkUrl(url, url);
      const b = bounds();
      if (!b) throw new Error("Preview area is not ready");
      const allowed = base?.url_scope?.user_supplied_host ? [host] : (base?.url_scope?.allowed_hosts as string[]);
      await studioHost.open(url, allowed, b);
      opened.current = true;
      setScopeUrl(url);
      setPageInfo(null);
    } catch (err) {
      fail(err);
    }
  };

  // Poll for element picks while a picker mode is active.
  useEffect(() => {
    if (mode === "none") return;
    let cancelled = false;
    const poll = async () => {
      while (!cancelled) {
        try {
          const picks = await studioHost.call<Pick[]>("takePicks");
          if (picks.length) {
            applyPick(picks[picks.length - 1]);
            setMode("none");
            return;
          }
        } catch (err) {
          fail(err);
          setMode("none");
          return;
        }
        await sleep(350);
      }
    };
    void poll();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const startPick = async (next: "element" | "repeated" | "next" | "detail") => {
    try {
      setError(null);
      await studioHost.call("setMode", [next, next === "element" ? recordRoot : null]);
      setMode(next);
      setNotice(next === "repeated" ? "Click one repeated card or row in the page." : next === "next" ? "Click the next-page link." : next === "detail" ? "Click one item link that opens a detail page." : "Click the value to extract inside a record.");
    } catch (err) {
      fail(err);
    }
  };

  const cancelPick = async () => {
    await studioHost.call("setMode", ["none"]).catch(() => {});
    setMode("none");
    setNotice(null);
  };

  const countRoot = async (css: string) => {
    if (!css) return setRootCount(null);
    try {
      const result = await studioHost.call<{ count: number; error: string | null }>("count", [css]);
      setRootCount(result.error ? null : result.count);
      if (result.error) setError(`Record root selector is invalid: ${result.error}`);
    } catch (err) {
      fail(err);
    }
  };

  const applyPick = (pick: Pick) => {
    setNotice(null);
    if (pick.mode === "repeated") {
      if (!pick.repeated) {
        setError("No repeated pattern found around that element. Pick a card that appears at least three times.");
        return;
      }
      setRecordRoot(pick.repeated.selector);
      setRootCount(pick.repeated.count);
    } else if (pick.mode === "next") {
      if (pick.tag !== "a" && !pick.attributes.href) {
        setError("Pick the link element for the next page.");
        return;
      }
      setNextCss(pick.selector);
      setPageMode("next_link");
    } else if (pick.mode === "detail") {
      if (!pick.repeated) {
        setError("No repeated item links found around that element. Pick a link that appears on every item.");
        return;
      }
      setDetailCss(pick.repeated.selector);
      setPageMode("detail_links");
    } else {
      if (recordRoot && !pick.inside_record_root) {
        setError("That element is outside the selected repeated item. Pick a value inside a card.");
        return;
      }
      const type: Field["type"] = pick.suggested_attribute ? "url" : "string";
      const key = slug(pick.text.split(/\s+/).slice(0, 2).join(" ") || pick.tag);
      const css = recordRoot ? pick.relative_selector ?? pick.selector : pick.selector;
      setFields((current) => [
        ...current,
        {
          key: current.some((f) => f.key === key) ? `${key}_${current.length + 1}` : key,
          type,
          required: current.length === 0,
          selectors: [
            { css: css === ":scope" ? "" : css, ...(pick.suggested_attribute ? { attribute: pick.suggested_attribute } : {}) },
            ...(pick.fallback_xpaths ?? []).map((xpath) => ({ xpath, ...(pick.suggested_attribute ? { attribute: pick.suggested_attribute } : {}) })),
          ],
          transforms: TRANSFORM_DEFAULTS[type],
        },
      ]);
    }
  };

  const extractCurrent = async (limit: number): Promise<{ page: Extracted; info: PageInfo }> => {
    const info = await studioHost.call<PageInfo>("pageInfo");
    setPageInfo(info);
    if (info.challenge_detected) throw new Error("The page shows an access challenge (CAPTCHA or bot check). Collection stopped; DataForge never bypasses these.");
    if (info.password_fields > 0) throw new Error("The page asks for a login. Collection stopped; DataForge does not collect behind logins.");
    const page = await studioHost.call<Extracted>("extract", [{ record_root: recordRoot, fields, next_css: pageMode === "next_link" ? nextCss || null : null, limit }]);
    if (page.error) throw new Error(page.error);
    return { page, info };
  };

  const stage = async (runMode: "test" | "full", pages: { url: string; records: Record<string, string>[]; retrieved_at: string }[]) => {
    const result = await call<{ job_id: string }>("scrape.stage_rendered", { preset: draftPreset(), pages, run_mode: runMode, policy_acknowledgement: acknowledged, purpose, dataset_name: `${name} (Scrape Studio)` });
    setJobId(result.job_id);
  };

  const save = async () => {
    try {
      setError(null);
      const preset = draftPreset();
      const { errors } = await call<{ errors: string[] }>("preset.validate", { preset });
      if (errors.length) throw new Error(errors.join("; "));
      await call("preset.save_custom", { preset: { ...preset, strategy: { preferred: "webview", allowed: ["webview"] } } });
      setNotice(`Saved custom.local.${slug(name)}@${version}. Run a test with it before a full run.`);
      void presets.reload();
    } catch (err) {
      fail(err);
    }
  };

  const waitForPage = async (previousUrl: string) => {
    for (let waited = 0; waited < 30000; waited += 250) {
      await sleep(250);
      const info = await studioHost.call<PageInfo>("pageInfo").catch(() => null);
      if (info && info.url !== previousUrl && info.ready_state === "complete") return;
    }
    throw new Error("The page did not finish loading in time");
  };

  /** Test runs collect at most 10 records with the same pagination logic a full run uses. */
  const collect = async (runMode: "test" | "full") => {
    if (!base || !scopeUrl) return;
    stopRequested.current = false;
    setRunning(runMode);
    setError(null);
    const pages: { url: string; records: Record<string, string>[]; retrieved_at: string }[] = [];
    const delay = Math.max(Number(base.request_limits.min_delay_ms) || 0, 1000);
    const recordLimit = runMode === "test" ? 10 : base.request_limits.max_records_default;
    const scrollLimit = runMode === "test" ? 0 : maxScrolls;
    try {
      await checkUrl((await studioHost.call<PageInfo>("pageInfo")).url, scopeUrl, true);
      if (pageMode === "infinite_scroll") {
        // Scroll until the item count stops growing (twice in a row), the scroll cap, or the record cap.
        let last = -1;
        let idle = 0;
        for (let round = 0; round < scrollLimit && !stopRequested.current; round++) {
          const { page } = await extractCurrent(recordLimit);
          setNotice(`Scroll ${round}: ${page.records.length} items`);
          if (page.records.length >= recordLimit) break;
          idle = page.records.length === last ? idle + 1 : 0;
          if (idle >= 2) break;
          last = page.records.length;
          await studioHost.call("scrollStep");
          await sleep(delay);
        }
        const { page } = await extractCurrent(recordLimit);
        pages.push({ url: page.url, records: page.records, retrieved_at: new Date().toISOString() });
      } else if (pageMode === "detail_links") {
        const found = await studioHost.call<{ urls: string[]; error: string | null }>("links", [detailCss]);
        if (found.error) throw new Error(found.error);
        let current = (await studioHost.call<PageInfo>("pageInfo")).url;
        for (const url of found.urls.slice(0, recordLimit)) {
          if (stopRequested.current) break;
          const allowed = await call<{ allowed: boolean; reason: string | null; skippable: boolean }>("scrape.check_url", { preset: draftPreset(), url, scope_url: scopeUrl, purpose });
          if (!allowed.allowed && !allowed.skippable) throw new Error(allowed.reason ?? "The site's signals do not allow this collection");
          if (!allowed.allowed) continue;
          await sleep(delay);
          await studioHost.navigate(url);
          await waitForPage(current);
          current = url;
          const { page } = await extractCurrent(1);
          pages.push({ url: page.url, records: page.records.slice(0, 1), retrieved_at: new Date().toISOString() });
          setNotice(`Detail page ${pages.length} of ${Math.min(found.urls.length, recordLimit)}`);
        }
      } else {
        const seen = new Set<string>();
        const pageLimit = runMode === "test" ? 1 : Math.min(maxPages, base.request_limits.max_pages_default);
        for (let index = 0; index < pageLimit && !stopRequested.current; index++) {
          const { page } = await extractCurrent(recordLimit);
          if (seen.has(page.url)) break;
          seen.add(page.url);
          pages.push({ url: page.url, records: page.records, retrieved_at: new Date().toISOString() });
          setNotice(`Page ${pages.length}: ${page.records.length} records`);
          if (pageMode !== "next_link" || page.records.length === 0 || !page.next_url || index + 1 >= pageLimit) break;
          await checkUrl(page.next_url, scopeUrl, true);
          setLoaded(null);
          await sleep(delay);
          await studioHost.navigate(page.next_url);
          await waitForPage(page.url);
        }
      }
      if (runMode === "test") pages.forEach((page, index) => (page.records = page.records.slice(0, Math.max(0, recordLimit - pages.slice(0, index).reduce((n, p) => n + p.records.length, 0)))));
      if (pages.length) await stage(runMode, pages);
      setNotice(stopRequested.current ? "Stopped. Pages collected so far were staged." : `Collected ${pages.length} page(s).`);
    } catch (err) {
      fail(err);
    } finally {
      setRunning(null);
    }
  };

  const draft = draftPreset();
  const canExtract = !!scopeUrl && !!recordRoot && fields.length > 0 && acknowledged && !!purpose && !running && !(job && isActive(job.state));
  const saved = (presets.data ?? []).some((p) => p.id === draft?.id && p.version === version);

  if (!desktop) {
    return (
      <div className="panel">
        <h2>Scrape Studio needs the desktop app</h2>
        <p className="muted">
          Rendered pages load in DataForge's own embedded WebView, which only exists in the desktop window. In a browser you can still edit selectors and run HTTP tests from the Scraping tab.
        </p>
        <button type="button" className="btn" onClick={() => navigate("scraping")}>
          Open Scraping
        </button>
      </div>
    );
  }

  return (
    <div className="studio">
      <aside className="studio-rail" aria-label="Studio controls">
        <label className="field">
          <span>Base preset</span>
          <select value={base ? `${base.id}@${base.version}` : ""} onChange={(e) => setBaseKey(e.target.value)}>
            {bases.map((p) => (
              <option key={`${p.id}@${p.version}`} value={`${p.id}@${p.version}`}>
                {p.display_name} — {p.version}
              </option>
            ))}
          </select>
        </label>
        <div className="path-input">
          <label className="field">
            <span>Page URL</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/listings" spellCheck={false} />
          </label>
          <button type="button" className="btn btn-primary" disabled={!url || !base} onClick={() => void load()}>
            Load
          </button>
        </div>
        {scopeUrl && (
          <div className="row-actions">
            <button type="button" className="btn btn-small" onClick={() => void studioHost.control("back")}>Back</button>
            <button type="button" className="btn btn-small" onClick={() => void studioHost.control("reload")}>Reload</button>
            <button type="button" className="btn btn-small" onClick={() => void studioHost.control("stop")}>Stop</button>
            <span className="muted small">{loaded ? `${loaded.state === "finished" ? "Loaded" : "Loading"} ${loaded.url}` : ""}</span>
          </div>
        )}
        <p className="muted small">
          Strategy: embedded WebView (visible, no separate browser). Scope: {hostOf(scopeUrl ?? url) ?? "—"} only. Limits: {base?.request_limits.max_pages_default} pages, {base?.request_limits.max_records_default} records,
          at least {Math.max(base?.request_limits.min_delay_ms ?? 0, 1000)} ms between pages.
        </p>
        {pageInfo && pageInfo.inaccessible_frames > 0 && <p className="note note-warning small">{pageInfo.inaccessible_frames} cross-origin frame(s) are unavailable and will not be inspected.</p>}
        {structured && (
          <div className="note small">
            <p>
              This page carries structured data:{" "}
              {Object.entries(structured.types)
                .map(([type, count]) => `${type} ×${count}`)
                .join(", ")}
              . Structured data survives redesigns better than selectors.
            </p>
            <button type="button" className="btn btn-small" disabled={!acknowledged || !purpose || !structuredPreset} onClick={() => void testStructured()} title={acknowledged ? undefined : "Confirm authorization below first"}>
              Use structured data (test 10 records)
            </button>
          </div>
        )}

        <h3 className="section-label">1. Repeated item</h3>
        <div className="row-actions">
          <button type="button" className="btn btn-small" disabled={!scopeUrl || mode !== "none"} onClick={() => void startPick("repeated")}>
            Pick repeated item
          </button>
          {mode !== "none" && (
            <button type="button" className="btn btn-small btn-danger" onClick={() => void cancelPick()}>
              Cancel pick
            </button>
          )}
        </div>
        <label className="field">
          <span>Record root {rootCount !== null && <span className="muted">· matches {rootCount}</span>}</span>
          <input className="code" value={recordRoot} onChange={(e) => setRecordRoot(e.target.value)} onBlur={(e) => void countRoot(e.target.value)} spellCheck={false} />
        </label>

        <h3 className="section-label">2. Fields</h3>
        <button type="button" className="btn btn-small" disabled={!scopeUrl || !recordRoot || mode !== "none"} onClick={() => void startPick("element")}>
          Pick element
        </button>
        {fields.map((field, index) => (
          <div key={index} className="field-card">
            <div className="field-card-row">
              <input aria-label="Field name" value={field.key} onChange={(e) => setFields(fields.map((f, i) => (i === index ? { ...f, key: slug(e.target.value) } : f)))} />
              <select
                aria-label="Field type"
                value={field.type}
                onChange={(e) => {
                  const type = e.target.value as Field["type"];
                  setFields(fields.map((f, i) => (i === index ? { ...f, type, transforms: TRANSFORM_DEFAULTS[type] } : f)));
                }}
              >
                <option value="string">text</option>
                <option value="url">url</option>
                <option value="decimal">decimal</option>
                <option value="integer">integer</option>
              </select>
              <button type="button" className="icon-btn-plain" aria-label={`Remove ${field.key}`} onClick={() => setFields(fields.filter((_, i) => i !== index))}>
                ✕
              </button>
            </div>
            <input
              className="code"
              aria-label="Selector relative to record root"
              value={field.selectors[0].css ?? ""}
              onChange={(e) => setFields(fields.map((f, i) => (i === index ? { ...f, selectors: [{ ...f.selectors[0], css: e.target.value }, ...f.selectors.slice(1)] } : f)))}
              spellCheck={false}
            />
            {field.selectors.length > 1 && (
              <p className="muted small">
                Fallbacks if the selector stops matching: {field.selectors.slice(1).map((s) => <code key={s.xpath}>{s.xpath}</code>).reduce<ReactNode[]>((all, node, i) => (i ? [...all, " then ", node] : [node]), [])}
              </p>
            )}
            <div className="field-card-row small">
              <label className="toggle">
                <input type="checkbox" checked={field.required} onChange={(e) => setFields(fields.map((f, i) => (i === index ? { ...f, required: e.target.checked } : f)))} /> required
              </label>
              <select
                aria-label="Extract"
                value={field.selectors[0].attribute ?? "text"}
                onChange={(e) => setFields(fields.map((f, i) => (i === index ? { ...f, selectors: f.selectors.map(({ attribute: _a, ...rest }) => ({ ...rest, ...(e.target.value === "text" ? {} : { attribute: e.target.value }) })) } : f)))}
              >
                {["text", "href", "src", "alt", "title", "datetime", "aria-label", "data-testid"].map((a) => (
                  <option key={a}>{a}</option>
                ))}
              </select>
              <span className="muted">{field.transforms.join(", ")}</span>
            </div>
          </div>
        ))}

        <h3 className="section-label">3. Pagination</h3>
        <label className="field">
          <span>Mode</span>
          <select value={pageMode} onChange={(e) => setPageMode(e.target.value as typeof pageMode)}>
            <option value="none">Single page</option>
            <option value="next_link">Next-page link</option>
            <option value="infinite_scroll">Infinite scroll</option>
            <option value="detail_links">Detail links (one record per item page)</option>
          </select>
        </label>
        {pageMode === "next_link" && (
          <>
            <div className="row-actions">
              <button type="button" className="btn btn-small" disabled={!scopeUrl || mode !== "none"} onClick={() => void startPick("next")}>
                Pick next page
              </button>
              <label className="field inline small">
                <span>Max pages</span>
                <input type="number" min={1} max={base?.request_limits.max_pages_default} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} />
              </label>
            </div>
            <input className="code" aria-label="Next page selector" value={nextCss} onChange={(e) => setNextCss(e.target.value)} placeholder="Next-page link selector" spellCheck={false} />
          </>
        )}
        {pageMode === "infinite_scroll" && (
          <label className="field inline small">
            <span>Max scrolls</span>
            <input type="number" min={1} max={100} value={maxScrolls} onChange={(e) => setMaxScrolls(Math.min(100, Number(e.target.value)))} />
          </label>
        )}
        {pageMode === "detail_links" && (
          <>
            <button type="button" className="btn btn-small" disabled={!scopeUrl || mode !== "none"} onClick={() => void startPick("detail")}>
              Pick detail link
            </button>
            <input className="code" aria-label="Detail link selector" value={detailCss} onChange={(e) => setDetailCss(e.target.value)} placeholder="Item link selector" spellCheck={false} />
            <p className="muted small">Record root and fields apply to each detail page. Only links inside this preset's scope are opened.</p>
          </>
        )}

        <h3 className="section-label">4. Test, save, run</h3>
        <div className="split tight">
          <label className="field">
            <span>Preset name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Version</span>
            <input value={version} onChange={(e) => setVersion(e.target.value)} />
          </label>
        </div>
        <label className="toggle block small">
          <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> I am authorized to collect and use this data and accept the site's terms.
        </label>
        <label className="field">
          <span>Purpose of this collection</span>
          <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
            {PURPOSES.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <div className="row-actions">
          <button type="button" className="btn btn-primary" disabled={!canExtract} onClick={() => void collect("test")}>
            Test 10 records
          </button>
          <button type="button" className="btn" disabled={!recordRoot || fields.length === 0 || saved} onClick={() => void save()} title={saved ? "This version is saved; bump the version to save changes" : undefined}>
            {saved ? "Saved" : "Save custom preset"}
          </button>
          {running === "full" ? (
            <button type="button" className="btn btn-danger" onClick={() => (stopRequested.current = true)}>
              Stop run
            </button>
          ) : (
            <button type="button" className="btn" disabled={!canExtract || !saved} onClick={() => void collect("full")} title={saved ? undefined : "Save the preset and pass a test first"}>
              Start full run
            </button>
          )}
        </div>
        <ErrorNote message={error} />
        {notice && (
          <p className="note small" role="status">
            {notice}
          </p>
        )}
        {jobId && job?.state !== "completed" && <JobProgress job={job} />}
        {job?.result && <ScrapeResult result={job.result} onOpenDataset={(id) => navigate("datasets", { datasetId: id })} />}
      </aside>
      <div className="studio-preview" ref={previewRef}>
        {!scopeUrl && (
          <div className="studio-empty">
            <strong>No page loaded</strong>
            <span className="muted">Enter a permitted URL and choose Load. The page renders here, inside DataForge.</span>
          </div>
        )}
      </div>
    </div>
  );
}
