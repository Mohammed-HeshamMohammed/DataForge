import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Navigate } from "../../app/App.tsx";
import { call, isTauri } from "../../lib/ipc.ts";
import { copyText, getZoom, studioHost, type Bounds } from "../../lib/desktop.ts";
import { useJob, useService } from "../../lib/hooks.ts";
import { isActive } from "../../lib/format.ts";
import { ErrorNote, JobProgress } from "../../components/ui.tsx";
import { CustomSelect } from "../../components/CustomSelect.tsx";
import { ScrapeResult } from "../scraping/Scraping.tsx";
import { PURPOSES, SOURCES, enrichSiteCatalog, formatSiteCategory, type SiteCatalogEntry, type SiteCatalogVariant, type SourceKind } from "../scraping/sources.ts";

type Selector = { css?: string; xpath?: string; attribute?: string };
type Field = { key: string; type: "string" | "url" | "decimal" | "integer"; required: boolean; selectors: Selector[]; transforms: string[] };
type Preset = Record<string, any> & { id: string; version: string; display_name: string; strategy: { preferred: string; allowed: string[] }; request_limits: Record<string, number> };
type Pick = { fallback_xpaths?: string[]; mode: string; tag: string; text: string; attributes: Record<string, string>; suggested_attribute: string | null; selector: string; relative_selector?: string | null; inside_record_root?: boolean; repeated?: { selector: string; count: number } | null };
type Extracted = { url: string; candidates: number; records: Record<string, string>[]; next_url: string | null; error: string | null };
type PageInfo = { url: string; title: string; favicon_url?: string | null; thumbnail_url?: string | null; ready_state: string; challenge_detected: boolean; password_fields: number; inaccessible_frames: number };
type BrowserHistoryEntry = { url: string; title: string; favicon_url: string; thumbnail_url: string | null; visited_at: number };
type SiteChoice = { key: string; site: SiteCatalogEntry; variant: SiteCatalogVariant };
type NetworkResponse = { url: string; data: unknown };
type Detection = {
  source: SourceKind;
  preset_id: string;
  preset_version: string;
  confidence: "high" | "medium";
  reason: string;
  content_type: string;
  created_preset: boolean;
  requires_rendered?: boolean;
  site_name?: string;
  site_category?: string;
  suggested_fields?: string[];
  recommended_method?: string;
  detection_limited?: boolean;
};
type FlowSuggestion = {
  kind: "next_link" | "load_more" | "infinite_scroll" | "none";
  next_css: string | null;
  load_more_css: string | null;
  can_scroll: boolean;
  reason: string;
};

function BrowserIcon({ name }: { name: "back" | "forward" | "reload" | "stop" | "home" | "history" | "up" | "down" | "plus" }) {
  const paths: Record<typeof name, ReactNode> = {
    back: <path d="m15 18-6-6 6-6M9 12h10" />,
    forward: <path d="m9 18 6-6-6-6m6 6H5" />,
    reload: <path d="M20 11a8 8 0 1 0-2.34 5.66M20 4v7h-7" />,
    stop: <path d="M7 7h10v10H7z" />,
    home: <path d="m4 11 8-7 8 7v9h-6v-6h-4v6H4z" />,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" /></>,
    up: <path d="m6 15 6-6 6 6" />,
    down: <path d="m6 9 6 6 6-6" />,
    plus: <path d="M12 5v14M5 12h14" />,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

const STUDIO_METHOD_HINTS: Partial<Record<SourceKind, string>> = {
  website: "Visible page picking, structured data, article text, or page JSON",
  sitemap: "Discover pages from XML sitemaps",
  feed: "Collect RSS or Atom entries",
  crawl: "Follow permitted links within one site",
  api: "JSON, GraphQL, CKAN, Socrata, and other read-only APIs",
  xml: "XML documents and read-only SOAP responses",
  repository: "OAI-PMH and research repositories",
  documents: "PDFs and linked CSV, JSONL, XLSX, XML, Parquet, DOCX, ZIP, or GZIP files",
  archive: "Wayback Machine and Common Crawl captures",
  bulk: "Downloaded Web Data Commons corpora",
};

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

function firstRecordArray(value: unknown, depth = 0): Record<string, unknown>[] {
  if (Array.isArray(value) && value.every((item) => typeof item === "object" && item !== null && !Array.isArray(item))) return value as Record<string, unknown>[];
  if (value && typeof value === "object" && depth < 4) {
    for (const nested of Object.values(value)) {
      const found = firstRecordArray(nested, depth + 1);
      if (found.length) return found;
    }
  }
  return [];
}

function browserTarget(value: string): string {
  const input = value.trim();
  if (!input) return "";
  if (/^https?:\/\//i.test(input)) return input;
  if (/^[a-z0-9.-]+\.[a-z]{2,}(?:\/.*)?$/i.test(input)) return `https://${input}`;
  return `https://www.google.com/search?q=${encodeURIComponent(input)}`;
}

function safePageAsset(candidate: string | null | undefined, pageUrl: string): string | null {
  if (!candidate) return null;
  try {
    const asset = new URL(candidate, pageUrl);
    return asset.protocol === "https:" || asset.protocol === "http:" ? asset.href : null;
  } catch {
    return null;
  }
}

function faviconFor(pageUrl: string, supplied?: string | null): string {
  const safe = safePageAsset(supplied, pageUrl);
  if (safe) return safe;
  try {
    return new URL("/favicon.ico", pageUrl).href;
  } catch {
    return "";
  }
}

function historyTime(value: number): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(value);
}

export function isInternalAutomationHost(host: string): boolean {
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".internal") || host.endsWith(".local") || host.endsWith(".test")) return true;
  if (/^127(?:\.\d{1,3}){3}$/.test(host) || host === "::1") return true;
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!match) return false;
  const [, a, b] = match.map(Number);
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

