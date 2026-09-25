/** Source types shown in the Scraping tab, and how presets are sorted into them. */

export type SourceKind = "website" | "sitemap" | "feed" | "crawl" | "api" | "xml" | "repository" | "documents" | "archive" | "bulk";

export type SiteCatalogEntry = {
  site_id: string;
  site_name: string;
  site_category: string;
  domains: string[];
  recommended_method: "visual_studio" | "structured_or_article";
  suggested_fields: string[];
  requires_rendered: boolean;
  example_url: string;
  variants?: SiteCatalogVariant[];
};

export type SiteCatalogVariant = {
  id: string;
  label: string;
  domain: string;
  example_url: string;
};

type BuiltInSite = readonly [site_id: string, site_name: string, site_category: string, domain: string, example_url: string];

const BUILT_IN_SITE_SEEDS: readonly BuiltInSite[] = [
  ["amazon", "Amazon", "marketplace", "amazon.com", "https://www.amazon.com/s?k=laptop"],
  ["ebay", "eBay", "marketplace", "ebay.com", "https://www.ebay.com/sch/i.html?_nkw=laptop"],
  ["walmart", "Walmart", "marketplace", "walmart.com", "https://www.walmart.com/search?q=laptop"],
  ["target", "Target", "marketplace", "target.com", "https://www.target.com/s?searchTerm=laptop"],
  ["best-buy", "Best Buy", "marketplace", "bestbuy.com", "https://www.bestbuy.com/site/searchpage.jsp?st=laptop"],
  ["etsy", "Etsy", "marketplace", "etsy.com", "https://www.etsy.com/search?q=desk+lamp"],
  ["aliexpress", "AliExpress", "marketplace", "aliexpress.com", "https://www.aliexpress.com/w/wholesale-laptop.html"],
  ["newegg", "Newegg", "marketplace", "newegg.com", "https://www.newegg.com/p/pl?d=laptop"],
  ["zillow", "Zillow", "real_estate", "zillow.com", "https://www.zillow.com/homes/"],
  ["realtor", "Realtor.com", "real_estate", "realtor.com", "https://www.realtor.com/realestateandhomes-search/New-York_NY"],
  ["redfin", "Redfin", "real_estate", "redfin.com", "https://www.redfin.com/city/30749/NY/New-York"],
  ["trulia", "Trulia", "real_estate", "trulia.com", "https://www.trulia.com/NY/New_York/"],
  ["homes", "Homes.com", "real_estate", "homes.com", "https://www.homes.com/new-york-ny/"],
  ["apartments", "Apartments.com", "real_estate", "apartments.com", "https://www.apartments.com/new-york-ny/"],
  ["loopnet", "LoopNet", "real_estate", "loopnet.com", "https://www.loopnet.com/search/commercial-real-estate/new-york-ny/for-sale/"],
  ["propertyshark", "PropertyShark", "real_estate", "propertyshark.com", "https://www.propertyshark.com/mason/Property-Search/"],
  ["indeed", "Indeed", "jobs", "indeed.com", "https://www.indeed.com/jobs?q=software+engineer"],
  ["linkedin-jobs", "LinkedIn Jobs", "jobs", "linkedin.com", "https://www.linkedin.com/jobs/search/?keywords=software%20engineer"],
  ["glassdoor", "Glassdoor", "jobs", "glassdoor.com", "https://www.glassdoor.com/Job/jobs.htm?sc.keyword=software%20engineer"],
  ["ziprecruiter", "ZipRecruiter", "jobs", "ziprecruiter.com", "https://www.ziprecruiter.com/jobs-search?search=software+engineer"],
  ["wellfound", "Wellfound", "jobs", "wellfound.com", "https://wellfound.com/jobs"],
  ["monster", "Monster", "jobs", "monster.com", "https://www.monster.com/jobs/search?q=software-engineer"],
  ["google-maps", "Google Maps", "local", "google.com", "https://www.google.com/maps/search/restaurants"],
  ["yelp", "Yelp", "local", "yelp.com", "https://www.yelp.com/search?find_desc=restaurants"],
  ["yellow-pages", "Yellow Pages", "local", "yellowpages.com", "https://www.yellowpages.com/search?search_terms=restaurants"],
  ["tripadvisor", "Tripadvisor", "local", "tripadvisor.com", "https://www.tripadvisor.com/Restaurants"],
  ["foursquare", "Foursquare", "local", "foursquare.com", "https://foursquare.com/explore"],
  ["reddit", "Reddit", "community", "reddit.com", "https://www.reddit.com/r/dataengineering/"],
  ["wikipedia", "Wikipedia", "content", "wikipedia.org", "https://en.wikipedia.org/wiki/Web_scraping"],
  ["medium", "Medium", "content", "medium.com", "https://medium.com/tag/data-engineering"],
  ["publishers", "Major publishers", "content", "reuters.com", "https://www.reuters.com/technology/"],
  ["youtube", "YouTube", "media", "youtube.com", "https://www.youtube.com/results?search_query=data+engineering"],
  ["imdb", "IMDb", "media", "imdb.com", "https://www.imdb.com/chart/top/"],
  ["rotten-tomatoes", "Rotten Tomatoes", "media", "rottentomatoes.com", "https://www.rottentomatoes.com/browse/movies_at_home/"],
  ["github", "GitHub", "developer", "github.com", "https://github.com/search?q=web+scraping&type=repositories"],
  ["stack-overflow", "Stack Overflow", "developer", "stackoverflow.com", "https://stackoverflow.com/questions/tagged/web-scraping"],
  ["product-hunt", "Product Hunt", "developer", "producthunt.com", "https://www.producthunt.com/"],
  ["npm", "npm", "developer", "npmjs.com", "https://www.npmjs.com/search?q=scraping"],
];

