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
        if (/json|x-component|text\/plain/i.test(type)) {
          response.clone().text().then((text) => {
            if (type.includes("json")) rememberJson(response.url, text);
            collectors.follow(text, type);
          }).catch(() => {});
        }
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
          const type = this.getResponseHeader("content-type") || "";
          if (!this.responseType || this.responseType === "text") {
            if (type.includes("json")) rememberJson(this.responseURL || this.__dataforgeUrl, this.responseText);
            if (/json|x-component|text\/plain/i.test(type)) collectors.follow(this.responseText, type);
          }
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

  /* ---- Listing and product collectors -------------------------------------------------------------------------
   * Read the page the user is looking at (and the data the site loads while they browse) into one row per listing or
   * product. Read-only like the rest of the bridge: nothing is fetched, stored, or sent; the host reads rows through
   * the allow-listed listings / products / nextPage actions. Agent and contact fields are skipped and phone numbers
   * and e-mail addresses are scrubbed from free text.
   */
  const collectors = (() => {
    const PHONE = /(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}/g;
    const EMAIL = /[\w.+-]+@[\w-]+\.[\w.-]+/g;
    const scrub = s => String(s ?? '').replace(EMAIL, '[email removed]').replace(PHONE, '[phone removed]');
    const clean = s => String(s ?? '').replace(/\s+/g, ' ').trim();

    /* ---- listing finder: reads any site's embedded or loaded data and normalizes what looks like a listing ---- */
    const PERSONAL = /^agent|agent(s|name|id|photo|image|phone|email|contact|license|first|last)|advertiser|phone|mobile|whatsapp|email|contact|owner|^user|username|person|^lead/i;
    const KEYS = {
      id: /^(propertyid|property_id|listingid|listing_id|zpid|typedhomeid|homeid|home_id|id|externalid|uuid)$/i,
      address: /^(addressstreet|streetaddress|street_address|streetline|street|line|address1|addressline1|fulladdress|addressfull|formattedaddress|displayaddress|fulllocation|address)$/i,
      city: /^(addresscity|city|cityname|addresslocality|town|municipality)$/i,
      state: /^(addressstate|statecode|state_code|stateabbr|stateabbreviation|state|stateorprovince|addressregion|region)$/i,
      zip: /^(addresszipcode|zipcode|zip_code|zip|postalcode|postal_code|postcode)$/i,
      price: /^(list_price|listprice|listedprice|listingprice|price|askingprice|currentprice|unformattedprice|saleprice|lowprice|minprice|price_min|pricemin|minrent|rentmin|startingprice|pricefrom|rent|rentprice|monthlyrent|pricerange|rentrange|amount)$/i,
      priceMax: /^(highprice|maxprice|price_max|pricemax|maxrent|rentmax|priceto)$/i,
      status: /^(status|listingstatus|homestatus|mlsstatus|statustext|currentstatus|listing_status)$/i,
      type: /^(propertytype|property_type|hometype|home_type|type|propertysubtype|uipropertytype|categoryname)$/i,
      beds: /^(beds|bedrooms|numberofbedrooms|bedroomcount|bed|bedrooms_total|beds_max|bedsmin|bedrange|bedtext|bedcount|minbeds|beds_min)$/i,
      baths: /^(baths|bathrooms|baths_consolidated|numberofbathroomstotal|bathroomstotal|bathroomcount|bath|baths_full|bathrange|bathtext|bathcount|minbaths)$/i,
      size: /^(sqft|sqftmax|livingarea|living_area|floorspace|floorsize|buildingsize|size|area|squarefeet|square_feet|finishedsqft|squarefeettext|sqfttext|sqftrange|areatext)$/i,
      lot: /^(lotsize|lot_size|lot_sqft|lotsqft|lotarea|lotsizesqft|lotareavalue)$/i,
      year: /^(yearbuilt|year_built|built)$/i,
      hoa: /^(hoa|hoafee|hoa_fee|monthlyhoafee|associationfee|hoadues)$/i,
      dom: /^(dom|daysonmarket|days_on_market|daysonzillow|daysonsite|timeonmarket)$/i,
      listed: /^(list_date|listdate|listingdate|listeddate|listed_at|datelisted|createdat|created_at|publishedat|onmarketdate|datepostedstring)$/i,
      brokerage: /^(brokername|brokerage|brokeragename|listingbrokername|listingofficename|officename|office|branding|provider|listingprovider|brokerdisplayname|company|propertymanagementcompany|managementcompany|managedby)$/i,
      mls: /^(mlsid|mls_id|mlsnumber|mls_number|mls)$/i,
      lat: /^(lat|latitude)$/i,
      lng: /^(lng|lon|long|longitude)$/i,
      url: /^(href|url|detailurl|detail_url|permalink|homeurl|propertyurl|listingurl|canonicalurl|shareurl|link|seourl|urlpathname|pathname|detailpath|relativeurl|detailspath)$/i,
      image: /^(primary_photo|primaryphoto|heroimage|imgsrc|image|imageurl|photo|mainphoto|coverphoto|thumbnail|thumb|mainimage|picture)$/i,
      photos: /^(photos|images|media|photourls|imageurls|carouselphotos|gallery|pictures)$/i,
      description: /^(description|remarks|publicremarks|marketingremarks|listingremarks|longdescription)$/i,
    };
    const STREET_KEYS = /^(addressstreet|streetaddress|street_address|streetline|street|line|address1|addressline1)$/i;
    const NOT_TYPE = /^(large|medium|small|x?large|thumb(nail)?|image|photo|video|default|primary|main|hero|place|offer|organization|website|web ?page|listing|product|text|link|button|icon|item|unknown|other|null|none)$/i;
    const LISTING_FIELDS = ['Listing ID', 'Address', 'City', 'State', 'Zip', 'Status', 'Property type', 'Price', 'Price (max)', 'Beds', 'Baths',
      'Living area (sqft)', 'Lot size', 'Year built', 'HOA fee', 'Days on market', 'Listed', 'Brokerage', 'MLS #', 'Latitude', 'Longitude',
      'Image', 'Listing URL', 'Description', 'Photos'];
    const SIGNAL = ['id', 'address', 'city', 'zip', 'beds', 'baths', 'size', 'url', 'type', 'image', 'lat'];
    const STATES = { alabama: 'AL', alaska: 'AK', arizona: 'AZ', arkansas: 'AR', california: 'CA', colorado: 'CO', connecticut: 'CT', delaware: 'DE',
      'district of columbia': 'DC', florida: 'FL', georgia: 'GA', hawaii: 'HI', idaho: 'ID', illinois: 'IL', indiana: 'IN', iowa: 'IA', kansas: 'KS',
      kentucky: 'KY', louisiana: 'LA', maine: 'ME', maryland: 'MD', massachusetts: 'MA', michigan: 'MI', minnesota: 'MN', mississippi: 'MS',
      missouri: 'MO', montana: 'MT', nebraska: 'NE', nevada: 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY',
      'north carolina': 'NC', 'north dakota': 'ND', ohio: 'OH', oklahoma: 'OK', oregon: 'OR', pennsylvania: 'PA', 'puerto rico': 'PR',
      'rhode island': 'RI', 'south carolina': 'SC', 'south dakota': 'SD', tennessee: 'TN', texas: 'TX', utah: 'UT', vermont: 'VT', virginia: 'VA',
      washington: 'WA', 'west virginia': 'WV', wisconsin: 'WI', wyoming: 'WY' };
    const stateCode = s => STATES[s.toLowerCase()] || (/^[a-z]{2}$/i.test(s) ? s.toUpperCase() : s);
    const tidy = s => {
      s = s.replace(/[\s,;]+$/, '');
      return /[A-Z]{3}/.test(s) && s === s.toUpperCase() ? s.toLowerCase().replace(/\b[a-z]/g, c => c.toUpperCase()) : s;
    };
    const isObject = v => !!v && typeof v === 'object' && !Array.isArray(v);
    const toNumber = v => {
      if (typeof v === 'number') return Number.isFinite(v) ? v : null;
      if (typeof v !== 'string') return null;
      const m = v.replace(/,/g, '').match(/(\d+(?:\.\d+)?)\s*([kKmM])?(?![a-zA-Z])/);
      if (!m) return null;
      return Number(m[1]) * (m[2] ? (/k/i.test(m[2]) ? 1e3 : 1e6) : 1);
    };
    const text = v => typeof v === 'string' ? v.trim() : typeof v === 'number' ? String(v) : '';
    const VALUE_KEYS = ['value', 'formattedValue', 'formattedPrice', 'formattedDimension', 'amount', 'price', 'name', 'label', 'displayValue',
      'display', 'text', 'full', 'formatted', 'summary', 'href', 'url', 'min', 'low'];
    const plain = v => {
      if (typeof v === 'string' || typeof v === 'number') return text(v);
      if (Array.isArray(v)) return v.map(plain).filter(Boolean).slice(0, 3).join(', ');
      if (isObject(v)) {
        for (const key of VALUE_KEYS) if (v[key] !== undefined && v[key] !== null && typeof v[key] !== 'boolean') { const out = plain(v[key]); if (out) return out; }
        const flags = Object.entries(v).filter(([key, value]) => value === true && /^is/.test(key))
          .map(([key]) => key.replace(/^is/, '').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase().replace(/^./, c => c.toUpperCase()));
        return flags.join(', ');
      }
      return '';
    };
    const find = (obj, pattern, accept = v => plain(v) !== '', depth = 4) => {
      if (!isObject(obj)) return undefined;
      for (const [key, value] of Object.entries(obj)) {
        if (pattern.test(key) && !PERSONAL.test(key) && value !== null && value !== '' && accept(value)) return value;
      }
      if (depth > 1) {
        for (const [key, value] of Object.entries(obj)) {
          if (isObject(value) && !PERSONAL.test(key)) {
            const found = find(value, pattern, accept, depth - 1);
            if (found !== undefined) return found;
          }
        }
      }
      return undefined;
    };
    const numberOf = v => toNumber(typeof v === 'number' ? v : plain(v));
    const imageUrl = v => {
      if (typeof v === 'string') return /^(https?:)?\/\/\S+$/.test(v) && !/fallback|placeholder|no[-_]?(photo|image)|noimage/i.test(v) ? (v.startsWith('//') ? 'https:' + v : v) : '';
      if (isObject(v)) {
        for (const key of ['href', 'url', 'src', 'large', 'medium', 'full', 'original', 'main', 'mediumSrc', 'uri', 'link', 'small']) {
          const out = imageUrl(v[key]);
          if (out) return out;
        }
      }
      return '';
    };
    const linkOf = (obj, base) => {
      const value = find(obj, KEYS.url, v => {
        const s = typeof v === 'string' ? v : isObject(v) ? (v.href || v.url || v.path || '') : '';
        return typeof s === 'string' && (/^https?:\/\/[^/]+\/\S+/.test(s) || /^\/\S+/.test(s)) && !/\.(jpe?g|png|webp|gif)(\?|$)/i.test(s);
      }, 2);
      const s = isObject(value) ? (value.href || value.url || value.path) : value;
      try { return s ? new URL(s, base).href : ''; } catch (error) { return ''; }
    };
    const CENTS = /^(listpricecents|list_price_cents|pricecents|price_cents|amountcents|amount_cents)$/i;
    const priceOf = obj => {
      const price = numberOf(find(obj, KEYS.price, v => numberOf(v) !== null && numberOf(v) > 0, 3));
      if (price !== null) return price;
      const cents = numberOf(find(obj, CENTS, v => numberOf(v) !== null && numberOf(v) > 0, 3));
      return cents === null ? null : Math.round(cents) / 100;
    };
    const score = obj => {
      if (!isObject(obj) || priceOf(obj) === null) return 0;
      let hits = 0;
      for (const name of SIGNAL) if (find(obj, KEYS[name]) !== undefined) hits += 1;
      return hits >= 3 ? hits : 0;
    };
    /* list items often wrap the listing: schema.org {"@type": "ListItem", item}, GraphQL {node}, {listing}, {property} */
    const unwrap = obj => {
      if (!isObject(obj) || score(obj)) return obj;
      for (const key of ['item', 'node', 'listing', 'property', 'home', 'result', 'data', 'attributes']) {
        if (isObject(obj[key]) && score(obj[key])) return obj[key];
      }
      return obj;
    };
    const dateOf = v => {
      if (v === undefined || v === null || v === '') return '';
      const raw = typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(plain(v));
      return isNaN(raw) ? plain(v) : raw.toISOString().slice(0, 10);
    };
    const normalize = (obj, base) => {
      const sizeValue = find(obj, KEYS.size, v => numberOf(v) !== null);
      const photoHolder = find(obj, KEYS.photos, v => Array.isArray(v) && v.length > 0) ??
        (isObject(find(obj, KEYS.photos, isObject)) ? Object.values(find(obj, KEYS.photos, isObject)).find(Array.isArray) : undefined);
      const photos = Array.isArray(photoHolder) ? [...new Set(photoHolder.map(imageUrl).filter(Boolean))].slice(0, 60) : [];
      /* a street line ("21619 Astipalia Dr") beats a one-line full address ("21619 Astipalia Dr Spring TX 77388") when the item has both */
      const addressValue = find(obj, STREET_KEYS, v => typeof v === 'string' && /\d/.test(v), 2) ??
        find(obj, KEYS.address, v => typeof v === 'string' || (isObject(v) && !!(v.streetAddress || v.line || v.street || plain(v))));
      const address = isObject(addressValue) ? plain(addressValue.streetAddress || addressValue.line || addressValue.street || addressValue) : plain(addressValue);
      const range = find(obj, /^(pricerange|rentrange)$/i, isObject);
      const price = priceOf(obj);
      const priceMax = numberOf(find(obj, KEYS.priceMax, v => numberOf(v) !== null, 3)) ?? (range ? toNumber(range.max ?? range.high) : null);
      return {
        'Listing ID': plain(find(obj, KEYS.id, v => typeof v === 'string' || typeof v === 'number', 2)),
        'Address': tidy(address),
        'City': tidy(plain(find(obj, KEYS.city, v => typeof v === 'string'))),
        'State': stateCode(plain(find(obj, KEYS.state, v => typeof v === 'string' && v.length <= 20))),
        'Zip': plain(find(obj, KEYS.zip, v => /^\d{5}/.test(String(plain(v))))).slice(0, 10),
        'Status': plain(find(obj, KEYS.status, v => !/^\d+$/.test(plain(v)) && plain(v) !== '')),
        'Property type': plain(find(obj, KEYS.type, v => (typeof v === 'string' && !NOT_TYPE.test(v)) || isObject(v))).replace(/_/g, ' ').toLowerCase().replace(/^./, c => c.toUpperCase()),
        'Price': price ?? '',
        'Price (max)': priceMax !== null && priceMax !== price ? priceMax : '',
        'Beds': numberOf(find(obj, KEYS.beds, v => numberOf(v) !== null || /studio/i.test(plain(v)))) ?? (/studio/i.test(plain(find(obj, KEYS.beds))) ? 0 : ''),
        'Baths': numberOf(find(obj, KEYS.baths, v => numberOf(v) !== null)) ?? '',
        'Living area (sqft)': numberOf(sizeValue) ?? '',
        'Lot size': plain(find(obj, KEYS.lot)),
        'Year built': numberOf(find(obj, KEYS.year, v => { const n = numberOf(v); return n !== null && n > 1700 && n < 2100; })) ?? '',
        'HOA fee': numberOf(find(obj, KEYS.hoa, v => numberOf(v) !== null)) ?? '',
        'Days on market': numberOf(find(obj, KEYS.dom, v => numberOf(v) !== null && numberOf(v) >= 0)) ?? '',
        'Listed': dateOf(find(obj, KEYS.listed)),
        'Brokerage': plain(find(obj, KEYS.brokerage)),
        'MLS #': plain(find(obj, KEYS.mls, v => typeof v === 'string' || typeof v === 'number' || isObject(v))),
        'Latitude': numberOf(find(obj, KEYS.lat, v => numberOf(v) !== null, 5)) ?? '',
        'Longitude': numberOf(find(obj, KEYS.lng, v => numberOf(v) !== null, 5)) ?? '',
        'Image': imageUrl(find(obj, KEYS.image, v => !!imageUrl(v))) || photos[0] || '',
        'Listing URL': linkOf(obj, base),
        'Description': scrub(plain(find(obj, KEYS.description, v => plain(v).length > 60, 2))),
        'Photos': photos.join('\n'),
      };
    };
    const collections = root => {
      const found = [];
      const seen = new Set();
      const walk = (value, depth) => {
        if (!value || typeof value !== 'object' || seen.has(value) || depth > 18) return;
        seen.add(value);
        const objects = (Array.isArray(value) ? value : Object.values(value)).filter(isObject).map(unwrap);
        if (objects.length >= 2) {
          const scores = objects.slice(0, 60).map(score);
          const good = scores.filter(Boolean).length;
          if (good >= 2 && good >= scores.length * 0.6) {
            found.push(objects.filter(score));
            return;
          }
        }
        for (const child of Array.isArray(value) ? value : Object.values(value)) walk(child, depth + 1);
      };
      walk(root, 0);
      return found;
    };
    const bestListing = (sources, base, hint) => {
      let best = null;
      let bestScore = 0;
      const seen = new Set();
      const walk = (value, depth) => {
        if (!value || typeof value !== 'object' || seen.has(value) || depth > 18) return;
        seen.add(value);
        const own = isObject(value) ? score(value) : 0;
        if (own) {
          const listing = normalize(value, base);
          const matches = (hint.url && listing['Listing URL'] === hint.url) || (hint.id && listing['Listing ID'] === hint.id);
          const total = own + (listing.Description ? 3 : 0) + (listing.Photos ? 2 : 0) + (matches ? 20 : 0);
          if (total > bestScore) { best = { listing, raw: value }; bestScore = total; }
        }
        for (const child of Array.isArray(value) ? value : Object.values(value)) walk(child, depth + 1);
      };
      sources.forEach(source => walk(source, 0));
      return best;
    };
    /* label/value pairs a listing page shows as facts: {formattedName, formattedValue}, {amenityName, amenityValues}, "Label: value" */
    const LABEL_KEYS = ['formattedName', 'amenityName', 'label', 'name', 'title', 'displayName'];
    const PAIR_VALUE_KEYS = ['formattedValue', 'amenityValues', 'value', 'values', 'displayValue', 'text'];
    const NOT_FACTS = /gender identity|sexual orientation|^(env|guid|ssid)$|listed by|listing agent|agent|presented by|courtesy of|contact/i;
    const factPairs = root => {
      const pairs = new Map();
      const seen = new Set();
      const add = (label, value) => {
        label = clean(label).replace(/:$/, '');
        value = clean(value);
        /* facts are labelled for people ("Year Built"); lowercase or camelCase labels are tracking and ad-targeting fields */
        if (label && value && /^[A-Z]/.test(label) && label.length <= 40 && value.length <= 200 && !PERSONAL.test(label) &&
            !NOT_FACTS.test(label) && !/^https?:/.test(value) && !pairs.has(label)) {
          pairs.set(label, scrub(value));
        }
      };
      const walk = (value, depth) => {
        if (!value || typeof value !== 'object' || seen.has(value) || depth > 14 || pairs.size >= 150) return;
        seen.add(value);
        if (isObject(value)) {
          const labelKey = LABEL_KEYS.find(key => typeof value[key] === 'string');
          const valueKey = PAIR_VALUE_KEYS.find(key => key !== labelKey && value[key] !== undefined && value[key] !== null &&
            (typeof value[key] !== 'object' || (Array.isArray(value[key]) && value[key].every(item => typeof item !== 'object'))));
          if (labelKey && valueKey) add(value[labelKey], Array.isArray(value[valueKey]) ? value[valueKey].join(', ') : plain(value[valueKey]));
        } else {
          value.forEach(item => {
            const match = typeof item === 'string' && item.match(/^([^:]{2,40}):\s*(.{1,200})$/);
            if (match) add(match[1], match[2]);
          });
        }
        for (const child of Array.isArray(value) ? value : Object.values(value)) walk(child, depth + 1);
      };
      walk(root, 0);
      return pairs;
    };
    const FACT_FIELDS = [
      ['Year built', /^(year built|built in|built)$/i, v => { const n = toNumber(v); return n > 1700 && n < 2100 ? n : ''; }],
      ['HOA fee', /^(hoa|hoa fee|hoa dues|association fee|hoa\/month|monthly hoa fee)$/i, v => toNumber(v) ?? ''],
      ['MLS #', /^(mls ?#|mls number|mls id|mls)$/i, v => v],
      ['Days on market', /^days on (market|trulia|redfin|zillow|realtor\.com|site)$/i, v => toNumber(v) ?? ''],
      ['Lot size', /^(lot size|lot area|lot size sq ?ft|lot size acres)$/i, v => v],
      ['Living area (sqft)', /^(living area|square feet|sq ?ft|finished sq ?ft|total sq ?ft)$/i, v => toNumber(v) ?? ''],
      ['Property type', /^(property type|home type|type)$/i, v => v],
    ];
    const parseJson = body => {
      const trimmed = String(body || '').replace(/^\s*(\{\}&&|\)\]\}',?\s*|for\s*\(;;\);)/, '');
      if (!/^\s*[[{]/.test(trimmed)) return null;
      try { return JSON.parse(trimmed); } catch (error) { return null; }
    };
    /* Next.js App Router pages stream their data as "id:JSON" lines inside self.__next_f.push([1, "..."]) calls */
    const flightSources = chunks => {
      const out = [];
      for (const line of chunks.join('').split('\n')) {
        const colon = line.indexOf(':');
        if (colon > 0 && colon < 8 && /^[[{]/.test(line.slice(colon + 1))) {
          const value = parseJson(line.slice(colon + 1));
          if (value) out.push(value);
        }
      }
      return out;
    };
    const pageSources = doc => {
      const sources = [];
      const flight = [];
      let liveFlight = false;
      try { liveFlight = doc === document && Array.isArray(window.__next_f) && window.__next_f.length > 0; } catch (error) { }
      doc.querySelectorAll('script').forEach(node => {
        const type = node.getAttribute('type') || '';
        if (node.id === '__NEXT_DATA__' || /application\/(ld\+)?json/i.test(type)) {
          const value = parseJson(node.textContent);
          if (value) sources.push(value);
        } else if (!liveFlight && node.textContent.includes('self.__next_f.push(')) {
          for (const m of node.textContent.matchAll(/self\.__next_f\.push\((\[[\s\S]*?\])\)\s*;?\s*(?=self\.__next_f|$)/g)) {
            const entry = parseJson(m[1]);
            if (Array.isArray(entry) && typeof entry[1] === 'string') flight.push(entry[1]);
          }
        }
      });
      if (doc === document) {
        try { (window.__next_f || []).forEach(entry => { if (Array.isArray(entry) && typeof entry[1] === 'string') flight.push(entry[1]); }); } catch (error) { }
        for (const name of ['__NUXT__', '__INITIAL_STATE__', '__PRELOADED_STATE__', '__APOLLO_STATE__', '__REDUX_STATE__', '__INITIAL_DATA__']) {
          try { if (window[name] && typeof window[name] === 'object') sources.push(window[name]); } catch (error) { }
        }
      }
      return sources.concat(flightSources(flight));
    };
    /* many sites spell the address in the listing link: /21619-Astipalia-Dr-Spring-TX-77388 or /2316-gentry-st-w-1-houston-tx-77009/ */
    const STREET_END = /^(st|street|dr|drive|ave|avenue|av|rd|road|ln|lane|ct|court|blvd|boulevard|way|pl|place|cir|circle|pkwy|parkway|ter|terrace|trl|trail|loop|hwy|highway|sq|square|pt|point|xing|crossing|run|pass|row|walk|bnd|bend|cv|cove|holw|hollow|gln|glen|grv|grove|hl|hill|hts|heights|lndg|landing|mdw|mdws|park|path|pike|rdg|ridge|vw|view|vis|vista|fwy|freeway|expy|plz|plaza|aly|alley|byp|cswy|ests?|estates|mnr|manor|ovlk|overlook|spg|spgs|springs|trce|trace|tpke|turnpike)$/i;
    const addressFromUrl = row => {
      if (row.Address || !row['Listing URL']) return row;
      let path;
      try { path = decodeURIComponent(new URL(row['Listing URL']).pathname); } catch (error) { return row; }
      const m = path.match(/\/(\d+[a-z]?(?:[-_][\w.#]+?)+?)[-_]([a-z]{2})[-_](\d{5})(?=[\/_.-]|$)/i);
      if (!m || !STATES[m[2].toLowerCase()] && !Object.values(STATES).includes(m[2].toUpperCase())) return row;
      const words = m[1].split(/[-_]+/);
      let end = -1;
      words.forEach((word, index) => { if (index > 0 && index < words.length - 1 && STREET_END.test(word)) end = index; });
      if (end < 0) end = words.length - 2;
      while (end + 1 < words.length - 1 && /^([nsew]|ne|nw|se|sw|apt|unit|ste|#?\d+[a-z]?)$/i.test(words[end + 1])) end += 1;
      const title = list => list.map(word => /^\d/.test(word) ? word : word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()).join(' ');
      row.Address = title(words.slice(0, end + 1));
      if (!row.City) row.City = title(words.slice(end + 1));
      if (!row.State) row.State = m[2].toUpperCase();
      if (!row.Zip) row.Zip = m[3];
      return row;
    };
    const squash = s => String(s ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const placeKeys = item => squash(item.Address) ? [item.Zip, item.City].filter(Boolean).map(part => squash(item.Address) + '|' + squash(part)) : [];
    const placeIndex = items => {
      const index = new Map();
      for (const item of items) for (const key of placeKeys(item)) if (!index.has(key)) index.set(key, item);
      return index;
    };
    const fillBlanks = (target, source, fields) => {
      for (const field of fields || Object.keys(source)) if (source[field] !== '' && source[field] !== undefined && (target[field] === '' || target[field] === undefined)) target[field] = source[field];
    };
    /* every object on the page that carries a link and an address, listing or not (schema.org Residence without a price, map pins) */
    const linkedObjects = (sources, base) => {
      const found = [];
      const walk = (value, depth) => {
        if (!value || typeof value !== 'object' || depth > 10 || found.length > 2000) return;
        if (Array.isArray(value)) return value.forEach(child => walk(child, depth + 1));
        const keys = Object.keys(value);
        if (keys.some(key => KEYS.url.test(key)) && keys.some(key => KEYS.address.test(key))) {
          const row = normalize(value, base);
          if (row['Listing URL']) found.push(row);
        }
        Object.values(value).forEach(child => walk(child, depth + 1));
      };
      sources.forEach(source => walk(source, 0));
      return found;
    };
    const listingsIn = (sources, base) => {
      const byKey = new Map();
      for (const source of sources) {
        for (const collection of collections(source)) {
          for (const item of collection) {
            const listing = normalize(item, base);
            const key = listing['Listing URL'] || listing['Listing ID'] || listing.Address;
            if (!key) continue;
            const existing = byKey.get(key);
            if (!existing) byKey.set(key, listing);
            else for (const [field, value] of Object.entries(listing)) if (value !== '' && existing[field] === '') existing[field] = value;
          }
        }
      }
      /* the same home from two sources (say JSON-LD without a link and the search data with one): fold the linkless copy in */
      const all = [...byKey.values()];
      const linked = placeIndex(all.filter(item => item['Listing URL']));
      const kept = all.filter(item => {
        const target = !item['Listing URL'] && placeKeys(item).map(key => linked.get(key)).find(Boolean);
        if (!target) return true;
        fillBlanks(target, item);
        return false;
      });
      /* homes still without a link: borrow it (and a photo) from any object on the page at the same address, e.g. a schema.org Residence */
      if (kept.some(item => !item['Listing URL'])) {
        const places = placeIndex(linkedObjects(sources, base));
        for (const item of kept) {
          const source = !item['Listing URL'] && placeKeys(item).map(key => places.get(key)).find(Boolean);
          if (source) fillBlanks(item, source, ['Listing URL', 'Image', 'Zip', 'Latitude', 'Longitude']);
        }
      }
      return kept.map(addressFromUrl);
    };
    /* sites without listing data in JSON: repeated cards (4+ alike) that each show a $ price and a link */
    const PRICE_TEXT = /\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?\s?[KkMm]\b|\$\s?\d{3,}(?:\.\d+)?/;
    const signatureOf = element => element.tagName + '.' + (element.getAttribute('class') || '').trim().split(/\s+/).slice(0, 2).join('.');
    const PRICE_RANGE = new RegExp('(' + PRICE_TEXT.source + ')\\s*(?:-|–|—|to)\\s*(' + PRICE_TEXT.source + ')', 'i');
    const NOT_LISTING_LINK = /(^|[\/_])(agents?|brokers?|offices?|realtors?|realestateagents|profiles?|teams?|contact|login|sign-?in|register|share|mortgage|lenders?|advertise|privacy|terms|help|schools?|reviews?|report)([\/_.-]|$)/i;
    const LISTING_LINK = /home-?details?|propert|listing|for-?sale|for-?rent|rental|apartment|condo|house|homes?\/|\d{5,}|-[a-z]{2}-\d{5}/i;
    const NOT_PHOTO_PART = '[class*="agent" i],[class*="realtor" i],[class*="avatar" i],[class*="logo" i],[class*="icon" i],[class*="badge" i],[class*="headshot" i]';
    const NOT_PHOTO_URL = /\.svg(\?|$)|\/(icons?|logos?|avatars?|agents?|realtors?|badges?|sprites?)\/|logo|avatar|headshot|placeholder|no[-_]?photo/i;
    const STATUS_TEXT = /\b(coming soon|for sale|for rent|for lease|under contract|contingent|pending|sold|leased|off market|new construction|auction|foreclosure)\b/i;
    const TYPE_TEXT = /\b(single[- ]family|multi[- ]family|condo(?:minium)?|townho(?:use|me)|duplex|triplex|fourplex|mobile home|manufactured home|apartment|co-?op|vacant land|lots? & land|farm|ranch)\b/i;
    const ID_ATTRIBUTES = ['data-listing-id', 'data-listingid', 'data-property-id', 'data-propertyid', 'data-pid', 'data-zpid', 'data-mlsid', 'data-id'];
    /* text with a comma where block elements meet, so <p>7715 Cloverlake Court</p><p>Houston, TX</p> reads as an address */
    const BLOCK = /^(P|DIV|BR|LI|H[1-6]|TR|TD|DT|DD|SECTION|ARTICLE|HEADER|FOOTER|ADDRESS|UL|OL)$/;
    const blockText = element => {
      let out = '';
      for (const node of element.childNodes) {
        if (node.nodeType === 3) out += node.data;
        else if (node.nodeType === 1 && !/^(SCRIPT|STYLE|TEMPLATE|SVG)$/i.test(node.nodeName)) out += BLOCK.test(node.nodeName) ? '\u0001' + blockText(node) + '\u0001' : blockText(node);
      }
      return out;
    };
    const lineOf = element => clean(blockText(element).replace(/\s*\u0001[\s\u0001]*/g, ', ')).replace(/^,\s*|,\s*$/g, '');
    const titleCase = s => s.toLowerCase().replace(/(^|[\s-])[a-z]/g, c => c.toUpperCase());
    const photoOf = (card, base) => {
      for (const element of card.querySelectorAll('img, source, [style*="background-image"], [data-src], [data-bg], [data-background-image]')) {
        const part = element.closest(NOT_PHOTO_PART);
        if (part && part !== card && card.contains(part)) continue;
        const style = (element.getAttribute('style') || '').match(/background-image\s*:\s*url\(\s*['"]?([^'")]+)/i);
        const source = element.getAttribute('data-src') || element.getAttribute('data-lazy-src') || element.getAttribute('data-bg') ||
          element.getAttribute('data-background-image') || element.getAttribute('src') ||
          (element.getAttribute('data-srcset') || element.getAttribute('srcset') || '').trim().split(/\s/)[0] || (style && style[1]) || '';
        if (!source || source.startsWith('data:') || NOT_PHOTO_URL.test(source)) continue;
        try { return new URL(source, base).href; } catch (error) { }
      }
      return '';
    };
    /* the card's own page: on-site links, scored up for listing-like paths and for carrying the street address, down for agent/office pages */
    const listingLinkOf = (card, base, street) => {
      const domainOf = host => host.split('.').slice(-2).join('.');
      const site = domainOf(new URL(base).hostname);
      const links = new Map();
      for (const anchor of card.querySelectorAll('a[href]')) {
        let url;
        try { url = new URL(anchor.getAttribute('href'), base); } catch (error) { continue; }
        if (!/^https?:$/.test(url.protocol) || domainOf(url.hostname) !== site || url.pathname.length < 2) continue;
        url.hash = '';
        const entry = links.get(url.href) || { url, score: NOT_LISTING_LINK.test(url.pathname) ? -10 : LISTING_LINK.test(url.pathname) ? 3 : 0 };
        entry.score += 1 + (street && clean(anchor.textContent).includes(street) ? 5 : 0);
        links.set(url.href, entry);
      }
      const best = [...links.values()].sort((a, b) => b.score - a.score)[0];
      return best && best.score > -5 ? best.url : null;
    };
    const cardListings = (doc, base) => {
      const cards = new Set();
      for (const element of doc.querySelectorAll('body *')) {
        if (element.children.length > 3) continue;
        const own = clean(element.textContent);
        if (own.length > 40 || !PRICE_TEXT.test(own)) continue;
        for (let card = element.parentElement, level = 0; card && card.parentElement && level < 8; card = card.parentElement, level += 1) {
          const signature = signatureOf(card);
          if ([...card.parentElement.children].filter(child => signatureOf(child) === signature).length >= 4 && card.querySelector('a[href]')) {
            cards.add(card);
            break;
          }
        }
      }
      const rows = new Map();
      for (const card of cards) {
        const all = clean(card.textContent);
        const range = all.match(PRICE_RANGE);
        const price = toNumber((range ? range[1] : (all.match(PRICE_TEXT) || [''])[0]).replace(/\$\s?/, ''));
        const texts = [...card.querySelectorAll('*')].filter(e => clean(e.textContent).length < 140).map(lineOf).sort((a, b) => a.length - b.length);
        const line = texts.find(t => /^[^,]{2,},\s*[A-Za-z .'-]{2,},?\s*[A-Z]{2}\s+\d{5}/.test(t)) || texts.find(t => /^[^,]{3,80},\s*[A-Za-z .'-]{2,40},\s*[A-Z]{2}$/.test(t)) || '';
        const parts = line.match(/^(.*?),\s*([A-Za-z .'-]+?),?\s*([A-Z]{2})(?:\s+(\d{5}))?\b/);
        const heading = [...card.querySelectorAll('h1,h2,h3,h4,[class*="title" i],[class*="address" i]')].map(lineOf)
          .find(text => text.length < 200 && !text.startsWith('$') && (text.split(' ').length >= 3 || /\d/.test(text))) || '';
        const street = parts ? parts[1] : /^\d+[A-Za-z]?\s+(?!(?:bed|br|bd|bath|ba|stor|car|unit|acre)\w*\b)[A-Za-z]/i.test(heading) ? heading.slice(0, 120) : '';
        const link = listingLinkOf(card, base, street || heading.slice(0, 40));
        if (!price || !link || rows.has(link.href)) continue;
        const full = all.match(/(\d+)\s*full(?:\s*(?:&|and|,)\s*(\d+)\s*half)?\s*baths?/i);
        const lot = all.match(/([\d.,]+)\s*(acres?)\b|([\d,]+)\s*(?:lot\s*sq\.?\s?ft|sq\.?\s?ft\.?\s*lot)/i);
        const hoa = all.match(/\$\s?([\d,]+)\s*(?:\/\s*mo(?:nth)?\.?)?\s*HOA|HOA(?:\s*(?:fee|dues))?:?\s*\$\s?([\d,]+)/i);
        const id = (link.pathname.match(/(\d{5,})(?:\.html?)?\/?$/) || [])[1] || ID_ATTRIBUTES.map(name => card.getAttribute(name)).find(Boolean) || '';
        const brokerage = [...card.querySelectorAll('a[href*="broker" i], a[href*="office" i], [class*="brokerage" i], [class*="broker-name" i]')]
          .map(element => clean(element.textContent)).find(text => text.length > 2 && text.length < 80) || '';
        const row = Object.fromEntries(LISTING_FIELDS.map(field => [field, '']));
        Object.assign(row, {
          'Listing ID': id,
          'Address': scrub(tidy(street)), 'City': parts ? tidy(parts[2]) : '', 'State': parts ? parts[3] : '', 'Zip': parts && parts[4] || '',
          'Status': titleCase((all.match(STATUS_TEXT) || [''])[0]),
          'Property type': titleCase((all.match(TYPE_TEXT) || [''])[0]),
          'Price': price,
          'Price (max)': range ? toNumber(range[2].replace(/\$\s?/, '')) ?? '' : '',
          'Beds': toNumber((all.match(/(\d+(?:\.\d+)?)\s*(?:bd|bds|bed|beds|bedrooms?|br|bdrms?)(?![a-z])/i) || [])[1]) ?? (/\bstudio\b/i.test(all) ? 0 : ''),
          'Baths': full ? Number(full[1]) + (full[2] ? Number(full[2]) / 2 : 0) : toNumber((all.match(/(\d+(?:\.\d+)?)\s*(?:ba|bath|baths|bathrooms?)(?![a-z])/i) || [])[1]) ?? '',
          'Living area (sqft)': toNumber((all.match(/([\d,]+)\s*(?:sq\.?\s?ft|sqft|square feet|ft²)/i) || [])[1]) ?? '',
          'Lot size': lot ? (lot[1] ? lot[1] + ' ' + lot[2].toLowerCase() : lot[3] + ' sqft') : '',
          'Year built': toNumber((all.match(/(?:built in|year built:?)\s*((?:18|19|20)\d\d)\b|\b((?:18|19|20)\d\d)\s*year built/i) || []).slice(1).find(Boolean)) ?? '',
          'HOA fee': hoa ? toNumber(hoa[1] || hoa[2]) ?? '' : '',
          'Days on market': toNumber((all.match(/(\d+)\s*days? on (?:market|site|[\w.]+\.com)/i) || [])[1]) ?? '',
          'Brokerage': scrub(brokerage),
          'MLS #': (all.match(/MLS\s*(?:#|number|no\.?|id)?\s*:?\s*#?\s*([A-Z]{0,4}\d[\dA-Z-]{3,})/i) || [])[1] || '',
          'Image': photoOf(card, base),
          'Listing URL': link.href,
          'Description': heading && heading !== street && !line.includes(heading) ? scrub(heading).slice(0, 300) : '',
        });
        rows.set(link.href, addressFromUrl(row));
      }
      return [...rows.values()];
    };
    const listingsOnPage = (doc, base) => {
      const sources = pageSources(doc);
      const found = listingsIn(sources, base);
      if (found.length >= 3 && found.every(item => item.Image && item['Listing URL'])) return found;
      const cards = cardListings(doc, base);
      const byUrl = new Map(cards.map(card => [card['Listing URL'], card]));
      const byPlace = placeIndex(cards);
      for (const item of found) {
        const card = byUrl.get(item['Listing URL']) || placeKeys(item).map(key => byPlace.get(key)).find(Boolean);
        if (card) fillBlanks(item, card, ['Listing URL', 'Image']);
      }
      if (found.length >= 3) return found;
      const fresh = cards.filter(card => !found.some(item => item['Listing URL'] === card['Listing URL']));
      const extras = linkedObjects(sources, base);
      const extraByUrl = new Map(extras.map(row => [row['Listing URL'], row]));
      const extraByPlace = placeIndex(extras);
      for (const card of fresh) {
        const extra = extraByUrl.get(card['Listing URL']) || placeKeys(card).map(key => extraByPlace.get(key)).find(Boolean);
        if (extra) fillBlanks(card, extra);
      }
      return found.concat(fresh);
    };
    const PAGE_PATTERNS = [/[?&](?:page|pg|p|pagenumber|currentpage)=(\d+)/i, /\/(\d+)_p\/?/, /\/p_(\d+)\/?/, /\/(?:page|pg)-(\d+)/i, /\/p(\d+)\/?$/, /\/(\d{1,3})\/?$/];
    const pageNumberOf = href => {
      const url = new URL(href, location.href);
      const tail = url.pathname + url.search;
      for (const pattern of PAGE_PATTERNS) { const m = tail.match(pattern); if (m) return Number(m[1]); }
      return 1;
    };
    const nextPageUrl = (doc, current) => {
      const here = new URL(current);
      const number = pageNumberOf(current);
      const declared = doc.querySelector('link[rel="next"][href], a[rel="next"][href]');
      if (declared) { try { return new URL(declared.getAttribute('href'), current).href; } catch (error) { } }
      for (const anchor of doc.querySelectorAll('a[aria-label*="next" i][href], a[title*="next" i][href]')) {
        try {
          const url = new URL(anchor.getAttribute('href'), current);
          if (url.origin === here.origin && pageNumberOf(url.href) === number + 1) return url.href;
        } catch (error) { }
      }
      const base = here.pathname.replace(/\/((\d+)_p|p_\d+|(page|pg)-\d+|p\d+|\d{1,3})\/?$/i, '/').replace(/\/?$/, '/');
      for (const anchor of doc.querySelectorAll('a[href]')) {
        let url;
        try { url = new URL(anchor.getAttribute('href'), current); } catch (error) { continue; }
        if (url.origin !== here.origin || url.href === here.href) continue;
        const tail = url.pathname + url.search;
        if (!(url.pathname + '/').startsWith(base) && url.pathname !== here.pathname) continue;
        if (PAGE_PATTERNS.some(pattern => { const m = tail.match(pattern); return m && Number(m[1]) === number + 1; })) return url.href;
      }
      return null;
    };

    /* ---- rows the host stages: snake_case keys that match the generic.listings / generic.products preset fields ---- */
    const LISTING_KEYS = {
      "Listing ID": "listing_id", Address: "address", City: "city", State: "state", Zip: "zip", Status: "status",
      "Property type": "property_type", Price: "price", "Price (max)": "price_max", Beds: "beds", Baths: "baths",
      "Living area (sqft)": "living_area_sqft", "Lot size": "lot_size", "Year built": "year_built", "HOA fee": "hoa_fee",
      "Days on market": "days_on_market", Listed: "listed", Brokerage: "brokerage", "MLS #": "mls_number",
      Latitude: "latitude", Longitude: "longitude", Image: "image", "Listing URL": "listing_url", Description: "description",
      Photos: "photos",
    };
    const LONG_FIELDS = { description: 4000, facts: 8000, facts_json: 9500, price_history: 4000, tax_history: 4000, schools: 2000,
      about_this_item: 6000, product_details: 6000, features_specs: 8000, additional_details: 4000, product_description: 6000,
      from_the_manufacturer: 3000, details_json: 9500 };
    const LIST_FIELDS = { photos: 40, all_images: 20 };
    const site = () => location.hostname.replace(/^www\./, "");
    /** Strings only, empty values dropped, long texts and photo lists capped. */
    const finish = (raw) => {
      const out = {};
      for (const [key, value] of Object.entries(raw)) {
        if (value === "" || value === null || value === undefined) continue;
        let text = String(value);
        if (LIST_FIELDS[key]) text = text.split("\n").filter(Boolean).slice(0, LIST_FIELDS[key]).join("\n");
        out[key] = text.slice(0, LONG_FIELDS[key] || 1000);
      }
      return out;
    };
    const record = (row, from) => {
      const out = { site: site(), collected_from: from };
      for (const [name, key] of Object.entries(LISTING_KEYS)) out[key] = row[name];
      return finish(out);
    };
    const snakeKey = (row) => row.listing_url || row.listing_id || [row.address, row.zip || row.city].filter(Boolean).join("|");
    const mergeInto = (into, row) => {
      const key = snakeKey(row);
      if (!key) return false;
      const existing = into.get(key);
      if (!existing) {
        into.set(key, row);
        return true;
      }
      for (const [field, value] of Object.entries(row)) if (existing[field] === undefined || existing[field] === "") existing[field] = value;
      return false;
    };

    /* ---- Zillow: its own search data (list results and map pins) and home pages ---- */
    const onZillow = () => /(^|\.)zillow\.com$/i.test(location.hostname);
    const nextDataOf = () => {
      const node = document.getElementById("__NEXT_DATA__");
      return node ? parseJson(node.textContent) : null;
    };
    const words = (value) => String(value || "").replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
    const zillowSearch = (data) => {
      const state = data?.props?.pageProps?.searchPageState || data;
      const out = { list: [], map: [], total: null };
      for (const category of ["cat1", "cat2"]) {
        const results = state?.[category]?.searchResults;
        if (!results) continue;
        out.list.push(...(Array.isArray(results.listResults) ? results.listResults : []));
        out.map.push(...(Array.isArray(results.mapResults) ? results.mapResults : []));
        const total = state?.[category]?.searchList?.totalResultCount;
        if (typeof total === "number") out.total = Math.max(out.total ?? 0, total);
      }
      return out;
    };
    const zillowRow = (item, from, position) => {
      const info = item.hdpData?.homeInfo || {};
      const street = item.addressStreet || info.streetAddress || String(item.address || "").split(",")[0];
      const days = info.daysOnZillow;
      return finish({
        site: site(), collected_from: from, listing_id: String(item.zpid || info.zpid || ""), address: street,
        city: item.addressCity || info.city, state: item.addressState || info.state, zip: item.addressZipcode || info.zipcode,
        status: item.statusText || words(info.homeStatus), property_type: words(info.homeType),
        price: item.unformattedPrice ?? info.price ?? toNumber(String(item.price || "")) ?? "",
        beds: item.beds ?? info.bedrooms, baths: item.baths ?? info.bathrooms, living_area_sqft: item.area ?? info.livingArea,
        lot_size: info.lotAreaValue != null ? `${info.lotAreaValue} ${info.lotAreaUnit || ""}`.trim() : "",
        zestimate: item.zestimate ?? info.zestimate, rent_zestimate: info.rentZestimate, tax_assessed_value: info.taxAssessedValue,
        days_on_market: typeof days === "number" && days >= 0 ? days : "", brokerage: scrub(item.brokerName || ""),
        latitude: item.latLong?.latitude ?? info.latitude, longitude: item.latLong?.longitude ?? info.longitude,
        image: item.imgSrc, listing_url: item.detailUrl ? new URL(item.detailUrl, location.origin).href : "",
        position: position ?? "",
      });
    };
    const zillowRows = (data, listFrom) => {
      const found = zillowSearch(data);
      const rows = [];
      found.list.forEach((item, index) => rows.push(zillowRow(item, listFrom, listFrom === "page" ? index + 1 : "")));
      found.map.forEach((item) => rows.push(zillowRow(item, "map")));
      return { rows: rows.filter((row) => row.listing_id || row.listing_url), total: found.total };
    };

    /* ---- data the site loads while the user browses (search APIs, map moves, Next.js navigations) ---- */
    const LISTING_HINT = /"(?:price|list_price|listPrice|unformattedPrice|listingPrice|askingPrice|listPriceCents)"\s*:/;
    const FOLLOW_LIMIT = 50000;
    const followed = new Map();
    let reportedTotal = null;
    const follow = (text, type) => {
      if (typeof text !== "string" || text.length > 15000000 || !LISTING_HINT.test(text) || followed.size >= FOLLOW_LIMIT) return;
      const sources = /x-component/i.test(type || "") ? flightSources([text]) : [parseJson(text)].filter(Boolean);
      for (const source of sources) {
        if (onZillow()) {
          const zillow = zillowRows(source, "browsing");
          if (zillow.rows.length) {
            zillow.rows.forEach((row) => mergeInto(followed, row));
            if (zillow.total !== null) reportedTotal = zillow.total;
            continue;
          }
        }
        listingsIn([source], location.href).forEach((row) => mergeInto(followed, record(row, "browsing")));
      }
    };

    const listings = () => {
      const all = new Map();
      let onPage = 0;
      let total = reportedTotal;
      if (onZillow()) {
        const zillow = zillowRows(nextDataOf() || {}, "page");
        zillow.rows.forEach((row) => mergeInto(all, row));
        onPage = zillow.rows.length;
        if (zillow.total !== null) total = zillow.total;
      }
      if (!onPage) {
        const rows = listingsOnPage(document, location.href);
        rows.forEach((row) => mergeInto(all, record(row, "page")));
        onPage = rows.length;
      }
      for (const row of followed.values()) mergeInto(all, { ...row });
      const records = [...all.values()].slice(0, 20000);
      return {
        url: location.href, records, on_page: onPage, from_browsing: followed.size, map_pins: records.filter((row) => row.collected_from === "map").length,
        reported_total: total, next_url: nextPageUrl(document, location.href), error: null,
      };
    };

    /* ---- Amazon search results: one row per product card ---- */
    const westernDigits = (s) => (s || "").replace(/[٠-٩]/g, (d) => d.charCodeAt(0) - 0x0660).replace(/٫/g, ".").replace(/٬/g, ",");
    const cleanText = (s) => westernDigits(s).replace(/[‎‏]/g, "").replace(/\s+/g, " ").trim();
    const textOf = (element) => {
      if (!element) return "";
      const copy = element.cloneNode(true);
      copy.querySelectorAll("script, style, noscript").forEach((node) => node.remove());
      return cleanText(copy.textContent);
    };
    const textIn = (root, selector) => textOf(root.querySelector(selector));
    const amount = (s) => {
      const match = cleanText(s).replace(/,/g, "").match(/\d+(?:\.\d+)?/);
      return match ? match[0] : "";
    };
    const productCards = () => [...document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]')].filter((card) => card.dataset.asin);
    const deliveryOf = (card) => {
      const box = card.querySelector('[data-cy="delivery-recipe"]');
      if (!box) return "";
      const rows = [...box.querySelectorAll(".a-row")].filter((row) => !row.querySelector(".a-row")).map((row) => textOf(row)).filter(Boolean);
      return rows.length ? rows.join(" · ") : textOf(box);
    };
    const lastPage = () => Number([...document.querySelectorAll(".s-pagination-item")].map((e) => cleanText(e.textContent)).filter((t) => /^\d+$/.test(t)).pop()) || null;
    const products = () => {
      const page = pageNumberOf(location.href);
      const records = productCards().slice(0, 1000).map((card, index) => {
        const asin = card.dataset.asin;
        const recipe = card.querySelector('[data-cy="title-recipe"]') || card;
        const headings = [...recipe.querySelectorAll("h2")].map((h) => textOf(h)).filter(Boolean);
        const priceText = textIn(card, ".a-price:not(.a-text-price) .a-offscreen");
        const countLabel = [...card.querySelectorAll('[data-cy="reviews-block"] [aria-label]')]
          .map((element) => cleanText(element.getAttribute("aria-label"))).find((label) => /\d/.test(label) && !/out of|stars|من 5|نجوم/.test(label)) || "";
        const image = (card.querySelector("img.s-image")?.getAttribute("src") || "").replace(/\._[^/]*_\.(jpe?g|png|webp)$/i, ".$1");
        return finish({
          site: site(), asin, title: headings[headings.length - 1] || "", brand: headings.length > 1 ? headings[0] : "",
          price: amount(priceText), currency: (priceText.match(/[A-Z]{3}/) || [""])[0], list_price: amount(textIn(card, ".a-price.a-text-price .a-offscreen")),
          rating: amount(textIn(card, ".a-icon-alt")), ratings_count: amount(countLabel), badge: textIn(card, ".a-badge-text"),
          sponsored: card.querySelector(".puis-sponsored-label-text") ? "yes" : "", delivery: deliveryOf(card), page: String(page), position: String(index + 1),
          image: /^https:\/\//.test(image) ? image : "", product_url: location.origin + "/dp/" + asin,
        });
      });
      const last = lastPage();
      const next = last !== null && page >= last ? null : nextPageUrl(document, location.href);
      return { url: location.href, records, page, last_page: last, next_url: next, error: null };
    };

    /* ---- detail pages: one listing or product, with its facts ---- */
    const pairsOf = (rows) => {
      const seen = new Map();
      rows.forEach(([key, value]) => {
        key = cleanText(key).replace(/\s*:\s*$/, "");
        value = cleanText(value).replace(/^:\s*/, "");
        if (key && value && !seen.has(key)) seen.set(key, value);
      });
      return [...seen];
    };
    const lines = (entries) => entries.map(([key, value]) => key + ": " + value).join("\n");
    const productDetails = () => {
      const doc = document;
      const html = doc.documentElement.outerHTML;
      const facts = pairsOf([
        ...[...doc.querySelectorAll("#productFactsDesktopExpander .product-facts-detail")].map((row) => {
          const cells = row.querySelectorAll(".a-fixed-left-grid-col");
          return [textOf(cells[0]), textOf(cells[1])];
        }),
        ...[...doc.querySelectorAll("#productOverview_feature_div tr")].map((tr) => [textOf(tr.cells[0]), textOf(tr.cells[1])]),
      ]);
      const about = [...new Set([...doc.querySelectorAll("#productFactsDesktopExpander ul li, #feature-bullets ul li")].map((li) => textOf(li)).filter(Boolean))];
      const specs = [];
      let section = "";
      doc.querySelectorAll("#voyager-ns-desktop-side-sheet-main-section h1, #voyager-ns-desktop-side-sheet-main-section h2, #voyager-ns-desktop-side-sheet-main-section h3, #voyager-ns-desktop-side-sheet-main-section tr")
        .forEach((element) => {
          if (element.tagName !== "TR") section = textOf(element);
          else if (element.cells.length >= 2) specs.push([section, textOf(element.cells[0]), textOf(element.cells[1])]);
        });
      doc.querySelectorAll("#productDetails_techSpec_section_1 tr, #productDetails_techSpec_section_2 tr, #productDetails_detailBullets_sections1 tr")
        .forEach((tr) => { if (tr.cells.length >= 2) specs.push(["Technical details", textOf(tr.cells[0]), textOf(tr.cells[1])]); });
      const additional = pairsOf([...doc.querySelectorAll("#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li")]
        .map((li) => [li, li.querySelector("span.a-text-bold")]).filter(([, bold]) => bold)
        .map(([li, bold]) => [textOf(bold), textOf(li).slice(textOf(bold).length)]))
        .filter(([key]) => !/^(ASIN|Customer reviews?)$/i.test(key));
      const specText = [];
      let lastSection = null;
      specs.forEach(([name, key, value]) => {
        if (name !== lastSection) specText.push("[" + (name || "Specifications") + "]");
        lastSection = name;
        specText.push(key + ": " + value);
      });
      const merged = {};
      [...facts, ...specs.map(([, key, value]) => [key, value]), ...additional].forEach(([key, value]) => {
        if (key && value && !(key in merged) && key !== "Best Sellers Rank") merged[key] = value;
      });
      const images = [...new Set((html.match(/"hiRes":"(https:[^"]+)"/g) || []).map((match) => match.slice(9, -1)))];
      const asin = (location.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/) || [])[1] || "";
      return finish({
        site: site(), asin, title: textIn(doc, "#productTitle"),
        seller: textIn(doc, "#sellerProfileTriggerId") || textIn(doc, "#merchantInfoFeature_feature_div .offer-display-feature-text-message"),
        fulfilled_by: textIn(doc, "#fulfillerInfoFeature_feature_div .offer-display-feature-text-message"),
        availability: textIn(doc, "#availability"),
        category_path: [...doc.querySelectorAll("#wayfinding-breadcrumbs_feature_div li a")].map((a) => textOf(a)).join(" › "),
        best_sellers_rank: (additional.find(([key]) => key === "Best Sellers Rank") || ["", ""])[1],
        about_this_item: about.join("\n"), product_details: lines(facts), features_specs: specText.join("\n"),
        additional_details: lines(additional.filter(([key]) => key !== "Best Sellers Rank")),
        product_description: textIn(doc, "#productDescription"), from_the_manufacturer: textIn(doc, "#aplus"),
        all_images: images.join("\n"), details_json: JSON.stringify(merged), details_collected_at: new Date().toISOString(),
      });
    };
    const ZILLOW_PERSONAL = /agent|phone|email|contact|owner name|listed by/i;
    const label = (key) => key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase());
    const zillowProperty = () => {
      const raw = nextDataOf()?.props?.pageProps?.componentProps?.gdpClientCache;
      if (!raw) return null;
      try {
        const cache = typeof raw === "string" ? JSON.parse(raw) : raw;
        return Object.values(cache).find((entry) => entry && entry.property)?.property || null;
      } catch {
        return null;
      }
    };
    const flatFacts = (facts) => {
      const out = {};
      Object.entries(facts || {}).forEach(([key, value]) => {
        if (ZILLOW_PERSONAL.test(key) || value === null || value === "" || value === false) return;
        if (Array.isArray(value)) {
          if (value.length && value.every((v) => v !== null && typeof v !== "object")) out[label(key)] = value.join(", ");
        } else if (typeof value !== "object") out[label(key)] = String(value);
      });
      (facts?.atAGlanceFacts || []).forEach((fact) => {
        if (fact?.factLabel && fact.factValue != null && !ZILLOW_PERSONAL.test(fact.factLabel)) out[fact.factLabel] = String(fact.factValue);
      });
      return out;
    };
    const largestPhoto = (photo) => {
      const jpeg = photo?.mixedSources?.jpeg || [];
      return jpeg.reduce((best, source) => ((source.width || 0) > (best.width || 0) ? source : best), {}).url || photo?.url || "";
    };
    const heading = (title) => [...document.querySelectorAll("h2")].find((h) => clean(h.textContent) === title);
    const follows = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    const tableAfter = (h) => {
      if (!h) return null;
      const nextHeading = [...document.querySelectorAll("h2")].find((other) => follows(h, other));
      return [...document.querySelectorAll("table")].find((table) => follows(h, table) && (!nextHeading || follows(table, nextHeading))) || null;
    };
    const tableLines = (table) => [...table.rows].filter((row) => row.cells.length >= 3 && !/^date$|^year$/i.test(clean(row.cells[0].textContent)))
      .map((row) => [...row.cells].map((cell) => clean(cell.textContent)).join(" | ")).join("\n");
    const zillowDetails = () => {
      const property = zillowProperty();
      const out = {};
      if (property) {
        const reso = property.resoFacts || {};
        const facts = flatFacts(reso);
        const attribution = property.attributionInfo || {};
        const subtype = property.listingSubType || property.listing_sub_type || {};
        Object.assign(out, {
          listing_id: String(property.zpid || ""), address: property.streetAddress || property.address?.streetAddress, city: property.city,
          state: property.state, zip: property.zipcode, status: words(property.homeStatus), property_type: words(property.homeType),
          price: property.price, beds: property.bedrooms, baths: property.bathrooms, living_area_sqft: property.livingArea ?? property.livingAreaValue,
          zestimate: property.zestimate, rent_zestimate: property.rentZestimate, latitude: property.latitude, longitude: property.longitude,
          brokerage: scrub(attribution.brokerName || ""), year_built: property.yearBuilt ?? reso.yearBuilt,
          lot_size: reso.lotSize || (property.lotAreaValue ? `${property.lotAreaValue} ${property.lotAreaUnits || ""}`.trim() : ""),
          price_per_sqft: reso.pricePerSquareFoot, hoa_fee: property.monthlyHoaFee, property_tax_rate: property.propertyTaxRate,
          page_views: property.pageViewCount, favorites: property.favoriteCount,
          listed: property.datePosted ? new Date(property.datePosted).toISOString().slice(0, 10) : "",
          mls_number: property.mlsid || attribution.mlsId, mls_name: attribution.mlsName,
          listing_type: Object.entries(subtype).filter(([, value]) => value === true).map(([key]) => key.replace(/^is_?/, "")).join(", "),
          description: scrub(property.description || ""),
          facts: scrub(Object.entries(facts).map(([key, value]) => key + ": " + value).join("\n")),
          price_history: (property.priceHistory || []).map((entry) => [entry.date, entry.event, entry.price].filter((v) => v != null && v !== "").join(" | ")).join("\n"),
          tax_history: (property.taxHistory || []).map((entry) => [entry.time ? new Date(entry.time).getFullYear() : "", entry.taxPaid != null ? "tax " + entry.taxPaid : "",
            entry.value != null ? "assessed " + entry.value : ""].filter(Boolean).join(" | ")).join("\n"),
          schools: (property.schools || []).map((school) => [school.name, school.grades, school.distance != null ? school.distance + " mi" : "",
            school.rating != null ? school.rating + "/10" : ""].filter(Boolean).join(" | ")).join("\n"),
          photos: (property.responsivePhotos || property.photos || []).map(largestPhoto).filter(Boolean).join("\n"),
          facts_json: JSON.stringify(facts),
        });
      }
      // The rendered page fills what the embedded data lacks (Zillow loads some sections as the page scrolls).
      const module = document.querySelector('[data-testid="facts-and-features-module"]');
      if (module && !out.facts) {
        let group = "";
        const factLines = [];
        module.querySelectorAll("h3, h6, li").forEach((element) => {
          const value = clean(element.textContent);
          if (!value) return;
          if (element.tagName === "H3") return void factLines.push("[" + value + "]");
          if (element.tagName === "H6") return void (group = value);
          if (ZILLOW_PERSONAL.test(group) || ZILLOW_PERSONAL.test(value.split(":")[0])) return;
          factLines.push(scrub((group ? group + " › " : "") + value));
        });
        out.facts = factLines.join("\n");
      }
      const priceTable = tableAfter(heading("Price history"));
      if (priceTable && !out.price_history) out.price_history = tableLines(priceTable);
      const taxTable = tableAfter(heading("Public tax history"));
      if (taxTable && taxTable !== priceTable && !out.tax_history) out.tax_history = tableLines(taxTable);
      return out;
    };
    /* Flat page-data objects inside ordinary scripts, e.g. analytics({"beds":"3","sqft":"1,988","mlsNumber":"…"}). */
    const LISTING_DATA = /"(?:beds|bedrooms|baths|bathrooms|sqft|livingArea|mlsNumber|mlsId|yearBuilt|lotSize|listPrice|price)"\s*:/;
    const inlineObjects = () => {
      const found = [];
      document.querySelectorAll("script:not([type]), script[type='text/javascript']").forEach((node) => {
        const text = node.textContent || "";
        if (text.length > 2000000 || !LISTING_DATA.test(text)) return;
        for (const match of text.matchAll(/\{[^{}]{20,6000}\}/g)) {
          if (found.length >= 20 || !LISTING_DATA.test(match[0])) continue;
          const value = parseJson(match[0]);
          if (isObject(value)) found.push(value);
        }
        // Analytics pushes are often not valid JSON (single quotes, nesting): read the "key": value pairs around the listing fields.
        const at = text.search(LISTING_DATA);
        const around = text.slice(Math.max(0, at - 2000), at + 2000);
        const flat = {};
        for (const pair of around.matchAll(/"([A-Za-z_]\w{1,40})"\s*:\s*(?:"([^"\\]{0,300})"|(-?\d+(?:\.\d+)?)(?=\s*[,}]))/g)) {
          if (!(pair[1] in flat)) flat[pair[1]] = pair[2] !== undefined ? pair[2] : Number(pair[3]);
        }
        if (found.length < 20 && Object.keys(flat).filter((key) => LISTING_DATA.test(`"${key}":`)).length >= 3) found.push(flat);
      });
      return found;
    };
    /* Labelled facts in the page's HTML: definition lists, two-cell table rows, and "Label: value" items. */
    const domFacts = () => {
      const pairs = [];
      const skip = (element) => !!element.closest("nav, header, footer, form, [role=navigation]");
      document.querySelectorAll("dt").forEach((dt) => {
        const dd = dt.nextElementSibling;
        if (dd && dd.tagName === "DD" && !skip(dt)) pairs.push([dt.textContent, dd.textContent]);
      });
      document.querySelectorAll("tr").forEach((tr) => {
        const cells = tr.querySelectorAll("th, td");
        if (cells.length === 2 && !skip(tr)) pairs.push([cells[0].textContent, cells[1].textContent]);
      });
      document.querySelectorAll("li, p, span, div").forEach((element) => {
        if (element.children.length > 3 || skip(element)) return;
        const text = clean(element.textContent);
        const match = text.length <= 160 && text.match(/^([A-Z][A-Za-z0-9 /&#().'-]{1,38}):\s*(\S.{0,119})$/);
        if (match) pairs.push([match[1], match[2]]);
      });
      return pairs;
    };
    const metaContent = (selector) => document.querySelector(selector)?.getAttribute("content") || "";
    const pageDetails = (hint) => {
      const sources = pageSources(document);
      const inline = inlineObjects();
      const best = bestListing(sources.concat(inline), location.href, { url: hint?.url || location.href, id: hint?.id || "" });
      const out = best ? record(best.listing, "details") : {};
      // Objects that describe this page (schema.org Residence / Product for its URL) and flat page-data objects fill the gaps.
      const here = location.origin + location.pathname;
      const samePage = (url) => {
        try {
          const target = new URL(url);
          return target.origin + target.pathname === here || (!!hint?.url && url === hint.url);
        } catch {
          return false;
        }
      };
      const describing = [];
      const walk = (value, depth) => {
        if (!value || typeof value !== "object" || depth > 8 || describing.length > 100) return;
        if (Array.isArray(value)) return value.forEach((child) => walk(child, depth + 1));
        if (Object.keys(value).some((key) => KEYS.url.test(key))) {
          const row = normalize(value, location.href);
          if (samePage(row["Listing URL"])) describing.push(row);
        }
        Object.values(value).forEach((child) => walk(child, depth + 1));
      };
      sources.forEach((source) => walk(source, 0));
      inline.forEach((value) => describing.push(normalize(value, location.href)));
      for (const row of describing) {
        for (const [key, value] of Object.entries(record(row, "details"))) if (out[key] === undefined || out[key] === "") out[key] = value;
      }
      const pairs = factPairs(best ? best.raw : sources);
      const add = (label, value) => {
        label = clean(label).replace(/:$/, "");
        value = clean(value);
        if (label && value && /^[A-Z]/.test(label) && label.length <= 40 && value.length <= 200 && !PERSONAL.test(label) && !NOT_FACTS.test(label) &&
            !/^https?:/.test(value) && !pairs.has(label) && pairs.size < 150) pairs.set(label, scrub(value));
      };
      domFacts().forEach(([label, value]) => add(label, value));
      for (const [field, pattern, convert] of FACT_FIELDS) {
        const key = LISTING_KEYS[field];
        const name = [...pairs.keys()].find((n) => pattern.test(n));
        const value = name === undefined ? "" : convert(pairs.get(name));
        if (value !== "" && (out[key] === undefined || out[key] === "")) out[key] = value;
      }
      if (!out.mls_number) out.mls_number = (clean(document.title).match(/MLS\s*#?\s*:?\s*([A-Z0-9-]{4,})/i) || [])[1] || "";
      if (!out.description) {
        const summary = metaContent("meta[property='og:description']") || metaContent("meta[name='description']");
        if (summary.length > 60) out.description = scrub(summary);
      }
      if (!out.image) out.image = imageUrl(metaContent("meta[property='og:image']"));
      if (!out.year_built) out.year_built = (String(out.description || "").match(/\bbuilt in ((?:18|19|20)\d\d)\b/i) || [])[1] || "";
      if (pairs.size) {
        out.facts = [...pairs].map(([name, value]) => name + ": " + value).join("\n");
        out.facts_json = JSON.stringify(Object.fromEntries(pairs));
      }
      delete out.collected_from;
      return out;
    };
    const details = (hint) => {
      if (/(^|\.)amazon\.[a-z.]+$/i.test(location.hostname)) {
        return { url: location.href, kind: "products", record: productDetails(), error: null };
      }
      const raw = onZillow() ? zillowDetails() : pageDetails(hint);
      const out = finish({ ...raw, details_collected_at: new Date().toISOString() });
      delete out.collected_from;
      return { url: location.href, kind: "listings", record: out, error: null };
    };

    const nextPage = () => {
      const next = nextPageUrl(document, location.href);
      return { url: location.href, page: pageNumberOf(location.href), next_url: next, next_page: next ? pageNumberOf(next) : null, error: null };
    };
    return { follow, listings, products, details, nextPage, followedCount: () => followed.size };
  })();

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
    listings() {
      try {
        return collectors.listings();
      } catch (error) {
        return { url: location.href, records: [], on_page: 0, from_browsing: 0, map_pins: 0, reported_total: null, next_url: null, error: String(error) };
      }
    },
    products() {
      try {
        return collectors.products();
      } catch (error) {
        return { url: location.href, records: [], next_url: null, error: String(error) };
      }
    },
    details(hint) {
      try {
        return collectors.details(hint && typeof hint === "object" ? hint : {});
      } catch (error) {
        return { url: location.href, kind: null, record: {}, error: String(error) };
      }
    },
    nextPage() {
      try {
        return collectors.nextPage();
      } catch (error) {
        return { url: location.href, page: 1, next_url: null, next_page: null, error: String(error) };
      }
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