function playwrightCompatibilityScript(pageUrl: string, root: string, fields: Field[]): string {
  const definitions = fields.map((field) => ({ key: field.key, css: field.selectors[0]?.css ?? "", attribute: field.selectors[0]?.attribute ?? null }));
  return `// Authorized internal systems only. No stealth, CAPTCHA handling, proxy rotation, or login automation.\nconst { chromium } = require("playwright");\n\n(async () => {\n  const browser = await chromium.launch({ headless: true });\n  const page = await browser.newPage();\n  await page.goto(${JSON.stringify(pageUrl)}, { waitUntil: "domcontentloaded" });\n  const rows = await page.locator(${JSON.stringify(root)}).evaluateAll((items, fields) => items.map((item) => Object.fromEntries(fields.map((field) => {\n    const node = field.css ? item.querySelector(field.css) : item;\n    return [field.key, node ? (field.attribute ? node.getAttribute(field.attribute) : node.textContent?.trim()) : null];\n  }))), ${JSON.stringify(definitions)});\n  console.log(JSON.stringify(rows, null, 2));\n  await browser.close();\n})();\n`;
}

function seleniumCompatibilityScript(pageUrl: string, root: string, fields: Field[]): string {
  const definitions = fields.map((field) => ({ key: field.key, css: field.selectors[0]?.css ?? "", attribute: field.selectors[0]?.attribute ?? null }));
  return `# Authorized internal systems only. No stealth, CAPTCHA handling, proxy rotation, or login automation.\nimport json\nfrom selenium import webdriver\nfrom selenium.webdriver.common.by import By\n\ndriver = webdriver.Edge()\ntry:\n    driver.get(${JSON.stringify(pageUrl)})\n    fields = json.loads(${JSON.stringify(JSON.stringify(definitions))})\n    rows = []\n    for item in driver.find_elements(By.CSS_SELECTOR, ${JSON.stringify(root)}):\n        row = {}\n        for field in fields:\n            try:\n                node = item.find_element(By.CSS_SELECTOR, field["css"]) if field["css"] else item\n                row[field["key"]] = node.get_attribute(field["attribute"]) if field["attribute"] else node.text.strip()\n            except Exception:\n                row[field["key"]] = None\n        rows.append(row)\n    print(json.dumps(rows, ensure_ascii=False, indent=2))\nfinally:\n    driver.quit()\n`;
}

function printableRecord(record: Record<string, unknown>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(record).slice(0, 100)) {
    const base = slug(key);
    let normalized = base;
    for (let suffix = 2; normalized in result; suffix++) normalized = `${base}_${suffix}`;
    result[normalized] = typeof value === "string" ? value : typeof value === "number" || typeof value === "boolean" ? String(value) : JSON.stringify(value);
  }
  return result;
}