const SITE_FIELDS: Record<string, string[]> = {
  marketplace: ["title", "price", "rating", "reviews", "seller", "url"],
  real_estate: ["full address", "building", "house number", "street", "unit", "neighborhood", "district", "city", "county", "state or region", "country", "postal code", "latitude", "longitude", "price", "property type", "beds", "baths", "area", "parcel or listing ID", "agent", "url"],
  jobs: ["job title", "company", "location", "salary", "posted date", "url"],
  local: ["name", "category", "full address", "street", "neighborhood", "district", "city", "county", "state or region", "country", "postal code", "latitude", "longitude", "phone", "rating", "reviews", "url"],
  community: ["title", "author", "community", "score", "comments", "date", "url"],
  content: ["title", "author", "published date", "article text", "url"],
  media: ["title", "creator", "rating", "release date", "duration", "url"],
  developer: ["name", "owner", "description", "language", "stars", "version", "url"],
};

const REGIONAL_SITE_VARIANTS: Record<string, SiteCatalogVariant[]> = {
  amazon: [
    ["us", "United States", "amazon.com"], ["uk", "United Kingdom", "amazon.co.uk"], ["eg", "Egypt", "amazon.eg"], ["ae", "United Arab Emirates", "amazon.ae"],
    ["sa", "Saudi Arabia", "amazon.sa"], ["ca", "Canada", "amazon.ca"], ["mx", "Mexico", "amazon.com.mx"], ["br", "Brazil", "amazon.com.br"],
    ["de", "Germany", "amazon.de"], ["fr", "France", "amazon.fr"], ["it", "Italy", "amazon.it"], ["es", "Spain", "amazon.es"], ["nl", "Netherlands", "amazon.nl"],
    ["pl", "Poland", "amazon.pl"], ["se", "Sweden", "amazon.se"], ["be", "Belgium", "amazon.com.be"], ["tr", "Turkey", "amazon.com.tr"], ["ie", "Ireland", "amazon.ie"],
    ["in", "India", "amazon.in"], ["jp", "Japan", "amazon.co.jp"], ["au", "Australia", "amazon.com.au"], ["sg", "Singapore", "amazon.sg"], ["za", "South Africa", "amazon.co.za"],
  ].map(([id, label, domain]) => ({ id, label, domain, example_url: `https://www.${domain}/s?k=laptop` })),
  ebay: [
    ["us", "United States", "ebay.com"], ["uk", "United Kingdom", "ebay.co.uk"], ["ca", "Canada", "ebay.ca"], ["au", "Australia", "ebay.com.au"],
    ["de", "Germany", "ebay.de"], ["fr", "France", "ebay.fr"], ["it", "Italy", "ebay.it"], ["es", "Spain", "ebay.es"], ["at", "Austria", "ebay.at"],
    ["be", "Belgium", "ebay.be"], ["ch", "Switzerland", "ebay.ch"], ["ie", "Ireland", "ebay.ie"], ["nl", "Netherlands", "ebay.nl"], ["pl", "Poland", "ebay.pl"],
    ["hk", "Hong Kong", "ebay.com.hk"], ["ph", "Philippines", "ebay.ph"], ["jp", "Japan", "ebay.co.jp"], ["vn", "Vietnam", "ebay.vn"],
  ].map(([id, label, domain]) => ({ id, label, domain, example_url: `https://www.${domain}/sch/i.html?_nkw=laptop` })),
  walmart: [
    { id: "us", label: "United States", domain: "walmart.com", example_url: "https://www.walmart.com/search?q=laptop" },
    { id: "ca", label: "Canada", domain: "walmart.ca", example_url: "https://www.walmart.ca/search?q=laptop" },
    { id: "mx", label: "Mexico", domain: "walmart.com.mx", example_url: "https://www.walmart.com.mx/search?q=laptop" },
  ],
  "best-buy": [
    { id: "us", label: "United States", domain: "bestbuy.com", example_url: "https://www.bestbuy.com/site/searchpage.jsp?st=laptop" },
    { id: "ca", label: "Canada", domain: "bestbuy.ca", example_url: "https://www.bestbuy.ca/en-ca/search?search=laptop" },
  ],
  newegg: [
    { id: "us", label: "United States", domain: "newegg.com", example_url: "https://www.newegg.com/p/pl?d=laptop" },
    { id: "ca", label: "Canada", domain: "newegg.ca", example_url: "https://www.newegg.ca/p/pl?d=laptop" },
  ],
  indeed: [
    ["us", "United States", "indeed.com"], ["ar", "Argentina", "ar.indeed.com"], ["au", "Australia", "au.indeed.com"], ["at", "Austria", "at.indeed.com"],
    ["bh", "Bahrain", "bh.indeed.com"], ["be", "Belgium", "be.indeed.com"], ["br", "Brazil", "br.indeed.com"], ["ca", "Canada", "ca.indeed.com"],
    ["cl", "Chile", "cl.indeed.com"], ["cn", "China", "cn.indeed.com"], ["co", "Colombia", "co.indeed.com"], ["cr", "Costa Rica", "cr.indeed.com"],
    ["cz", "Czech Republic", "cz.indeed.com"], ["dk", "Denmark", "dk.indeed.com"], ["ec", "Ecuador", "ec.indeed.com"], ["eg", "Egypt", "eg.indeed.com"],
    ["fi", "Finland", "fi.indeed.com"], ["fr", "France", "fr.indeed.com"], ["de", "Germany", "de.indeed.com"], ["gr", "Greece", "gr.indeed.com"],
    ["hk", "Hong Kong", "hk.indeed.com"], ["hu", "Hungary", "hu.indeed.com"], ["in", "India", "in.indeed.com"], ["id", "Indonesia", "id.indeed.com"],
    ["ie", "Ireland", "ie.indeed.com"], ["il", "Israel", "il.indeed.com"], ["it", "Italy", "it.indeed.com"], ["jp", "Japan", "jp.indeed.com"],
    ["kw", "Kuwait", "kw.indeed.com"], ["lu", "Luxembourg", "lu.indeed.com"], ["my", "Malaysia", "malaysia.indeed.com"], ["mx", "Mexico", "mx.indeed.com"],
    ["ma", "Morocco", "ma.indeed.com"], ["nl", "Netherlands", "nl.indeed.com"], ["nz", "New Zealand", "nz.indeed.com"], ["ng", "Nigeria", "ng.indeed.com"],
    ["no", "Norway", "no.indeed.com"], ["om", "Oman", "om.indeed.com"], ["pk", "Pakistan", "pk.indeed.com"], ["pa", "Panama", "pa.indeed.com"],
    ["pe", "Peru", "pe.indeed.com"], ["ph", "Philippines", "ph.indeed.com"], ["pl", "Poland", "pl.indeed.com"], ["pt", "Portugal", "pt.indeed.com"],
    ["qa", "Qatar", "qa.indeed.com"], ["ro", "Romania", "ro.indeed.com"], ["sa", "Saudi Arabia", "sa.indeed.com"], ["sg", "Singapore", "sg.indeed.com"],
    ["za", "South Africa", "za.indeed.com"], ["kr", "South Korea", "kr.indeed.com"], ["es", "Spain", "es.indeed.com"], ["se", "Sweden", "se.indeed.com"],
    ["ch", "Switzerland", "ch.indeed.com"], ["tw", "Taiwan", "tw.indeed.com"], ["th", "Thailand", "th.indeed.com"], ["tr", "Turkey", "tr.indeed.com"],
    ["ua", "Ukraine", "ua.indeed.com"], ["ae", "United Arab Emirates", "ae.indeed.com"], ["uk", "United Kingdom", "uk.indeed.com"],
    ["uy", "Uruguay", "uy.indeed.com"], ["ve", "Venezuela", "ve.indeed.com"], ["vn", "Vietnam", "vn.indeed.com"],
  ].map(([id, label, domain]) => ({ id, label, domain, example_url: `https://${domain}/jobs?q=engineer` })),
  glassdoor: [
    ["us", "United States", "glassdoor.com"], ["uk", "United Kingdom", "glassdoor.co.uk"], ["ca", "Canada", "glassdoor.ca"], ["au", "Australia", "glassdoor.com.au"],
    ["de", "Germany", "glassdoor.de"], ["fr", "France", "glassdoor.fr"], ["in", "India", "glassdoor.co.in"],
  ].map(([id, label, domain]) => ({ id, label, domain, example_url: `https://www.${domain}/Job/jobs.htm?sc.keyword=engineer` })),
  monster: [
    ["us", "United States", "monster.com"], ["uk", "United Kingdom", "monster.co.uk"], ["ca", "Canada", "monster.ca"], ["de", "Germany", "monster.de"],
    ["fr", "France", "monster.fr"], ["ie", "Ireland", "monster.ie"], ["in", "India", "monster.co.in"], ["sg", "Singapore", "monster.com.sg"],
  ].map(([id, label, domain]) => ({ id, label, domain, example_url: `https://www.${domain}/jobs/search?q=engineer` })),
  "google-maps": [
    ["global", "Global", "google.com"], ["eg", "Egypt", "google.com.eg"], ["uk", "United Kingdom", "google.co.uk"], ["ca", "Canada", "google.ca"],
    ["au", "Australia", "google.com.au"], ["de", "Germany", "google.de"], ["fr", "France", "google.fr"], ["in", "India", "google.co.in"], ["jp", "Japan", "google.co.jp"],
  ].map(([id, label, domain]) => ({ id, label, domain, example_url: `https://www.${domain}/maps/search/cafes` })),
  yelp: [
    ["us", "United States", "yelp.com"], ["uk", "United Kingdom", "yelp.co.uk"], ["ca", "Canada", "yelp.ca"], ["au", "Australia", "yelp.com.au"],
    ["de", "Germany", "yelp.de"], ["fr", "France", "yelp.fr"], ["es", "Spain", "yelp.es"], ["it", "Italy", "yelp.it"], ["jp", "Japan", "yelp.co.jp"],
  ].map(([id, label, domain]) => ({ id, label, domain, example_url: `https://www.${domain}/search?find_desc=cafe` })),
  "yellow-pages": [
    { id: "us", label: "United States", domain: "yellowpages.com", example_url: "https://www.yellowpages.com/search?search_terms=cafe" },
    { id: "ca", label: "Canada", domain: "yellowpages.ca", example_url: "https://www.yellowpages.ca/search/si/1/cafe/Canada" },
    { id: "au", label: "Australia", domain: "yellowpages.com.au", example_url: "https://www.yellowpages.com.au/search/listings?clue=cafe" },
  ],
  tripadvisor: [
    ["us", "United States", "tripadvisor.com"], ["uk", "United Kingdom", "tripadvisor.co.uk"], ["eg", "Egypt", "tripadvisor.com.eg"], ["ca", "Canada", "tripadvisor.ca"],
    ["au", "Australia", "tripadvisor.com.au"], ["de", "Germany", "tripadvisor.de"], ["fr", "France", "tripadvisor.fr"], ["it", "Italy", "tripadvisor.it"], ["es", "Spain", "tripadvisor.es"],
    ["in", "India", "tripadvisor.in"], ["jp", "Japan", "tripadvisor.jp"],
  ].map(([id, label, domain]) => ({ id, label, domain, example_url: `https://www.${domain}/Search?q=cafe` })),
  wikipedia: [
    { id: "en", label: "English", domain: "en.wikipedia.org", example_url: "https://en.wikipedia.org/wiki/Web_scraping" },
    { id: "ar", label: "Arabic", domain: "ar.wikipedia.org", example_url: "https://ar.wikipedia.org/wiki/استخلاص_بيانات_الويب" },
    { id: "de", label: "German", domain: "de.wikipedia.org", example_url: "https://de.wikipedia.org/wiki/Web_Scraping" },
    { id: "fr", label: "French", domain: "fr.wikipedia.org", example_url: "https://fr.wikipedia.org/wiki/Web_scraping" },
    { id: "es", label: "Spanish", domain: "es.wikipedia.org", example_url: "https://es.wikipedia.org/wiki/Web_scraping" },
  ],
  publishers: [
    { id: "reuters", label: "Reuters", domain: "reuters.com", example_url: "https://www.reuters.com/world/" },
    { id: "bbc", label: "BBC", domain: "bbc.com", example_url: "https://www.bbc.com/news" },
    { id: "guardian", label: "The Guardian", domain: "theguardian.com", example_url: "https://www.theguardian.com/international" },
    { id: "nyt", label: "The New York Times", domain: "nytimes.com", example_url: "https://www.nytimes.com/section/world" },
    { id: "ap", label: "Associated Press", domain: "apnews.com", example_url: "https://apnews.com/world-news" },
  ],
};

