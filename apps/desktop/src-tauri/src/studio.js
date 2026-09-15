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
    while (current && current !== root && current.nodeType === 1 && current !== document.documentElement) {
      const part = segment(current);
      parts.unshift(part);
      if (part.includes("#")) break;
      current = current.parentElement;
    }
    return parts.join(" > ");
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
        count = document.querySelectorAll(selector).length;
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

  document.addEventListener("mousemove", (event) => {
    if (mode === "none") return;
    const target = event.target instanceof Element ? event.target : null;
    if (target && target !== hovered && !target.hasAttribute("data-dataforge-overlay")) {
      hovered = target;
      highlight(target);
    }
  }, true);

  document.addEventListener("click", (event) => {
    if (mode === "none") return;
    // Capture the selection without triggering the page's own click behaviour.
    event.preventDefault();
    event.stopImmediatePropagation();
    const el = event.target instanceof Element ? event.target : null;
    if (!el) return;
    const pick = { mode, ...describe(el), selector: pathTo(el) };
    if (mode === "repeated") {
      pick.repeated = repeatedContainer(el);
    } else if (recordRoot) {
      const root = el.closest(recordRoot);
      pick.relative_selector = root ? pathTo(el, root) || ":scope" : null;
      pick.inside_record_root = !!root;
    }
    picks.push(pick);
    mode = "none";
    highlight(null);
  }, true);

  function valueFor(node, css) {
    let selector = css;
    let attribute = null;
    const match = /::(text|attr\(([\w-]+)\))\s*$/.exec(css);
    if (match) {
      selector = css.slice(0, match.index);
      attribute = match[2] || null;
    }
    const target = selector.trim() ? node.querySelector(selector) : node;
    if (!target || isSensitive(target)) return null;
    if (attribute) {
      if (!SAFE_ATTRIBUTES.includes(attribute)) return null;
      const value = target.getAttribute(attribute);
      if (!value) return null;
      if (attribute === "href" || attribute === "src") return new URL(value, location.href).href;
      return value;
    }
    const text = (target.innerText || target.textContent || "").trim();
    return text || null;
  }

  window.__dataforgeStudio = Object.freeze({
    setMode(next, root) {
      mode = ["none", "element", "repeated", "next"].includes(next) ? next : "none";
      recordRoot = typeof root === "string" && root ? root : null;
      if (mode === "none") highlight(null);
      return { mode };
    },
    takePicks() {
      const out = picks;
      picks = [];
      return out;
    },
    pageInfo() {
      const html = document.documentElement.outerHTML.slice(0, 200000).toLowerCase();
      const frames = [...document.querySelectorAll("iframe")];
      const inaccessibleFrames = frames.filter((f) => {
        try {
          return !f.contentDocument;
        } catch {
          return true;
        }
      }).length;
      return {
        url: location.href, title: document.title.slice(0, 200), ready_state: document.readyState,
        challenge_detected: CHALLENGE.some((m) => html.includes(m)), password_fields: document.querySelectorAll("input[type=password]").length,
        inaccessible_frames: inaccessibleFrames,
      };
    },
    count(css) {
      try {
        return { count: document.querySelectorAll(css).length, error: null };
      } catch (error) {
        return { count: 0, error: String(error) };
      }
    },
    extract(config) {
      let roots;
      try {
        roots = [...document.querySelectorAll(config.record_root)];
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
              value = valueFor(root, selector.attribute ? `${selector.css}::attr(${selector.attribute})` : selector.css);
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
          const next = document.querySelector(config.next_css.replace(/::attr\(href\)\s*$/, ""));
          const href = next && next.getAttribute("href");
          nextUrl = href ? new URL(href, location.href).href : null;
        } catch {
          nextUrl = null;
        }
      }
      return { url: location.href, candidates: roots.length, records, next_url: nextUrl, error: null };
    },
  });
})();
