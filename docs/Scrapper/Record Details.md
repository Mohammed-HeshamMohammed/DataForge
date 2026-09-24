# DataForge: Record Details

**Status:** Implemented
**Date:** 2026-09-24
**Builds on:** [Scraper spec](Scrapper.md), [Scraping Expansion Plan](Scraping%20Expansion%20Plan.md), [Implementation decisions](../decisions.md)

Every scraped record now carries more than the fields its preset declares. DataForge adds what the record's own values, its HTML element, its page, and (optionally) its detail page say about it. The data is gathered under the same scope, robots.txt, usage-signal, challenge, and politeness rules as the rest of a job.

Code: `workers/scraping/src/dataforge_scraping/details.py`, called from `extraction.py` (every mode), `runtime.py` (httpx engine), `engines/launcher.py` (Scrapy engine), and the service's scrape, Studio, archive, and bulk jobs.

## 1. Detail levels

A job's level comes from its `detail_level` parameter, then the preset's `details.level`, then the job default **`full`**: every record's own page is read, one extra request per record. The desktop Scraping tab and Scrape Studio also default to **Full**; the Scraping tab remembers the last choice. (The library functions, used offline and in tests, default to `standard`.)

| Level | Adds | Extra requests |
| --- | --- | --- |
| `none` | Nothing: the preset's fields and provenance only. | No |
| `basic` | **Value details** (`<field>.<detail>`) derived from each string field. | No |
| `standard` | Basic, plus **element details** (`item.*`) from each record's HTML element and **page metadata** (`page.*`) from the page it came from. | No |
| `full` | Standard, plus **detail-page data** (`detail.*`): each record's own link is followed and its page is read. | One per distinct detail link |

Details never overwrite a value the preset extracted. Every added key is namespaced, so exports stay traceable.

## 2. What each group contains

### Value details: `<field>.<detail>`

The first kind that fits a value wins, so a value is never read two ways.