function siteVariants(siteId: string, domain: string, exampleUrl: string): SiteCatalogVariant[] {
  return REGIONAL_SITE_VARIANTS[siteId] ?? [{ id: "default", label: "Default", domain, example_url: exampleUrl }];
}

/** Shipped locally so the chooser also works with an older or offline service. */
export const BUILT_IN_SITE_CATALOG: SiteCatalogEntry[] = BUILT_IN_SITE_SEEDS.map(([site_id, site_name, site_category, domain, example_url]) => {
  const variants = siteVariants(site_id, domain, example_url);
  return {
    site_id,
    site_name,
    site_category,
    domains: [...new Set(variants.map((variant) => variant.domain))],
    recommended_method: site_category === "content" ? "structured_or_article" : "visual_studio",
    suggested_fields: SITE_FIELDS[site_category] ?? ["title", "url"],
    requires_rendered: site_category !== "content",
    example_url,
    variants,
  };
});

const comparableSiteId = (siteId: string) => siteId.replace(/[^a-z0-9]/gi, "").replace(/^majorpublisher$/, "publishers").toLowerCase();

/** Add client-shipped regional choices to service entries, including older sidecars. */
export function enrichSiteCatalog(entries: SiteCatalogEntry[] | null | undefined): SiteCatalogEntry[] {
  if (!entries?.length) return BUILT_IN_SITE_CATALOG;
  return entries.map((entry) => {
    const builtIn = BUILT_IN_SITE_CATALOG.find((candidate) => comparableSiteId(candidate.site_id) === comparableSiteId(entry.site_id));
    if (!builtIn) return { ...entry, variants: entry.variants?.length ? entry.variants : siteVariants(entry.site_id, entry.domains[0], entry.example_url) };
    const variants = entry.variants?.length ? entry.variants : builtIn.variants;
    return { ...entry, domains: [...new Set([...entry.domains, ...builtIn.domains])], variants };
  });
}