export function Studio({ navigate, initialUrl = "", active = true }: { navigate: Navigate; initialUrl?: string; active?: boolean }) {
  const presets = useService<Preset[]>("preset.list");
  const siteCatalog = useService<SiteCatalogEntry[]>("scrape.site_catalog");
  const supportedSites = useMemo(() => enrichSiteCatalog(siteCatalog.data), [siteCatalog.data]);
  const siteChoices = useMemo<SiteChoice[]>(() => supportedSites.flatMap((site) => (site.variants ?? [{ id: "default", label: "Default", domain: site.domains[0], example_url: site.example_url }]).map((variant) => ({ key: `${site.site_id}::${variant.id}`, site, variant }))), [supportedSites]);
  const homeChoices = useMemo(() => {
    const wanted = ["Amazon", "eBay", "Google Maps", "Indeed", "Zillow", "YouTube", "GitHub", "Reddit"];
    return wanted.flatMap((name) => {
      const choices = siteChoices.filter((choice) => choice.site.site_name === name);
      return choices.find((choice) => choice.variant.id === "eg") ?? choices[0] ?? [];
    });
  }, [siteChoices]);
  const suggestedHomeChoices = useMemo(() => {
    const featured = new Set(homeChoices.map((choice) => choice.site.site_id));
    return supportedSites
      .filter((site) => !featured.has(site.site_id))
      .slice(0, 6)
      .map((site) => {
        const variants = site.variants ?? [{ id: "default", label: "Default", domain: site.domains[0], example_url: site.example_url }];
        const variant = variants.find((entry) => entry.id === "eg") ?? variants[0];
        return { key: `${site.site_id}::${variant.id}`, site, variant };
      });
  }, [homeChoices, supportedSites]);
  const bases = (presets.data ?? []).filter((p) => p.strategy.allowed.includes("webview") && p.status !== "disabled" && p.status !== "deprecated" && !((p.extraction as { mode?: string })?.mode ?? "").match(/structured_data|article|document_tables/));
  const structuredPreset = (presets.data ?? []).find((p) => p.id === "generic.structured_data");
  const [structured, setStructured] = useState<{ url: string; types: Record<string, number>; suggested_type: string | null } | null>(null);
  const [recommendation, setRecommendation] = useState<Detection | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [flowSuggestion, setFlowSuggestion] = useState<FlowSuggestion | null>(null);
  const [baseKey, setBaseKey] = useState("");
  const base = bases.find((p) => `${p.id}@${p.version}` === baseKey) ?? bases[0];

  const [url, setUrl] = useState(initialUrl);
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [scopeUrl, setScopeUrl] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<{ url: string; state: string } | null>(null);
  const [pageInfo, setPageInfo] = useState<PageInfo | null>(null);
  const [browserHome, setBrowserHome] = useState(!initialUrl);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [browserHistory, setBrowserHistory] = useState<BrowserHistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [homeAddress, setHomeAddress] = useState("");
  const [allHomeSites, setAllHomeSites] = useState(false);
  const visibleHistory = useMemo(() => {
    const terms = historyQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return [...browserHistory].reverse().filter((entry) => !terms.length || terms.every((term) => `${entry.title} ${entry.url}`.toLowerCase().includes(term)));
  }, [browserHistory, historyQuery]);
  const [mode, setMode] = useState<"none" | "element" | "repeated" | "next" | "detail" | "load_more">("none");
  const [recordRoot, setRecordRoot] = useState("");
  const [rootCount, setRootCount] = useState<number | null>(null);
  const [fields, setFields] = useState<Field[]>([]);
  const [nextCss, setNextCss] = useState("");
  const [pageMode, setPageMode] = useState<"none" | "next_link" | "infinite_scroll" | "detail_links" | "load_more">("none");
  const [detailCss, setDetailCss] = useState("");
  const [loadMoreCss, setLoadMoreCss] = useState("");
  const [maxClicks, setMaxClicks] = useState(10);
  const [maxScrolls, setMaxScrolls] = useState(10);
  const [captureNetwork, setCaptureNetwork] = useState(false);
  const [manualSignIn, setManualSignIn] = useState(false);
  const [sessionExpiresAt, setSessionExpiresAt] = useState<number | null>(null);
  const [sessionSecondsLeft, setSessionSecondsLeft] = useState(0);
  const [networkCount, setNetworkCount] = useState(0);
  const [downloaded, setDownloaded] = useState<{ path: string; url: string } | null>(null);
  const [name, setName] = useState("my_cards");
  const [version, setVersion] = useState("1.0.0");
  const [maxPages, setMaxPages] = useState(3);
  const [acknowledged, setAcknowledged] = useState(false);
  const settings = useService<{ default_purpose: string }>("settings.get");
  const [purpose, setPurpose] = useState("");
  useEffect(() => {
    if (!purpose && settings.data) setPurpose(settings.data.default_purpose);
  }, [settings.data, purpose]);
  useEffect(() => {
    const host = hostOf(url);
    const matchingChoice = host ? siteChoices.find((choice) => host === choice.variant.domain || host.endsWith(`.${choice.variant.domain}`)) : undefined;
    setSelectedSiteId(matchingChoice?.key ?? "");
  }, [siteChoices, url]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const stopRequested = useRef(false);
  const previewRef = useRef<HTMLDivElement>(null);
  const opened = useRef(false);
  const handledInitialUrl = useRef("");

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

  // A visited Studio stays mounted across tab switches and while its browser home is visible.
  // preserving page history, cookies, selections, and the in-progress extraction setup.
  useEffect(() => {
    if (!desktop || !opened.current) return;
    const visible = active && !browserHome && !historyOpen;
    void studioHost.setVisible(visible).then(() => {
      if (!visible) return;
      window.requestAnimationFrame(() => {
        const nextBounds = bounds();
        if (nextBounds) void studioHost.setBounds(nextBounds).catch(() => {});
      });
    }).catch(() => {});
  }, [active, browserHome, desktop, historyOpen]);

  useEffect(() => {
    if (!desktop) return;
    let unlisten: () => void = () => {};
    void studioHost
      .onEvent((event) => {
        if (event.type === "page_load") {
          const currentUrl = event.url ?? "";
          setLoaded({ url: currentUrl, state: event.event ?? "" });
          if (currentUrl) setUrl(currentUrl);
        }
        if (event.type === "navigation_blocked") setNotice(`Blocked navigation outside this preset's scope: ${event.url}`);
        if (event.type === "download_blocked") setNotice(`Blocked a download outside this preset's scope: ${event.url}`);
        if (event.type === "download" && event.success && event.path) setDownloaded({ path: event.path, url: event.url ?? "download" });
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

  // Detect the page's most likely continuation control after every completed navigation.
  useEffect(() => {
    if (!desktop || loaded?.state !== "finished") return;
    let cancelled = false;
    void studioHost.call<PageInfo>("pageInfo").then((info) => {
      if (cancelled) return;
      setPageInfo(info);
      const historyUrl = info.url || loaded.url;
      setBrowserHistory((current) => {
        const entry = {
          url: historyUrl,
          title: info.title || hostOf(historyUrl) || historyUrl,
          favicon_url: faviconFor(historyUrl, info.favicon_url),
          thumbnail_url: safePageAsset(info.thumbnail_url, historyUrl),
          visited_at: Date.now(),
        };
        if (current.at(-1)?.url === historyUrl) return [...current.slice(0, -1), entry];
        return [...current, entry].slice(-50);
      });
    }).catch(() => {});
    void studioHost
      .call<FlowSuggestion>("suggestFlow")
      .then((suggestion) => {
        if (cancelled) return;
        setFlowSuggestion(suggestion);
        setPageMode((current) => {
          if (current !== "none" || suggestion.kind === "none") return current;
          if (suggestion.kind === "next_link" && suggestion.next_css) setNextCss(suggestion.next_css);
          if (suggestion.kind === "load_more" && suggestion.load_more_css) setLoadMoreCss(suggestion.load_more_css);
          return suggestion.kind;
        });
      })
      .catch(() => {
        if (!cancelled) setFlowSuggestion(null);
      });
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
            : pageMode === "load_more" && loadMoreCss
              ? { type: "load_more", button: { css: loadMoreCss }, max_clicks: maxClicks, stop_conditions: ["max_records", "no_new_records", "max_duration"] }
            : pageMode === "detail_links" && detailCss
              ? { type: "detail_links", links: { css: detailCss, attribute: "href" }, stop_conditions: ["max_records", "repeated_canonical_url"] }
              : { type: "none" },
      validation: { ...clean.validation, unique_by: fields.some((f) => f.type === "url") ? [fields.find((f) => f.type === "url")!.key] : [] },
    };
  }, [base, name, version, recordRoot, fields, nextCss, pageMode, detailCss, loadMoreCss, maxClicks, maxScrolls]);

  const fail = (err: unknown) => setError(err instanceof Error ? err.message : String(err));

  /** Scope check for browsing; with `collecting`, also the site's robots.txt, TDMRep, and AIPREF signals for the purpose. */
  const checkUrl = async (target: string, scope: string, collecting = false) => {
    const result = await call<{ allowed: boolean; reason: string | null; skippable: boolean }>("scrape.check_url", { preset: draftPreset(), url: target, scope_url: scope, ...(collecting ? { purpose } : {}) });
    if (!result.allowed) throw new Error(result.reason ?? "URL is not allowed");
  };

  const load = async (target = url) => {
    try {
      setError(null);
      setNotice(null);
      const normalized = target.trim();
      const host = hostOf(normalized);
      if (!host) throw new Error("Enter a full URL, for example https://example.com/listings");
      await checkUrl(normalized, normalized);
      const b = bounds();
      if (!b) throw new Error("Preview area is not ready");
      const allowed = base?.url_scope?.user_supplied_host ? [host] : (base?.url_scope?.allowed_hosts as string[]);
      await studioHost.open(normalized, allowed, b);
      opened.current = true;
      setBrowserHome(false);
      setHistoryOpen(false);
      setUrl(normalized);
      setScopeUrl(normalized);
      setPageInfo(null);
      setFlowSuggestion(null);
      setCaptureNetwork(false);
      setNetworkCount(0);
      setDownloaded(null);
      setManualSignIn(false);
      setSessionExpiresAt(null);
      setSessionSecondsLeft(0);
    } catch (err) {
      fail(err);
    }
  };

  const endSignedInSession = useCallback(async (message = "Temporary signed-in session ended. Cookies and site storage were discarded.") => {
    await studioHost.close().catch(() => {});
    opened.current = false;
    setScopeUrl(null);
    setLoaded(null);
    setPageInfo(null);
    setManualSignIn(false);
    setSessionExpiresAt(null);
    setSessionSecondsLeft(0);
    setCaptureNetwork(false);
    setNetworkCount(0);
    setDownloaded(null);
    setStructured(null);
    setNotice(message);
  }, []);

  useEffect(() => {
    if (!manualSignIn || !sessionExpiresAt) return;
    const update = () => {
      const seconds = Math.max(0, Math.ceil((sessionExpiresAt - Date.now()) / 1000));
      setSessionSecondsLeft(seconds);
      if (seconds === 0) void endSignedInSession("Temporary signed-in session expired after 30 minutes and was securely closed.");
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [endSignedInSession, manualSignIn, sessionExpiresAt]);

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

  const startPick = async (next: "element" | "repeated" | "next" | "detail" | "load_more") => {
    try {
      setError(null);
      await studioHost.call("setMode", [next, next === "element" ? recordRoot : null]);
      setMode(next);
      setNotice(next === "repeated" ? "Click one repeated card or row in the page." : next === "next" ? "Click the next-page link." : next === "detail" ? "Click one item link that opens a detail page." : next === "load_more" ? "Click the button that loads more items." : "Click the value to extract inside a record.");
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
    } else if (pick.mode === "load_more") {
      setLoadMoreCss(pick.selector);
      setPageMode("load_more");
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

  const networkRecords = async (limit: number) => {
    const captured = await studioHost.call<{ responses: NetworkResponse[]; error: string | null }>("networkData");
    if (captured.error) throw new Error(captured.error);
    return captured.responses.flatMap((response) => firstRecordArray(response.data).map(printableRecord)).slice(0, limit);
  };

  const inspectNetwork = async () => {
    try {
      const records = await networkRecords(5000);
      if (!records.length) throw new Error("No JSON record arrays have been observed yet. Use the page so it loads its data, then try again.");
      const sample = records[0];
      const nextFields = Object.keys(sample).slice(0, 50).map((key, index): Field => ({
        key: slug(key), type: "string", required: index === 0, selectors: [{ css: "" }], transforms: ["trim", "collapse_whitespace"],
      }));
      setCaptureNetwork(true);
      setRecordRoot(":scope");
      setFields(nextFields);
      setPageMode("none");
      setRootCount(records.length);
      setNetworkCount(records.length);
      setNotice(`Found ${records.length} records in page JSON. Review the fields, then run a test.`);
    } catch (err) {
      fail(err);
    }
  };

  const analyzeAndLoad = async (target = url) => {
    const normalized = target.trim();
    if (!normalized) return;
    let detectionWarning: string | null = null;
    setDetecting(true);
    setError(null);
    const detectionRequest = call<Detection>("scrape.detect_url", { url: normalized, purpose });
    await load(normalized);
    try {
      const detected = await detectionRequest;
      setRecommendation(detected);
      await presets.reload();
    } catch (err) {
      setRecommendation(null);
      detectionWarning = `Automatic method detection was unavailable (${err instanceof Error ? err.message : String(err)}). The page can still be configured visually.`;
    } finally {
      setDetecting(false);
    }
    if (detectionWarning) setNotice(detectionWarning);
  };

  const showBrowserHome = async () => {
    setBrowserHome(true);
    setHistoryOpen(false);
    if (desktop && opened.current) await studioHost.setVisible(false).catch(() => {});
  };

  const showBrowserHistory = async () => {
    setBrowserHome(false);
    setHistoryOpen(true);
    if (desktop && opened.current) await studioHost.setVisible(false).catch(() => {});
  };

  const openBrowserTarget = async (target: string) => {
    const normalized = browserTarget(target);
    if (!normalized) return;
    setHomeAddress("");
    await analyzeAndLoad(normalized);
  };

  const openSiteChoice = async (choice: SiteChoice) => {
    setSelectedSiteId(choice.key);
    setUrl(choice.variant.example_url);
    setRecommendation(null);
    setNotice(`Opening and analyzing ${choice.site.site_name} — ${choice.variant.label}…`);
    await analyzeAndLoad(choice.variant.example_url);
  };

  // When automatic detection produces a visual preset, use its selectors as a head start.
  useEffect(() => {
    if (!recommendation || !presets.data) return;
    const recommended = presets.data.find((preset) => preset.id === recommendation.preset_id && preset.version === recommendation.preset_version);
    if (!recommended || recommendation.source !== "website") return;
    if (recommended.strategy.allowed.includes("webview")) setBaseKey(`${recommended.id}@${recommended.version}`);
    const extraction = recommended.extraction as { record_root?: Selector; fields?: Field[] } | undefined;
    const pagination = recommended.pagination as { type?: string; next?: Selector; button?: Selector; max_scrolls?: number; max_clicks?: number } | undefined;
    if (extraction?.record_root?.css) setRecordRoot(extraction.record_root.css);
    if (Array.isArray(extraction?.fields) && extraction.fields.length) setFields(extraction.fields);
    if (pagination?.type === "next_link" && pagination.next?.css) {
      setPageMode("next_link");
      setNextCss(pagination.next.css);
    } else if (pagination?.type === "load_more" && pagination.button?.css) {
      setPageMode("load_more");
      setLoadMoreCss(pagination.button.css);
      if (pagination.max_clicks) setMaxClicks(pagination.max_clicks);
    } else if (pagination?.type === "infinite_scroll") {
      setPageMode("infinite_scroll");
      if (pagination.max_scrolls) setMaxScrolls(pagination.max_scrolls);
    }
  }, [presets.data, recommendation]);

  useEffect(() => {
    if (!desktop || !active || !initialUrl || !base || !purpose || handledInitialUrl.current === initialUrl) return;
    handledInitialUrl.current = initialUrl;
    void analyzeAndLoad(initialUrl);
    // The URL was explicitly passed from automatic detection; load it once when Studio mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, desktop, initialUrl, base?.id, purpose]);

  const importDownloaded = async () => {
    if (!downloaded) return;
    try {
      const started = await call<{ job_id: string }>("dataset.import", { path: downloaded.path });
      for (let waited = 0; waited < 60000; waited += 250) {
        const importJob = await call<any>("job.get", { job_id: started.job_id });
        if (importJob.state === "completed") {
          navigate("datasets", { datasetId: String(importJob.result.dataset_id) });
          return;
        }
        if (importJob.state === "failed" || importJob.state === "cancelled") throw new Error(importJob.error || "The downloaded file could not be imported");
        await sleep(250);
      }
      throw new Error("The download import did not finish in time");
    } catch (err) {
      fail(err);
    }
  };

  const extractCurrent = async (limit: number): Promise<{ page: Extracted; info: PageInfo }> => {
    const info = await studioHost.call<PageInfo>("pageInfo");
    setPageInfo(info);
    if (info.challenge_detected) throw new Error("The page shows an access challenge (CAPTCHA or bot check). Collection stopped; DataForge never bypasses these.");
    if (info.password_fields > 0) {
      throw new Error(
        manualSignIn
          ? "Finish signing in in the visible page before collecting. DataForge never reads, stores, or submits your credentials."
          : "This page asks for a login. Enable temporary manual sign-in, sign in yourself, then collect only data you are authorized to access.",
      );
    }
    const page = captureNetwork
      ? { url: info.url, candidates: networkCount, records: await networkRecords(limit), next_url: null, error: null }
      : await studioHost.call<Extracted>("extract", [{ record_root: recordRoot, fields, next_css: pageMode === "next_link" ? nextCss || null : null, limit }]);
    if (page.error) throw new Error(page.error);
    return { page, info };
  };

  const stage = async (runMode: "test" | "full", pages: { url: string; records: Record<string, string>[]; retrieved_at: string }[]) => {
    const result = await call<{ job_id: string }>("scrape.stage_rendered", { preset: draftPreset(), pages, run_mode: runMode, policy_acknowledgement: acknowledged, purpose, dataset_name: `${name} (Scrape Studio)` });
    setJobId(result.job_id);
  };

  const save = async (): Promise<boolean> => {
    try {
      setError(null);
      const preset = draftPreset();
      const { errors } = await call<{ errors: string[] }>("preset.validate", { preset });
      if (errors.length) throw new Error(errors.join("; "));
      await call("preset.save_custom", { preset: { ...preset, strategy: { preferred: "webview", allowed: ["webview"] } } });
      setNotice(`Saved custom.local.${slug(name)}@${version}. Run a test with it before a full run.`);
      void presets.reload();
      return true;
    } catch (err) {
      fail(err);
      return false;
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
      } else if (pageMode === "load_more") {
        let last = -1;
        let idle = 0;
        const clickLimit = runMode === "test" ? Math.min(maxClicks, 3) : maxClicks;
        for (let round = 0; round < clickLimit && !stopRequested.current; round++) {
          const { page } = await extractCurrent(recordLimit);
          if (page.records.length >= recordLimit) break;
          idle = page.records.length === last ? idle + 1 : 0;
          if (idle >= 2) break;
          last = page.records.length;
          const clicked = await studioHost.call<{ clicked: boolean; error: string | null }>("click", [loadMoreCss]);
          if (!clicked.clicked) {
            if (clicked.error) setNotice(clicked.error);
            break;
          }
          setNotice(`Load more ${round + 1}: ${page.records.length} items`);
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
  const sourceInfo = SOURCES.find((source) => source.id === recommendation?.source);
  const recommendationUsesStudio = !!recommendation && recommendation.source === "website";
  const preview = async () => {
    if (!saved && !(await save())) return;
    await collect("test");
  };

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
        <header className="studio-guide-header">
          <p className="eyebrow">Scrape Studio</p>
          <h2>Collect from this page</h2>
          <p className="muted small">Open a page, point at one repeating item, then choose the information you want. DataForge handles the technical setup.</p>
        </header>
        <section className="studio-site-picker" aria-labelledby="studio-site-picker-heading">
          <label className="field">
            <span id="studio-site-picker-heading">Start with a supported website</span>
            <CustomSelect
              value={selectedSiteId}
              onChange={(siteKey) => {
                const choice = siteChoices.find((entry) => entry.key === siteKey);
                if (choice) void openSiteChoice(choice);
              }}
              placeholder="Choose a website or paste an address"
              disabled={!base || !purpose || detecting}
              searchable
              searchPlaceholder="Search Amazon Egypt, eBay UK, jobs…"
              options={siteChoices.map(({ key, site, variant }) => ({
                value: key,
                label: site.variants && site.variants.length > 1 ? `${site.site_name} — ${variant.label}` : site.site_name,
                description: variant.domain,
                group: formatSiteCategory(site.site_category),
              }))}
            />
          </label>
          {siteCatalog.error && <p className="note small" role="status">Using the built-in website list. Restart DataForge later to refresh it from the collection service.</p>}
          <p className="muted small">Choose a country or regional version. DataForge opens it immediately and starts automatic analysis.</p>
        </section>
        {recommendation && (
          <section className="studio-recommendation" aria-label="Recommended collection method">
            <span className="studio-recommendation-badge">Recommended</span>
            <strong>{recommendation.site_name ?? sourceInfo?.label ?? "Website"}</strong>
            <p className="small">{recommendation.reason}.</p>
            {!!recommendation.suggested_fields?.length && <p className="muted small">Suggested information: {recommendation.suggested_fields.join(", ")}.</p>}
            {!recommendationUsesStudio && <button type="button" className="btn btn-small" onClick={() => navigate("scraping", { source: recommendation.source })}>Use the recommended collector</button>}
            <details><summary>Technical detection details</summary><code>{recommendation.preset_id}@{recommendation.preset_version}</code> · {recommendation.confidence} confidence</details>
          </section>
        )}
        <section className="studio-steps" aria-labelledby="studio-steps-heading">
          <h3 id="studio-steps-heading">What should DataForge collect?</h3>
          <button type="button" className={`studio-step ${recordRoot ? "is-complete" : ""}`} disabled={!scopeUrl || mode !== "none"} onClick={() => void startPick("repeated")}>
            <span className="studio-step-number">{recordRoot ? "✓" : "1"}</span>
            <span><strong>{recordRoot ? "Repeated items selected" : "Select a repeated item"}</strong><small>{rootCount !== null ? `${rootCount} matching items found` : "Click one card, row, product, or result"}</small></span>
          </button>
          <button type="button" className={`studio-step ${fields.length ? "is-complete" : ""}`} disabled={!scopeUrl || !recordRoot || mode !== "none"} onClick={() => void startPick("element")}>
            <span className="studio-step-number">{fields.length ? "✓" : "2"}</span>
            <span><strong>{fields.length ? `${fields.length} field${fields.length === 1 ? "" : "s"} selected` : "Choose information"}</strong><small>Click a title, price, link, date, or other value</small></span>
          </button>
          {fields.length > 0 && <div className="studio-field-chips" aria-label="Selected fields">{fields.map((field, index) => <span key={`${field.key}-${index}`}>{field.key}<button type="button" aria-label={`Remove ${field.key}`} onClick={() => setFields(fields.filter((_, fieldIndex) => fieldIndex !== index))}>×</button></span>)}</div>}
          {mode !== "none" && <button type="button" className="btn btn-small btn-danger" onClick={() => void cancelPick()}>Cancel page selection</button>}
        </section>
        {scopeUrl && <div className="studio-smart-actions"><button type="button" className="btn btn-small" onClick={() => void inspectNetwork()}>Find data already loaded by the page</button>{structured && <button type="button" className="btn btn-small" disabled={!acknowledged || !purpose || !structuredPreset} onClick={() => void testStructured()}>Use the page's structured data</button>}</div>}
        {flowSuggestion && scopeUrl && <section className="studio-flow-card" aria-label="More pages recommendation"><div><strong>{flowSuggestion.kind === "next_link" ? "Next pages will be included" : flowSuggestion.kind === "load_more" ? "The Load more button will be used" : flowSuggestion.kind === "infinite_scroll" ? "Results will scroll automatically" : "This page is ready"}</strong><p className="muted small">{flowSuggestion.reason}</p></div>{flowSuggestion.kind !== "none" && <span className="status-pill success">Automatic</span>}</section>}
        {scopeUrl && (
          <details className="advanced-section studio-sign-in">
            <summary>Does this page require you to sign in?</summary>
            <label className="toggle block">
              <input
                type="checkbox"
                checked={manualSignIn}
                onChange={(event) => {
                  if (!event.target.checked) {
                    void endSignedInSession();
                    return;
                  }
                  setManualSignIn(true);
                  setSessionExpiresAt(Date.now() + 30 * 60 * 1000);
                  setNotice("Temporary manual sign-in enabled for 30 minutes. Complete the login and any MFA yourself in the visible page; DataForge never reads or fills credentials or one-time codes.");
                }}
              />
              Temporary manual sign-in
            </label>
            <p className="muted small">Uses this isolated Studio session only. You complete sign-in and MFA yourself; DataForge never reads credentials or codes.{manualSignIn && sessionSecondsLeft > 0 ? ` Session closes in ${Math.ceil(sessionSecondsLeft / 60)} minute(s).` : ""}</p>
            {manualSignIn && <button type="button" className="btn btn-small" onClick={() => void endSignedInSession()}>End session and discard sign-in</button>}
          </details>
        )}
        {downloaded && (
          <div className="note small">
            Download finished from {downloaded.url}. <button type="button" className="btn btn-small" onClick={() => void importDownloaded()}>Import downloaded file</button>
          </div>
        )}
        {pageInfo && pageInfo.inaccessible_frames > 0 && <p className="note note-warning small">{pageInfo.inaccessible_frames} cross-origin frame(s) are unavailable and will not be inspected.</p>}
        {pageInfo?.challenge_detected && (
          <div className="note note-warning small">
            <p>Collection stopped at a CAPTCHA or access challenge. DataForge will not solve or bypass it.</p>
            <button type="button" className="btn btn-small" onClick={() => navigate("scraping", { source: "api" })}>Try an official API instead</button>
          </div>
        )}
        <details className="advanced-section studio-advanced">
          <summary>Show technical setup</summary>
          <label className="field">
            <span>Collection method</span>
            <CustomSelect ariaLabel="Collection method" value="website" onChange={(method) => {
              if (method === "local_file") navigate("datasets");
              else if (method !== "website") navigate("scraping", { source: method as SourceKind });
            }} options={[
              ...SOURCES.map((source) => ({ value: source.id, label: source.label, description: STUDIO_METHOD_HINTS[source.id] })),
              { value: "local_file", label: "Import a local file" },
            ]} />
          </label>
          <label className="field">
            <span>Base preset</span>
            <CustomSelect
              value={base ? `${base.id}@${base.version}` : ""}
              onChange={setBaseKey}
              searchable={bases.length > 8}
              searchPlaceholder="Search presets"
              options={bases.map((preset) => ({ value: `${preset.id}@${preset.version}`, label: preset.display_name, description: preset.version }))}
            />
          </label>
          <p className="muted small">Embedded WebView · {hostOf(scopeUrl ?? url) ?? "—"} only · {base?.request_limits.max_pages_default} page limit · {base?.request_limits.max_records_default} record limit · {Math.max(base?.request_limits.min_delay_ms ?? 0, 1000)} ms minimum delay.</p>
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
        {scopeUrl && recordRoot && fields.length > 0 && isInternalAutomationHost(hostOf(scopeUrl) ?? "") && (
          <details className="advanced-section">
            <summary>Internal browser compatibility</summary>
            <p className="muted small">Copies a plain extraction script for an authorized localhost, private-network, or internal-test host. The scripts contain no login automation, stealth, CAPTCHA handling, or proxy rotation.</p>
            <div className="row-actions">
              <button type="button" className="btn btn-small" onClick={() => void copyText(playwrightCompatibilityScript(scopeUrl, recordRoot, fields)).then(() => setNotice("Playwright compatibility script copied."))}>Copy Playwright script</button>
              <button type="button" className="btn btn-small" onClick={() => void copyText(seleniumCompatibilityScript(scopeUrl, recordRoot, fields)).then(() => setNotice("Selenium compatibility script copied."))}>Copy Selenium script</button>
            </div>
          </details>
        )}
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
              <CustomSelect
                ariaLabel="Field type"
                value={field.type}
                onChange={(value) => {
                  const type = value as Field["type"];
                  setFields(fields.map((f, i) => (i === index ? { ...f, type, transforms: TRANSFORM_DEFAULTS[type] } : f)));
                }}
                options={[
                  { value: "string", label: "text" },
                  { value: "url", label: "url" },
                  { value: "decimal", label: "decimal" },
                  { value: "integer", label: "integer" },
                ]}
              />
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
              <CustomSelect
                ariaLabel="Extract"
                value={field.selectors[0].attribute ?? "text"}
                onChange={(value) => setFields(fields.map((f, i) => (i === index ? { ...f, selectors: f.selectors.map(({ attribute: _a, ...rest }) => ({ ...rest, ...(value === "text" ? {} : { attribute: value }) })) } : f)))}
                options={["text", "href", "src", "alt", "title", "datetime", "aria-label", "data-testid"].map((attribute) => ({ value: attribute, label: attribute }))}
              />
              <span className="muted">{field.transforms.join(", ")}</span>
            </div>
          </div>
        ))}

        <h3 className="section-label">3. Pagination</h3>
        <label className="field">
          <span>Mode</span>
          <CustomSelect value={pageMode} onChange={(value) => setPageMode(value as typeof pageMode)} options={[
            { value: "none", label: "Single page" },
            { value: "next_link", label: "Next-page link" },
            { value: "infinite_scroll", label: "Infinite scroll" },
            { value: "load_more", label: "Load-more button" },
            { value: "detail_links", label: "Detail links", description: "One record per item page" },
          ]} />
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
        {pageMode === "load_more" && (
          <>
            <div className="row-actions">
              <button type="button" className="btn btn-small" disabled={!scopeUrl || mode !== "none"} onClick={() => void startPick("load_more")}>Pick load-more button</button>
              <label className="field inline small">
                <span>Max clicks</span>
                <input type="number" min={1} max={100} value={maxClicks} onChange={(e) => setMaxClicks(Math.min(100, Number(e.target.value)))} />
              </label>
            </div>
            <input className="code" aria-label="Load more selector" value={loadMoreCss} onChange={(e) => setLoadMoreCss(e.target.value)} placeholder="Load-more button selector" spellCheck={false} />
          </>
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
          <button type="button" className="btn btn-small" disabled={!recordRoot || fields.length === 0 || saved} onClick={() => void save()} title={saved ? "This version is saved; bump the version to save changes" : undefined}>
            {saved ? "Setup saved" : "Keep this setup"}
          </button>
        </details>
        <label className="toggle block small">
          <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> I am authorized to collect and use this data and accept the site's terms.
        </label>
        <label className="field">
          <span>Purpose of this collection</span>
          <CustomSelect value={purpose} onChange={setPurpose} options={PURPOSES.map((purposeOption) => ({ value: purposeOption.id, label: purposeOption.label }))} />
        </label>
        <div className="studio-primary-actions">
          <button type="button" className="btn btn-primary" disabled={!canExtract} onClick={() => void preview()}>
            Preview 10 records
          </button>
          {running === "full" ? (
            <button type="button" className="btn btn-danger" onClick={() => (stopRequested.current = true)}>
              Stop collection
            </button>
          ) : (
            <button type="button" className="btn" disabled={!canExtract || !saved} onClick={() => void collect("full")} title={saved ? undefined : "Preview and save the setup first"}>
              Collect full dataset
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
      <div className="studio-browser">
        <form className="studio-browser-toolbar" onSubmit={(event) => { event.preventDefault(); void openBrowserTarget(url); }}>
          <div className="studio-browser-nav" aria-label="Page navigation">
            <button type="button" aria-label="Go back" title="Go back" disabled={!scopeUrl || browserHome || historyOpen} onClick={() => void studioHost.control("back")}><BrowserIcon name="back" /></button>
            <button type="button" aria-label="Go forward" title="Go forward" disabled={!scopeUrl || browserHome || historyOpen} onClick={() => void studioHost.control("forward")}><BrowserIcon name="forward" /></button>
            <button type="button" aria-label="Reload page" title="Reload page" disabled={!scopeUrl || browserHome || historyOpen} onClick={() => void studioHost.control("reload")}><BrowserIcon name="reload" /></button>
            <button type="button" aria-label="Stop loading" title="Stop loading" disabled={!scopeUrl || browserHome || historyOpen || loaded?.state === "finished"} onClick={() => void studioHost.control("stop")}><BrowserIcon name="stop" /></button>
            <button type="button" aria-label="Open browser home" title="Home" aria-pressed={browserHome} onClick={() => void showBrowserHome()}><BrowserIcon name="home" /></button>
            <button type="button" aria-label="Open browsing history" title="History" aria-pressed={historyOpen} aria-controls={historyOpen ? "studio-browser-history" : undefined} onClick={() => void showBrowserHistory()}><BrowserIcon name="history" /></button>
          </div>
          <label className="studio-address"><span className="sr-only">Search or page address</span><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="Search or enter a web address" spellCheck={false} /></label>
          <button type="submit" className="btn btn-primary btn-small" disabled={!url.trim() || !base || !purpose || detecting}>{detecting ? "Opening…" : "Go"}</button>
          <div className="studio-browser-scroll" aria-label="Scroll page">
            <button type="button" aria-label="Scroll up" title="Scroll up" disabled={!scopeUrl || browserHome || historyOpen} onClick={() => void studioHost.call("scrollPage", [-1])}><BrowserIcon name="up" /></button>
            <button type="button" aria-label="Scroll down" title="Scroll down" disabled={!scopeUrl || browserHome || historyOpen} onClick={() => void studioHost.call("scrollPage", [1])}><BrowserIcon name="down" /></button>
          </div>
        </form>
        <div className="studio-browser-status" aria-live="polite"><span className={`studio-load-dot ${browserHome || historyOpen || loaded?.state === "finished" ? "is-ready" : ""}`} />{historyOpen ? "History" : browserHome ? "New tab" : loaded ? loaded.state === "finished" ? `Ready · ${pageInfo?.title || hostOf(loaded.url) || "page loaded"}` : "Loading page…" : "Enter an address to begin"}</div>
        <div className="studio-preview" ref={previewRef}>
          {historyOpen ? (
            <section className="studio-history-page" id="studio-browser-history" aria-labelledby="studio-history-title">
              <header className="studio-history-heading">
                <div><p className="eyebrow">DataForge Browser</p><h2 id="studio-history-title">History</h2><p className="muted">Pages visited during this Scrape Studio session.</p></div>
                <button type="button" className="btn" disabled={!browserHistory.length} onClick={() => setBrowserHistory([])}>Clear browsing history</button>
              </header>
              <label className="studio-history-search"><span className="sr-only">Search browsing history</span><input type="search" value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} placeholder="Search history" /></label>
              {!browserHistory.length ? (
                <div className="studio-history-empty"><BrowserIcon name="history" /><strong>No browsing history yet</strong><span className="muted">Pages you open in Scrape Studio will appear here with their site icon and preview.</span></div>
              ) : !visibleHistory.length ? (
                <div className="studio-history-empty"><strong>No pages match “{historyQuery}”</strong><span className="muted">Try a site name, page title, or web address.</span></div>
              ) : (
                <ol className="studio-history-list">
                  {visibleHistory.map((entry) => (
                    <li key={`${entry.visited_at}-${entry.url}`}>
                      <button type="button" onClick={() => void openBrowserTarget(entry.url)}>
                        <span className={`studio-history-thumbnail ${entry.thumbnail_url ? "" : "is-favicon"}`}>
                          <img src={entry.thumbnail_url ?? entry.favicon_url} alt="" onError={(event) => { if (event.currentTarget.src !== entry.favicon_url) event.currentTarget.src = entry.favicon_url; else event.currentTarget.hidden = true; }} />
                        </span>
                        <span className="studio-history-copy"><strong>{entry.title}</strong><span>{hostOf(entry.url)}</span><small>{historyTime(entry.visited_at)}</small></span>
                        <span className="studio-history-open">Open</span>
                      </button>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          ) : browserHome ? (
            <div className="studio-home">
              <div className="studio-home-hero" id="studio-browser-home">
                <img className="studio-google-wordmark" src="https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png" alt="Google" />
                <h2 className="sr-only">Search the web</h2>
                <form className="studio-home-search" onSubmit={(event) => { event.preventDefault(); void openBrowserTarget(homeAddress); }}>
                  <label><span className="sr-only">Search Google or type a URL</span><input value={homeAddress} onChange={(event) => setHomeAddress(event.target.value)} placeholder="Search Google or type a URL" autoFocus /></label>
                  <button type="submit" className="btn btn-primary" disabled={!homeAddress.trim() || !base || !purpose || detecting}>{detecting ? "Searching…" : "Search Google"}</button>
                </form>
              </div>
              {!!browserHistory.length && (() => {
                const entry = browserHistory[browserHistory.length - 1];
                return (
                  <section className="studio-home-section studio-home-continue" aria-labelledby="studio-home-continue-title">
                    <header><h3 id="studio-home-continue-title">Continue</h3><button type="button" onClick={() => void showBrowserHistory()}>View history</button></header>
                    <button type="button" className="studio-continue-card" onClick={() => void openBrowserTarget(entry.url)}>
                      <span className={`studio-continue-preview ${entry.thumbnail_url ? "" : "is-favicon"}`}><img src={entry.thumbnail_url ?? entry.favicon_url} alt="" onError={(event) => { if (event.currentTarget.src !== entry.favicon_url) event.currentTarget.src = entry.favicon_url; else event.currentTarget.hidden = true; }} /></span>
                      <span><strong>{entry.title}</strong><small>{hostOf(entry.url)}</small></span>
                    </button>
                  </section>
                );
              })()}
              <section className="studio-speed-dial" aria-labelledby="studio-speed-dial-title">
                <header><div><h3 id="studio-speed-dial-title">Speed Dial</h3><p>Open a supported website and start automatic analysis.</p></div><button type="button" onClick={() => setAllHomeSites((shown) => !shown)}>{allHomeSites ? "Show favorites" : "All websites & regions"}</button></header>
                <div className="studio-dial-grid">
                  {(allHomeSites ? siteChoices : homeChoices).map((choice) => (
                    <button type="button" className="studio-dial-tile" data-category={choice.site.site_category} key={choice.key} onClick={() => void openSiteChoice(choice)}>
                      <span className="studio-dial-art" aria-hidden="true"><img src={faviconFor(choice.variant.example_url)} alt="" onError={(event) => { event.currentTarget.hidden = true; }} /></span>
                      <span className="studio-dial-label"><strong>{choice.site.site_name}</strong><small>{choice.variant.label === "Default" ? choice.variant.domain : choice.variant.label}</small></span>
                    </button>
                  ))}
                  {!allHomeSites && <button type="button" className="studio-dial-tile studio-dial-add" onClick={() => setAllHomeSites(true)}><span className="studio-dial-art"><BrowserIcon name="plus" /></span><span className="studio-dial-label"><strong>Add website</strong><small>Browse every preset</small></span></button>}
                </div>
              </section>
              <section className="studio-speed-dial studio-home-suggestions" aria-labelledby="studio-home-suggestions-title">
                <header><div><h3 id="studio-home-suggestions-title">Suggestions</h3><p>More sources that work with DataForge.</p></div></header>
                <div className="studio-dial-grid">{suggestedHomeChoices.map((choice) => <button type="button" className="studio-dial-tile" data-category={choice.site.site_category} key={choice.key} onClick={() => void openSiteChoice(choice)}><span className="studio-dial-art" aria-hidden="true"><img src={faviconFor(choice.variant.example_url)} alt="" onError={(event) => { event.currentTarget.hidden = true; }} /></span><span className="studio-dial-label"><strong>{choice.site.site_name}</strong><small>{choice.variant.domain}</small></span></button>)}</div>
              </section>
            </div>
          ) : !scopeUrl ? <div className="studio-empty"><strong>Open the page you want to collect from</strong><span className="muted">Paste its address above. DataForge will recommend the collection method and guide you through the rest.</span></div> : null}
        </div>
      </div>
    </div>
  );
}
