// DataForge Scrape Studio bridge. Injected by the desktop host into the embedded child WebView only.
// It exposes a fixed set of functions the host calls by name. It never reads form values, cookies,
// storage, or hidden inputs, and it never sends data anywhere: the host polls for results.
(() => {
  if (window.top !== window || window.__dataforgeStudio) return;

  const SAFE_ATTRIBUTES = ["href", "src", "alt", "title", "datetime", "aria-label", "data-testid"];
  const SECRET_VALUE = /(token|session|auth|password|secret|sig=|key=)/i;
  const CHALLENGE = ["g-recaptcha", "h-captcha", "cf-challenge", "challenge-platform", "captcha-delivery"];
  let mode = "none";
  let recordRoot = null;
  let picks = [];
  let overlay = null;
  let hovered = null;
  const listenerDocuments = new WeakSet();
  const networkResponses = [];
  let networkBytes = 0;

  /** Documents plus open shadow roots and same-origin frames, capped to avoid hostile DOM expansion. */
  function scopes(start = document) {
    const found = [];
    const queue = [start];
    const seen = new Set();
    while (queue.length && found.length < 100) {
      const scope = queue.shift();
      if (!scope || seen.has(scope)) continue;
      seen.add(scope);
      found.push(scope);
      for (const element of [...scope.querySelectorAll("*")].slice(0, 20000)) {
        if (element.shadowRoot) queue.push(element.shadowRoot);
        if (element.tagName === "IFRAME") {
          try {
            if (element.contentDocument) queue.push(element.contentDocument);
          } catch {
            // Cross-origin frames remain intentionally inaccessible.
          }
        }
      }
    }
    return found;
  }

  function queryAll(css, within) {
    const roots = scopes(within || document);
    return [...new Set(roots.flatMap((scope) => [...scope.querySelectorAll(css)]))];
  }

  function safeUrl(raw) {
    try {
      const url = new URL(raw, location.href);
      return url.origin === location.origin ? url.origin + url.pathname : null;
    } catch {
      return null;
    }
  }

  function rememberJson(rawUrl, text) {
    const url = safeUrl(rawUrl);
    if (!url || typeof text !== "string" || text.length > 1000000 || networkResponses.length >= 50 || networkBytes + text.length > 5000000) return;
    try {
      const data = JSON.parse(text);
      networkResponses.push({ url, data });
      networkBytes += text.length;
    } catch {
      // Only valid JSON is retained.
    }
  }

  try {
    const originalFetch = window.fetch;
    window.fetch = function (...args) {
      const pending = originalFetch.apply(this, args);
      pending.then((response) => {
        const type = response.headers.get("content-type") || "";
        if (type.includes("json")) response.clone().text().then((text) => rememberJson(response.url, text)).catch(() => {});
      }).catch(() => {});
      return pending;
    };
    const originalOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      this.__dataforgeUrl = String(url);
      return originalOpen.call(this, method, url, ...rest);
    };
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (...args) {
      this.addEventListener("loadend", () => {
        try {
          if ((this.getResponseHeader("content-type") || "").includes("json") && (!this.responseType || this.responseType === "text")) rememberJson(this.responseURL || this.__dataforgeUrl, this.responseText);
        } catch {
          // Some response types prohibit responseText access.
        }
      }, { once: true });
      return originalSend.apply(this, args);
    };
  } catch {
    // A page may freeze browser prototypes; DOM extraction still works.
  }

  const isSensitive = (el) => !!el.closest("input, textarea, select, option, [contenteditable=''], [contenteditable='true'], [type=password]");

  const stableClasses = (el) =>
    [...el.classList].filter((c) => /^[a-zA-Z][\w-]{1,40}$/.test(c) && !/\d{3,}/.test(c) && !/^(is|has)-(active|hover|focus|selected)/.test(c)).slice(0, 2);

  const generalized = (el) => el.tagName.toLowerCase() + stableClasses(el).map((c) => "." + CSS.escape(c)).join("");

  function segment(el) {
    if (el.id && /^[a-zA-Z][\w-]*$/.test(el.id) && !/\d{3,}/.test(el.id)) return el.tagName.toLowerCase() + "#" + CSS.escape(el.id);
    let selector = generalized(el);
    const parent = el.parentElement;
    if (parent) {
      const sameTag = [...parent.children].filter((c) => c.tagName === el.tagName);
      const matching = sameTag.filter((c) => c.matches(selector));
      if (matching.length > 1) selector += `:nth-of-type(${sameTag.indexOf(el) + 1})`;
    }
    return selector;
  }

  function pathTo(el, root) {
    const parts = [];
    let current = el;
    while (current && current !== root && current.nodeType === 1 && current !== el.ownerDocument.documentElement) {
      const part = segment(current);
      parts.unshift(part);
      if (part.includes("#")) break;
      current = current.parentElement;
    }
    return parts.join(" > ");
  }

  /** Relative structural XPath from root, e.g. ./div[2]/span[1]. Survives class renames. */
  function structuralXPath(el, root) {
    const steps = [];
    let current = el;
    while (current && current !== root && current.nodeType === 1 && steps.length < 8) {
      const parent = current.parentElement;
      if (!parent) return null;
      const sameTag = [...parent.children].filter((c) => c.tagName === current.tagName);
      steps.unshift(`${current.tagName.toLowerCase()}[${sameTag.indexOf(current) + 1}]`);
      current = parent;
    }
    return current === root ? "./" + steps.join("/") : null;
  }

  /** Label-anchored XPath: the value that follows a short visible label ("Price:", <dt>SKU</dt>). Survives reordering. */
  function labelXPath(el, root) {
    const candidates = [el.previousElementSibling, el.parentElement && el.parentElement !== root ? el.parentElement.previousElementSibling : null];
    for (const label of candidates) {
      if (!label || isSensitive(label)) continue;
      const text = (label.textContent || "").trim().replace(/\s+/g, " ");
      if (!text || text.length > 40 || /['"]/.test(text) || !root.contains(label)) continue;
      // A label is a caption, not a value: label-like markup or a trailing colon, with no links or headings.
      const labelLike = ["dt", "th", "label"].includes(label.tagName.toLowerCase()) || /:\s*$/.test(text);
      if (!labelLike || label.querySelector("a, h1, h2, h3, h4, h5, h6") || /^h[1-6]$/i.test(label.tagName)) continue;
      const target = label === el.previousElementSibling ? el : el.parentElement;
      const step = `${target.tagName.toLowerCase()}[1]`;
      const inner = target === el ? "" : "/" + (structuralXPath(el, target) || "").replace(/^\.\//, "");
      if (target !== el && inner === "/") continue;
      const xpath = `.//${label.tagName.toLowerCase()}[normalize-space(.)='${text}']/following-sibling::${step}${inner}`;
      // The same label must appear in other repeated items, or it identifies one item rather than a field.
      const siblings = recordRoot ? queryAll(recordRoot).slice(0, 10) : [];
      const hits = siblings.filter((item) => nodeForXPath(item, xpath)).length;
      if (siblings.length > 1 && hits < 2) continue;
      return xpath;
    }
    return null;
  }

  function nodeForXPath(node, xpath) {
    const owner = node.ownerDocument || document;
    const result = owner.evaluate(xpath, node, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
    return result.singleNodeValue instanceof Element ? result.singleNodeValue : null;
  }

  function describe(el) {
    const attributes = {};
    for (const name of SAFE_ATTRIBUTES) {
      const value = el.getAttribute(name);
      if (value && !SECRET_VALUE.test(value)) attributes[name] = value.slice(0, 300);
    }
    const text = isSensitive(el) ? "" : (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 160);
    const tag = el.tagName.toLowerCase();
    const suggestedAttribute = tag === "a" ? "href" : tag === "img" ? "src" : null;
    return { tag, text, attributes, suggested_attribute: suggestedAttribute, frame: "top" };
  }

  function repeatedContainer(el) {
    let current = el;
    while (current && current !== document.body) {
      const parent = current.parentElement;
      const selector = parent && parent !== document.body ? pathTo(parent) + " > " + generalized(current) : generalized(current);
      let count = 0;
      try {
        count = queryAll(selector).length;
      } catch {
        count = 0;
      }
      if (count >= 3) return { selector, count };
      current = parent;
    }
    return null;
  }

  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.setAttribute("data-dataforge-overlay", "");
    Object.assign(overlay.style, {
      position: "fixed", zIndex: "2147483647", pointerEvents: "none", border: "2px solid #34d399",
      background: "rgba(52, 211, 153, 0.15)", borderRadius: "3px", display: "none",
    });
    document.documentElement.appendChild(overlay);
    return overlay;
  }

  function highlight(el) {
    const box = ensureOverlay();
    if (!el) {
      box.style.display = "none";
      return;
    }
    const rect = el.getBoundingClientRect();
    Object.assign(box.style, { display: "block", left: rect.left + "px", top: rect.top + "px", width: rect.width + "px", height: rect.height + "px" });
  }

  function handleMove(event) {
    if (mode === "none") return;
    const target = event.target instanceof Element ? event.target : null;
    if (target && target !== hovered && !target.hasAttribute("data-dataforge-overlay")) {
      hovered = target;
      highlight(target);
    }
  }

  function handleClick(event) {
    if (mode === "none") return;
    // Capture the selection without triggering the page's own click behaviour.
    event.preventDefault();
    event.stopImmediatePropagation();
    const el = event.target instanceof Element ? event.target : null;
    if (!el) return;
    const pick = { mode, ...describe(el), selector: pathTo(el) };
    if (mode === "repeated") {
      pick.repeated = repeatedContainer(el);
    } else if (mode === "detail") {
      const link = el.closest("a[href]");
      const container = link ? repeatedContainer(link) : null;
      // Target the link inside each repeated item, not the item itself.
      pick.repeated = container && link ? { selector: container.selector === generalized(link) || container.selector.endsWith(" > " + generalized(link)) ? container.selector : `${container.selector} ${generalized(link)}`, count: container.count } : null;
      pick.tag = link ? "a" : pick.tag;
    } else if (recordRoot) {
      const root = el.closest(recordRoot);
      pick.relative_selector = root ? pathTo(el, root) || ":scope" : null;
      pick.inside_record_root = !!root;
      // Ordered fallbacks tried after the CSS selector: label-anchored, then structural.
      pick.fallback_xpaths = root ? [labelXPath(el, root), structuralXPath(el, root)].filter((x) => x && x !== "./") : [];
    }
    picks.push(pick);
    mode = "none";
    highlight(null);
  }

  function installListeners() {
    for (const scope of scopes()) {
      const owner = scope.nodeType === Node.DOCUMENT_NODE ? scope : scope.ownerDocument;
      if (!owner || listenerDocuments.has(owner)) continue;
      listenerDocuments.add(owner);
      owner.addEventListener("mousemove", handleMove, true);
      owner.addEventListener("click", handleClick, true);
    }
  }

  installListeners();

  function valueFor(node, css) {
    let selector = css;
    let attribute = null;
    const match = /::(text|attr\(([\w-]+)\))\s*$/.exec(css);
    if (match) {
      selector = css.slice(0, match.index);
      attribute = match[2] || null;
    }
    const target = selector.trim() ? queryAll(selector, node)[0] : node;
    if (!target || isSensitive(target)) return null;
    if (attribute) {
      if (!SAFE_ATTRIBUTES.includes(attribute)) return null;
      const value = target.getAttribute(attribute);
      if (!value) return null;
      if (attribute === "href" || attribute === "src") return new URL(value, target.ownerDocument.defaultView.location.href).href;
      return value;
    }
    const text = (target.innerText || target.textContent || "").trim();
    return text || null;
  }

  window.__dataforgeStudio = Object.freeze({
    setMode(next, root) {
      mode = ["none", "element", "repeated", "next", "detail", "load_more"].includes(next) ? next : "none";
      recordRoot = typeof root === "string" && root ? root : null;
      installListeners();
      if (mode === "none") highlight(null);
      return { mode };
    },
    takePicks() {
      const out = picks;
      picks = [];
      return out;
    },
    pageInfo() {
      const html = scopes().map((scope) => scope.nodeType === 9 ? scope.documentElement?.outerHTML || "" : scope.host?.outerHTML || "").join(" ").slice(0, 500000).toLowerCase();
      const frames = queryAll("iframe");
      const absoluteAsset = (value) => {
        if (!value) return null;
        try {
          const asset = new URL(value, location.href);
          return ["http:", "https:"].includes(asset.protocol) ? asset.href.slice(0, 2048) : null;
        } catch {
          return null;
        }
      };
      const icon = document.querySelector("link[rel~='icon'][href]");
      const preview = document.querySelector("meta[property='og:image'][content], meta[name='twitter:image'][content], meta[property='twitter:image'][content]");
      const inaccessibleFrames = frames.filter((f) => {
        try {
          return !f.contentDocument;
        } catch {
          return true;
        }
      }).length;
      return {
        url: location.href, title: document.title.slice(0, 200), ready_state: document.readyState,
        favicon_url: absoluteAsset(icon?.getAttribute("href")) || absoluteAsset("/favicon.ico"),
        thumbnail_url: absoluteAsset(preview?.getAttribute("content")),
        challenge_detected: CHALLENGE.some((m) => html.includes(m)), password_fields: queryAll("input[type=password]").length,
        inaccessible_frames: inaccessibleFrames,
      };
    },
    html() {
      // Read-only snapshot for structured-data detection; capped so large pages cannot flood the bridge.
      return { url: location.href, html: document.documentElement.outerHTML.slice(0, 5000000) };
    },
    scrollStep() {
      window.scrollTo(0, document.documentElement.scrollHeight);
      return { height: document.documentElement.scrollHeight, y: window.scrollY };
    },
    scrollPage(direction) {
      const amount = Math.max(240, Math.floor(window.innerHeight * 0.82));
      window.scrollBy({ top: Number(direction) < 0 ? -amount : amount, left: 0, behavior: "smooth" });
      return { height: document.documentElement.scrollHeight, y: window.scrollY };
    },
    suggestFlow() {
      const visible = (node) => {
        const style = node.ownerDocument.defaultView.getComputedStyle(node);
        return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0 && !isSensitive(node);
      };
      const label = (node) => [node.textContent, node.getAttribute("aria-label"), node.getAttribute("title")].filter(Boolean).join(" ").trim().replace(/\s+/g, " ");
      const anchors = queryAll("a[href]").filter(visible);
      const next = anchors.find((node) => (node.getAttribute("rel") || "").split(/\s+/).includes("next"))
        || anchors.find((node) => /^(next(?:\s+page)?|older|more|›|»|→)$/i.test(label(node)));
      if (next) {
        return { kind: "next_link", next_css: pathTo(next), load_more_css: null, can_scroll: document.documentElement.scrollHeight > window.innerHeight + 40, reason: "A visible next-page link was found." };
      }
      const controls = queryAll("button, [role=button]").filter((node) => visible(node) && !node.closest("form"));
      const loadMore = controls.find((node) => /^(load more|show more|view more|more results|see more)$/i.test(label(node)));
      if (loadMore) {
        return { kind: "load_more", next_css: null, load_more_css: pathTo(loadMore), can_scroll: document.documentElement.scrollHeight > window.innerHeight + 40, reason: "A visible load-more button was found." };
      }
      const canScroll = document.documentElement.scrollHeight > window.innerHeight + 40;
      const infiniteMarker = queryAll("[data-infinite-scroll], [data-infinite-scroller], .infinite-scroll, .infinite-scroller").some(visible);
      return {
        kind: infiniteMarker && canScroll ? "infinite_scroll" : "none",
        next_css: null,
        load_more_css: null,
        can_scroll: canScroll,
        reason: infiniteMarker && canScroll ? "The page identifies an infinite-scrolling result area." : canScroll ? "The page can be scrolled; no automatic next-page control was found." : "This page fits in the visible area.",
      };
    },
    click(css) {
      try {
        const selector = String(css || "").replace(/::(?:text|attr\([\w-]+\))\s*$/, "");
        const target = queryAll(selector).find((node) => {
          const style = node.ownerDocument.defaultView.getComputedStyle(node);
          const visible = style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
          const safeControl = node.matches("a[href], button:not([type=submit]), [role=button]") && !node.closest("form") && !isSensitive(node);
          return visible && safeControl;
        });
        if (!target) return { clicked: false, error: "No visible non-form button matches that selector" };
        target.click();
        return { clicked: true, error: null };
      } catch (error) {
        return { clicked: false, error: String(error) };
      }
    },
    automation(step) {
      try {
        const kind = String(step?.type || "");
        const selector = String(step?.selector || "");
        if (!selector || !["click", "fill", "read"].includes(kind)) return { ok: false, error: "Unsupported workflow action" };
        const target = queryAll(selector).find((node) => {
          const style = node.ownerDocument.defaultView.getComputedStyle(node);
          return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
        });
        if (!target) return { ok: false, error: "No visible element matches that selector" };
        if (target.matches("input[type=password], input[type=file]") || target.closest("[data-dataforge-secret]")) {
          return { ok: false, error: "Password, file, and secret fields are never automated" };
        }
        if (kind === "click") {
          const safe = target.matches("a[href], button:not([type=submit]), [role=button], input[type=checkbox], input[type=radio]");
          if (!safe || target.closest("form")?.querySelector("input[type=password]")) return { ok: false, error: "Only non-submit, non-secret controls can be clicked" };
          target.click();
          return { ok: true, value: null };
        }
        if (kind === "fill") {
          if (!target.matches("input:not([type]), input[type=text], input[type=email], input[type=tel], input[type=url], input[type=search], input[type=number], textarea, [contenteditable=true]")) {
            return { ok: false, error: "Fill targets must be ordinary text fields" };
          }
          const value = String(step?.value ?? "").slice(0, 10000);
          if (target.isContentEditable) target.textContent = value;
          else {
            const prototype = target instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(target, value);
          }
          target.dispatchEvent(new Event("input", { bubbles: true }));
          target.dispatchEvent(new Event("change", { bubbles: true }));
          return { ok: true, value: null };
        }
        if (isSensitive(target)) return { ok: false, error: "Form values cannot be read" };
        return { ok: true, value: (target.innerText || target.textContent || "").trim().slice(0, 10000) };
      } catch (error) {
        return { ok: false, error: String(error) };
      }
    },
    networkData() {
      return { responses: networkResponses.slice(0, 50), error: null };
    },
    links(css) {
      try {
        const selector = css.replace(/::attr\(href\)\s*$/, "");
        const anchors = queryAll(selector)
          .map((node) => (node.matches("a[href]") ? node : node.querySelector("a[href]")))
          .filter(Boolean);
        const urls = anchors
          .map((anchor) => [anchor.getAttribute("href"), anchor.ownerDocument.defaultView.location.href])
          .filter(([href]) => Boolean(href))
          .map(([href, base]) => new URL(href, base).href);
        return { urls: [...new Set(urls)].slice(0, 1000), error: null };
      } catch (error) {
        return { urls: [], error: String(error) };
      }
    },
    count(css) {
      try {
        return { count: queryAll(css).length, error: null };
      } catch (error) {
        return { count: 0, error: String(error) };
      }
    },
    extract(config) {
      let roots;
      try {
        roots = queryAll(config.record_root);
      } catch (error) {
        return { error: "Invalid record root selector: " + String(error), records: [] };
      }
      const limit = Math.max(0, Math.min(Number(config.limit) || 500, 5000));
      const records = roots.slice(0, limit).map((root) => {
        const record = {};
        for (const field of config.fields || []) {
          for (const selector of field.selectors || []) {
            let value = null;
            try {
              if (selector.xpath) {
                const target = nodeForXPath(root, selector.xpath);
                if (target && !isSensitive(target)) {
                  if (selector.attribute) {
                    const raw = SAFE_ATTRIBUTES.includes(selector.attribute) ? target.getAttribute(selector.attribute) : null;
                    value = raw ? (selector.attribute === "href" || selector.attribute === "src" ? new URL(raw, target.ownerDocument.defaultView.location.href).href : raw) : null;
                  } else {
                    value = (target.innerText || target.textContent || "").trim() || null;
                  }
                }
              } else {
                value = valueFor(root, selector.attribute ? `${selector.css}::attr(${selector.attribute})` : selector.css);
              }
            } catch {
              value = null;
            }
            if (value !== null) {
              record[field.key] = String(value).slice(0, 10000);
              break;
            }
          }
        }
        return record;
      });
      let nextUrl = null;
      if (config.next_css) {
        try {
          const next = queryAll(config.next_css.replace(/::attr\(href\)\s*$/, ""))[0];
          const href = next && next.getAttribute("href");
          nextUrl = href ? new URL(href, next.ownerDocument.defaultView.location.href).href : null;
        } catch {
          nextUrl = null;
        }
      }
      return { url: location.href, candidates: roots.length, records, next_url: nextUrl, error: null };
    },
  });
})();
