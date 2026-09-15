import { useCallback, useEffect, useRef, useState } from "react";
import type { Navigate } from "../../app/App.tsx";
import { call, isTauri } from "../../lib/ipc.ts";
import { studioHost, type Bounds } from "../../lib/desktop.ts";
import { useJob, useService } from "../../lib/hooks.ts";
import { isActive } from "../../lib/format.ts";
import { ErrorNote, JobProgress } from "../../components/ui.tsx";
import { ScrapeResult } from "../scraping/Scraping.tsx";

type Selector = { css: string; attribute?: string };
type Field = { key: string; type: "string" | "url" | "decimal" | "integer"; required: boolean; selectors: Selector[]; transforms: string[] };
type Preset = Record<string, any> & { id: string; version: string; display_name: string; strategy: { preferred: string; allowed: string[] }; request_limits: Record<string, number> };
type Pick = { mode: string; tag: string; text: string; attributes: Record<string, string>; suggested_attribute: string | null; selector: string; relative_selector?: string | null; inside_record_root?: boolean; repeated?: { selector: string; count: number } | null };
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
  const bases = (presets.data ?? []).filter((p) => p.strategy.allowed.includes("webview") && p.status !== "disabled" && p.status !== "deprecated");
  const [baseKey, setBaseKey] = useState("");
  const base = bases.find((p) => `${p.id}@${p.version}` === baseKey) ?? bases[0];

  const [url, setUrl] = useState("");
  const [scopeUrl, setScopeUrl] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<{ url: string; state: string } | null>(null);
  const [pageInfo, setPageInfo] = useState<PageInfo | null>(null);
  const [mode, setMode] = useState<"none" | "element" | "repeated" | "next">("none");
  const [recordRoot, setRecordRoot] = useState("");
  const [rootCount, setRootCount] = useState<number | null>(null);
  const [fields, setFields] = useState<Field[]>([]);
  const [nextCss, setNextCss] = useState("");
  const [name, setName] = useState("my_cards");
  const [version, setVersion] = useState("1.0.0");
  const [maxPages, setMaxPages] = useState(3);
  const [acknowledged, setAcknowledged] = useState(false);
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
    return rect ? { x: rect.left, y: rect.top, width: rect.width, height: rect.height } : null;
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
      pagination: nextCss ? { type: "next_link", next: { css: nextCss, attribute: "href" }, stop_conditions: ["max_pages", "max_records", "repeated_canonical_url", "no_new_records"] } : { type: "none" },
      validation: { ...clean.validation, unique_by: fields.some((f) => f.type === "url") ? [fields.find((f) => f.type === "url")!.key] : [] },
    };
  }, [base, name, version, recordRoot, fields, nextCss]);

  const fail = (err: unknown) => setError(err instanceof Error ? err.message : String(err));

  const checkUrl = async (target: string, scope: string) => {
    const result = await call<{ allowed: boolean; reason: string | null }>("scrape.check_url", { preset: draftPreset(), url: target, scope_url: scope });
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

  const startPick = async (next: "element" | "repeated" | "next") => {
    try {
      setError(null);
      await studioHost.call("setMode", [next, next === "element" ? recordRoot : null]);
      setMode(next);
      setNotice(next === "repeated" ? "Click one repeated card or row in the page." : next === "next" ? "Click the next-page link." : "Click the value to extract inside a record.");
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
          selectors: [{ css: css === ":scope" ? "" : css, ...(pick.suggested_attribute ? { attribute: pick.suggested_attribute } : {}) }],
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
    const page = await studioHost.call<Extracted>("extract", [{ record_root: recordRoot, fields, next_css: nextCss || null, limit }]);
    if (page.error) throw new Error(page.error);
    return { page, info };
  };

  const stage = async (runMode: "test" | "full", pages: { url: string; records: Record<string, string>[]; retrieved_at: string }[]) => {
    const result = await call<{ job_id: string }>("scrape.stage_rendered", { preset: draftPreset(), pages, run_mode: runMode, policy_acknowledgement: acknowledged, dataset_name: `${name} (Scrape Studio)` });
    setJobId(result.job_id);
  };

  const test = async () => {
    try {
      setError(null);
      setRunning("test");
      const { page } = await extractCurrent(10);
      await stage("test", [{ url: page.url, records: page.records, retrieved_at: new Date().toISOString() }]);
    } catch (err) {
      fail(err);
    } finally {
      setRunning(null);
    }
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

  const fullRun = async () => {
    if (!base || !scopeUrl) return;
    stopRequested.current = false;
    setRunning("full");
    setError(null);
    const pages: { url: string; records: Record<string, string>[]; retrieved_at: string }[] = [];
    const seen = new Set<string>();
    const delay = Math.max(Number(base.request_limits.min_delay_ms) || 0, 1000);
    const pageLimit = Math.min(maxPages, base.request_limits.max_pages_default);
    try {
      for (let index = 0; index < pageLimit && !stopRequested.current; index++) {
        const { page } = await extractCurrent(base.request_limits.max_records_default);
        if (seen.has(page.url)) break;
        seen.add(page.url);
        pages.push({ url: page.url, records: page.records, retrieved_at: new Date().toISOString() });
        setNotice(`Page ${pages.length}: ${page.records.length} records`);
        if (page.records.length === 0 || !page.next_url || index + 1 >= pageLimit) break;
        await checkUrl(page.next_url, scopeUrl);
        setLoaded(null);
        await sleep(delay);
        await studioHost.navigate(page.next_url);
        for (let waited = 0; waited < 30000; waited += 250) {
          await sleep(250);
          const info = await studioHost.call<PageInfo>("pageInfo").catch(() => null);
          if (info && info.url !== page.url && info.ready_state === "complete") break;
        }
      }
      if (pages.length) await stage("full", pages);
      setNotice(stopRequested.current ? "Stopped. Pages collected so far were staged." : `Collected ${pages.length} page(s).`);
    } catch (err) {
      fail(err);
    } finally {
      setRunning(null);
    }
  };

  const draft = draftPreset();
  const canExtract = !!scopeUrl && !!recordRoot && fields.length > 0 && acknowledged && !running && !(job && isActive(job.state));
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
              value={field.selectors[0].css}
              onChange={(e) => setFields(fields.map((f, i) => (i === index ? { ...f, selectors: [{ ...f.selectors[0], css: e.target.value }] } : f)))}
              spellCheck={false}
            />
            <div className="field-card-row small">
              <label className="toggle">
                <input type="checkbox" checked={field.required} onChange={(e) => setFields(fields.map((f, i) => (i === index ? { ...f, required: e.target.checked } : f)))} /> required
              </label>
              <select
                aria-label="Extract"
                value={field.selectors[0].attribute ?? "text"}
                onChange={(e) => setFields(fields.map((f, i) => (i === index ? { ...f, selectors: [{ css: f.selectors[0].css, ...(e.target.value === "text" ? {} : { attribute: e.target.value }) }] } : f)))}
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
        <div className="row-actions">
          <button type="button" className="btn btn-small" disabled={!scopeUrl || mode !== "none"} onClick={() => void startPick("next")}>
            Pick next page
          </button>
          <label className="field inline small">
            <span>Max pages</span>
            <input type="number" min={1} max={base?.request_limits.max_pages_default} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} />
          </label>
        </div>
        <input className="code" aria-label="Next page selector" value={nextCss} onChange={(e) => setNextCss(e.target.value)} placeholder="No pagination" spellCheck={false} />

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
        <div className="row-actions">
          <button type="button" className="btn btn-primary" disabled={!canExtract} onClick={() => void test()}>
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
            <button type="button" className="btn" disabled={!canExtract || !saved} onClick={() => void fullRun()} title={saved ? undefined : "Save the preset and pass a test first"}>
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