export function formatSiteCategory(category: string): string {
  return category.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export type RequestVariable = {
  type?: "string" | "integer" | "number" | "enum" | "sparql" | "graphql" | "path";
  required?: boolean;
  default?: string | number;
  choices?: string[];
  minimum?: number;
  maximum?: number;
  pattern?: string;
  max_length?: number;
};

export type SourcePreset = {
  id: string;
  version: string;
  page_type: string;
  category?: string;
  strategy: { preferred: string; allowed: string[] };
  extraction: { mode?: string } & Record<string, unknown>;
  discovery?: { mode?: string } & Record<string, unknown>;
  request?: { url_template?: string; variables?: Record<string, RequestVariable>; limit_variable?: string } & Record<string, unknown>;
  requires_contact_user_agent?: boolean;
};

export const SOURCES: { id: SourceKind; label: string; hint: string }[] = [
  { id: "website", label: "Website", hint: "One page or paginated listings: selectors, structured data, or article text." },
  { id: "sitemap", label: "Sitemap", hint: "Pages listed in the site's XML sitemaps." },
  { id: "feed", label: "Feed", hint: "Entries from an RSS or Atom feed." },
  { id: "crawl", label: "Site crawl", hint: "Follow links within one site, to a set depth." },
  { id: "api", label: "Open data API", hint: "CKAN, Socrata, Wikidata, OpenAlex, OpenStreetMap, SEC EDGAR, GDELT, and JSON APIs." },
  { id: "xml", label: "XML or SOAP", hint: "Repeated records from XML documents or read-only SOAP responses." },
  { id: "repository", label: "Research repository", hint: "OAI-PMH metadata with Dublin Core or another repository-provided format." },
  { id: "documents", label: "Documents", hint: "PDF tables plus CSV, JSONL, XLSX, XML, Parquet, DOCX, ZIP, or GZIP files." },
  { id: "archive", label: "Web archive", hint: "Historical captures from the Wayback Machine or Common Crawl, without loading the live site." },
  { id: "bulk", label: "Bulk corpus", hint: "A downloaded Web Data Commons schema.org subset." },
];

export const PURPOSES: { id: string; label: string }[] = [
  { id: "internal_analysis", label: "Internal analysis" },
  { id: "lead_research", label: "Lead research" },
  { id: "dataset_building", label: "Building a dataset" },
  { id: "price_monitoring", label: "Price monitoring" },
  { id: "research", label: "Research" },
  { id: "archival", label: "Archiving" },
  { id: "search_indexing", label: "Search indexing" },
  { id: "ai_training", label: "AI training" },
];

export function sourceOf(preset: SourcePreset): SourceKind {
  if (preset.extraction?.mode === "xml") return "xml";
  if (preset.strategy.preferred === "api") return "api";
  if (preset.strategy.preferred === "webview") return "website";
  if (preset.extraction?.mode === "document_tables") return "documents";
  const mode = preset.discovery?.mode ?? "none";
  if (mode === "oai_pmh") return "repository";
  if (mode === "sitemap") return "sitemap";
  if (mode === "feed") return "feed";
  if (mode === "crawl") return "crawl";
  return "website";
}

export function presetsFor<T extends SourcePreset>(presets: T[], source: SourceKind): T[] {
  if (source === "archive") return presets.filter((p) => p.strategy.preferred === "http" && sourceOf(p) === "website");
  return presets.filter((p) => sourceOf(p) === source);
}

/** Initial values for a preset's request variables (defaults, with the limit variable left to the record cap). */
export function defaultVariables(preset: SourcePreset | undefined): Record<string, string> {
  const variables = preset?.request?.variables ?? {};
  return Object.fromEntries(Object.entries(variables).map(([name, spec]) => [name, spec.default === undefined ? "" : String(spec.default)]));
}

/** Client-side hints only; the service validates every variable again before any request. */
export function variableProblems(preset: SourcePreset | undefined, values: Record<string, string>): string[] {
  const problems: string[] = [];
  for (const [name, spec] of Object.entries(preset?.request?.variables ?? {})) {
    const value = (values[name] ?? "").trim();
    if (!value) {
      if (spec.required) problems.push(`${name} is required`);
      continue;
    }
    if (spec.type === "integer" || spec.type === "number") {
      const number = Number(value);
      if (!Number.isFinite(number) || (spec.type === "integer" && !Number.isInteger(number))) problems.push(`${name} must be a ${spec.type}`);
      else if ((spec.minimum !== undefined && number < spec.minimum) || (spec.maximum !== undefined && number > spec.maximum)) problems.push(`${name} must be between ${spec.minimum} and ${spec.maximum}`);
    } else if (spec.type === "enum" && spec.choices && !spec.choices.includes(value)) {
      problems.push(`${name} must be one of ${spec.choices.join(", ")}`);
    } else if (spec.pattern && !new RegExp(`^(?:${spec.pattern})$`).test(value)) {
      problems.push(`${name} has an invalid format`);
    }
  }
  return problems;
}

export function needsStartUrl(preset: SourcePreset | undefined): boolean {
  return !preset?.request?.url_template;
}

export function variablePayload(preset: SourcePreset | undefined, values: Record<string, string>): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [name, spec] of Object.entries(preset?.request?.variables ?? {})) {
    const value = (values[name] ?? "").trim();
    if (!value) continue;
    out[name] = spec.type === "integer" || spec.type === "number" ? Number(value) : value;
  }
  return out;
}