| Value looks like | Keys added | Example |
| --- | --- | --- |
| Price (currency mark, or a price-like field name) | `.amount`, `.currency` (ISO 4217) | `price: "Now £1,299.50"` → `price.amount: 1299.5`, `price.currency: "GBP"` |
| Phone number (valid for the preset's `normalization.default_region`) | `.e164`, `.country`, `.type` | `(512) 555-0100` → `+15125550100`, `US`, `fixed_line_or_mobile` |
| Date (date-like text or a date-like field name) | `.iso` | `12 Sept 2026` → `2026-09-12` |
| Rating (`out of`, `/`, star words, rating-like field name) | `.value`, `.scale` | `Three` (in `star-rating Three`) → `3` of `5` |
| Stock status (availability-like field name) | `.in_stock`, `.quantity` | `Only 3 left in stock` → `true`, `3` |
| Formatted number or number with unit | `.number`, `.unit` | `3,450 reviews` → `3450`, `reviews`; `1.2k` → `1200` |
| URL | `.domain` (registrable), `.path`, `.file_type` | `https://shop.example.co.uk/p/x.pdf` → `example.co.uk`, `/p/x.pdf`, `pdf` |
| Relative link in a link-like field | `.absolute`, `.domain` | `/item/1` → `https://shop.test/item/1` |
| E-mail address | `.normalized`, `.domain` | `Jo@Example.COM` → `jo@example.com`, `example.com` |
| US address (address-like field name) | `.street`, `.city`, `.state`, `.postal_code` | usaddress components |
| Long text (40+ words) | `.word_count` | |

### Element details: `item.*` (selector presets)

These come from each record's root element. Parsing is the same for the bs4, parsel, and selectolax parsers and for both engines.

- `heading`, `title` (first `title` attribute), `text`
- `link`, `links`, `link_count`
- `image`, `images`, `image_alt` (lazy `data-src` and `srcset` included)
- `price`, `price_original` (struck-through or `was`/`old`/`list` price), `prices`
- `rating` (from `aria-label`, class words such as `star-rating Four`, or text)
- `availability`, `datetime` (`<time datetime>`)
- `email`/`emails`, `phone`/`phones` (from `mailto:`/`tel:` links and text, in E.164)
- `data.<name>` (data attributes, with framework and tracking attributes skipped)
- `prop.<itemprop>` (microdata inside the card)

Value details then apply to these keys too, for example `item.price.amount` and `item.rating.value`.

#### Grid fields: every value the cards share

On top of those fixed keys, DataForge compares all the cards of a page and adds every value they have in common as its own `item.<name>` column, with no selector needed. On a retail search grid (the `tests/retail_grid.py` fixture, shaped like a large marketplace's results) that is 16 `item.*` fields per card, for example `item.price`, `item.prices`, `item.rating`, `item.ratings`, `item.sales` ("2K+ bought in past month"), `item.delivery`, `item.badge`, `item.coupon`, `item.availability`, and `item.data.asin`.

How the fields are found (`details.grid_details`):

- Each text node and descriptive attribute (`title`, `aria-label`, `datetime`, `content`, `alt`, `value`, `href`, `src`) is keyed by its structural path: tag plus a stable class name, or tag plus position. Hashed and utility class names are ignored.
- A path becomes a field when it carries a value on at least 15% of the cards (and on at least two).
- Boilerplate is dropped: a value identical on 90% or more of the cards ("Add to cart"), `aria-hidden` subtrees (duplicate screen-reader copies), and values already present as element details or preset fields.
- Names come from the value (`$19.99` → price, `4.5 out of 5 stars` → rating, `FREE delivery` → delivery), then the class vocabulary, then the value kind. At most 40 grid fields per page.

The same pass runs for the three HTML parsers, both engines, and Scrape Studio (from the sanitized card copies), so a grid gives the same columns whichever way it was read. Studio previews the list as soon as the repeated item is picked (`scrape.detect_fields`).

### Page metadata: `page.*` (every HTML mode)

- `title`, `description`, `keywords`, `canonical`, `language`, `h1`, `author`
- `published`, `modified`, `site_name`, `type`, `image`, `robots`
- `og.*` (Open Graph and `product:`/`article:` properties)
- `twitter.*`, including label/data pairs such as `twitter.reading_time: "4 min"`
- `breadcrumbs` (JSON-LD `BreadcrumbList`, or a breadcrumb nav), `structured_types`, `feed`

Security tokens in meta tags (CSRF, nonce, session) are never read.

### Detail page: `detail.*` (level `full`)

- **The page's primary schema.org entity**, mapped through `data/schemaorg_mappings.json`, for example `detail.sku`, `detail.brand`, `detail.gtin`, `detail.price`, `detail.availability`, `detail.rating`, `detail.review_count`. The record's own `schema_type` is preferred; otherwise the mapping's `priority` list picks the entity.
- **Specifications** from `<dl>` lists, two-column tables, and "Label: value" list items, as `detail.spec.<label>` (with value details, for example `detail.spec.weight.number: 1.5`).
- **Main text** via trafilatura: `detail.text` (up to 20,000 characters), `detail.word_count`, `detail.author`, `detail.date_published`, `detail.categories`, `detail.tags`.
- **Contacts:** `detail.emails`, `detail.phones` (E.164).
- **Social profiles:** `detail.social.<network>` (Facebook, X/Twitter, Instagram, LinkedIn, YouTube, TikTok, GitHub, and others).
- **Images:** `detail.image`, `detail.images` (main content, excluding icons and sprites).
- **The page's own metadata:** `detail.page.*`.
- **Provenance:** `detail.url`, `detail.final_url` (after redirects), `detail.status`, `detail.retrieved_at`.

## 3. Which link is followed

The link comes from `details.follow.field` when the preset sets it. Otherwise DataForge tries, in order:

1. the preset's `url`-typed fields (image-like keys excluded);
2. common link keys (`link`, `url`, `href`, `detail_url`, `permalink`, …);
3. `item.link`, the first link in the record's element.

Links to files (images, PDFs, archives, media) and links back to the record's own page are skipped. Several records that share a detail link fetch it once.

**Not followed:**

- records collected through `detail_links` pagination (they are detail pages already);
- archive jobs (they never touch live sites, so `full` reads as `standard`);
- over HTTP, for Studio presets whose base allows only WebView rendering. Scrape Studio opens their item pages in the WebView instead (see *Item-page fields and Scrape Studio* below).

### Item-page fields and Scrape Studio (the grid → product page case)

Product grids (search results, category pages) usually hold a title, price, and link. The rest (description, specifications, seller, variants) is on each product's own page. At the `full` level every record's own page is opened. A preset can also name exact values to read there:

```json
"details": {
  "level": "full",
  "follow": {
    "field": "link",
    "fields": [
      {"key": "description", "selectors": [{"css": "#productDescription"}], "transforms": ["trim", "collapse_whitespace"]},
      {"key": "seller", "type": "url", "selectors": [{"css": "#seller a", "attribute": "href"}], "transforms": ["to_absolute_url"]}
    ]
  }
}
```

- **Scraping tab → Customize preset → Item pages:**
  - choose the link field;
  - add item-page fields (a CSS selector, and text or an attribute);
  - **Test item page** on a sample URL (`scrape.test_detail`) shows every value DataForge would read, and names the fields that matched nothing.
- **Scrape Studio → 4. Record detail → Full:**
  1. pick the grid and its fields, including the item link (Extract: href);
  2. optionally **Open a sample item page** and **Pick item-page value** for each extra value, then **Back to the list**;
  3. choose how item pages are read during a test or full run:
     - **In the background** (the default when the base preset allows HTTP): the list stays on screen. The service reads each item's page with the policy-checked HTTP client while every card on the grid is outlined: dashed while waiting, pulsing while its page is read, green when its data is in, amber when skipped (scope or robots.txt), red when the page failed. A status line in the corner counts pages and shows the last page's field count and timings. The marks come from the job's `detail_page_extracted` events and are drawn by the bridge's `markItems` action; they add only DataForge's own attributes and are never part of the copies sent for record details.
     - **In the WebView**: Studio opens each item's page visibly, for sites whose item pages need JavaScript.
  4. **Delay between item pages** (background mode) defaults to the base preset's `min_delay_ms` and can go down to 250 ms. The service never goes below 250 ms, and a robots.txt `Crawl-delay` still applies on top.

A Studio draft sent for a test run may narrow its base preset but never broaden it: the service checks unsaved drafts against the base's hosts, strategies, and policy, as it already did on save. HTTP is added to a draft's strategies only for background item pages and only when the base allows HTTP.

Item-page fields are stored as `detail.<key>`, come first, and win over automatic values with the same key. Value details apply to them as well.

**Sites that forbid automated collection stay off-limits.** DataForge does not evade robots.txt, terms, or bot checks. Amazon's robot check, HUMAN (PerimeterX), and Imperva challenges are detected and stop collection, as reCAPTCHA and Cloudflare challenges already did. For Amazon catalog data, use Amazon's own Product Advertising API.

### Speed

Reading an item page is fast; the network and the politeness delay set the pace. Measured on the test fixtures (one core, average of five runs; timings vary by machine):

| Step | Time |
| --- | --- |
| Grid details, per card (48-card retail grid, 16 `item.*` fields per card) | about 1 ms |
| Item page, small (1 KB) | about 3 ms |
| Item page, large retail page (263 KB) | about 44 ms (was 402 ms) |

What made it faster: structured data is read in order (JSON-LD, then microdata/microformats, then RDFa) and stops at the first mapped entity; the page is parsed once for metadata, facts, specifications, contacts, and images; trafilatura runs without its own metadata pass unless `details.follow.text` is `precise`; phone scanning is pre-filtered.

A page's download usually takes 100 ms to a few seconds, and item pages are requested one delay apart (the preset's `min_delay_ms`, at least 250 ms in Studio, and never faster than the site's `Crawl-delay`), so a 48-card grid at a 1-second delay takes about a minute. Each `detail_page_extracted` event reports `fetch_ms` and `parse_ms`, and the result's `detail_pages` statistics add their totals.

## 4. Policy

Detail pages go through the same `get` as listing pages, so all of these apply:

- scope (`validate_url`, and every redirect hop);
- robots.txt, TDMRep, and AIPREF for the declared purpose;
- the per-host politeness scheduler and Crawl-delay;
- the HTTP cache and opt-in WARC capture.

What happens when a check fails:

| Outcome | Behaviour |
| --- | --- |
| Link outside the preset scope | Skipped and counted; one summary warning. |
| robots.txt disallows the page | Skipped and counted; one summary warning. |
| HTTP error (for example 404) | The record gets `detail.status`; following continues. |
| Access denial, rate limit, challenge, or usage-signal reservation | Following stops. Records already collected are kept, with a warning. No evasion, no retry around the block. |
| Five network errors in a row | Following stops. |
| Cap reached | Following stops. The cap is the record cap and `details.follow.max_pages`, within `details.follow.max_duration_seconds` (default 600 s). |
| Cancel | Following stops and the job is cancelled. |

On the Scrapy engine, the parent process follows detail pages with the httpx policy client after the crawl. Records match the httpx engine; a parity test checks this.

## 5. Preset configuration

```json
"details": {
  "level": "full",
  "follow": { "field": "link", "max_pages": 200, "max_duration_seconds": 900, "text": "fast" }
}
```

`text` is `fast` (the default: the page's main text, with author and dates from the page's own metadata) or `precise` (trafilatura's metadata pass as well, several times slower on large pages).

`validate_preset` checks the level, the follow settings, and that `full` is not set on a preset whose strategies allow no HTTP.

## 6. Results, datasets, and the desktop UI

- **Job results** add `details` (contract `record-details.schema.json`):
  - `level`, `records_enriched`, and `fields_added`;
  - per-group counts;
  - the coverage of the most common detail keys;
  - `detail_pages` statistics (`candidates`, `fetched`, `reused`, `failed`, `skipped_scope`, `skipped_robots`, `stop_reason`, and the total `fetch_ms` and `parse_ms`).

  `field_coverage` stays about the preset's own fields.
- **Job events:** each record's item page emits one `detail_page_extracted` stage event: `detail_page` (a running count), `url`, `status` (`done`, `reused`, `failed`, `skipped`, or `stopped`), and, when read, `fields`, `fetch_ms`, and `parse_ms`, or a `reason`. Scrape Studio uses them to mark each card.
- **Staged datasets** keep every detail column.
- **Watch diffs** ignore `page.*`, `detail.page.*`, and `detail.retrieved_at`: that metadata is shared by every record on a page and often carries timestamps. Record-level detail changes, such as `detail.spec.color`, are reported.
- **Scraping tab:**
  - A **Record detail** picker explains each level.
  - Results show a **Details** summary.
  - The results table shows **Detected values** by default: the preset's fields, then up to 24 columns of card, item-page, and value details, most common first. Whole texts, lists, page-level values, and values identical on every record stay out of the table. **Preset fields** switches back to the preset's own columns.
  - Each row has an **Inspect** button that opens every value grouped as Fields, Value details, From the record's element, From the detail page, From the page, and Provenance.
- **Datasets tab:** a **Columns** choice (Fields and detected values, the default; Fields only; Every column) and the same inspector. Unmapped detail columns that look like contact data (phones, e-mails, addresses) stay masked until **Reveal sensitive values** is on.
- **Scrape Studio:**
  - The WebView bridge sends a sanitized copy of each record's element (at most 40,000 characters, room for large retail cards) and of the page head. The copy drops scripts (except JSON-LD), styles, frames, form controls, editable regions, secret-looking attribute values, `on*` handlers, and security meta tags.
  - The service applies the same element and page extraction, so Studio records match HTTP ones.

## 7. Structured data mappings

`data/schemaorg_mappings.json` (generated by `scripts/schemaorg-mappings.py`) maps 23 schema.org types and 273 subtypes to 654 canonical fields:

- **Commerce:** Product, Vehicle, Offer
- **Places and businesses:** LocalBusiness, LodgingBusiness, Organization, Place, Accommodation
- **People and listings:** Person, JobPosting, RealEstateListing
- **Content:** Event, Article, Book, Recipe, Movie, Course, SoftwareApplication, Review, VideoObject, Dataset, HowTo, Question

A field spec can take one of three forms:

- a dotted path;
- a list of fallback paths;
- `{"paths": [...], "join": true}`, which joins every value, such as all authors, opening hours, or ingredients.

Values are also cleaned:

- schema.org enumeration URLs become names (`https://schema.org/InStock` → `InStock`);
- HTML in descriptions is stripped;
- booleans become `true`/`false`.

Markup injected after `</html>` is still read.

The file was missing from the repository: `.gitignore`'s `Data/` rule also matches `data/` on case-insensitive file systems. It is now tracked and explicitly un-ignored.

## 8. Tests

- **`workers/scraping/tests/test_details.py`** (44 tests, offline) covers:
  - every value kind;
  - element, page, and detail-page extraction;
  - the level steps;
  - preset validation;
  - following under policy (scope, robots, 404, shared links, caps, cancel, challenge stop);
  - detail-link pagination;
  - Scrapy parity;
  - the new mappings;
  - watch diffs and summaries;
  - item-page fields on both engines, Studio item pages, and retailer bot-check detection;
  - one outcome event per item page (done, reused, skipped, failed) with timings;
  - grid fields on a retail grid (`tests/retail_grid.py`) through all three parsers, and a per-card speed bound.
- **`services/application/tests/test_details.py`** covers:
  - the job option and its validation;
  - the result contract;
  - staged datasets;
  - the `none` level's lean records;
  - Studio staging from sanitized copies;
  - Studio item pages read in the background (events per card, the 250 ms floor) and drafts that try to broaden their base;
  - the Studio field preview (`scrape.detect_fields`);
  - bulk-corpus value details by country.
- **Desktop:**
  - `src/lib/details.test.ts`: grouping, columns (including detected-value columns), and summaries;
  - `src/test/scraping.spec.tsx`: the picker, the job payload, the column toggle, the inspector, and accessibility;
  - `src/test/studio.spec.tsx`: card marks and the status line from item-page events;
  - `src/test/studio-bridge.spec.tsx`: the `markItems` bridge action and that marks never reach record copies.
