# Bookmarklet Collectors

This file has two parts: the browser bookmarklets we built to collect data, and the plan for the DataForge Collector extension that builds on them.

Each bookmarklet is a self-contained HTML page in `scripts/`. The page shows a draggable button whose `javascript:` URL is the collector. Save it to the bookmarks bar (`Ctrl+Shift+B` shows the bar), open a results page, and click it. A panel appears in the page, collects the listings, and exports a CSV.

If the bookmark does not run, open the page, press `F12`, paste the collector code into the Console and press Enter. Each install page shows the code to copy.

## The three collectors

| Collector | File | Sites |
| --- | --- | --- |
| Real Estate Collector | `scripts/real_estate_collector.html` | Many US real-estate sites (below) |
| Zillow Collector | `scripts/zillow_search_collector.html` | Zillow only |
| Amazon Collector | `scripts/amazon_search_collector.html` | Amazon only, across its regional domains |

## Shared behavior

- Runs in your own browser tab, on the page you opened.
- Pace between background fetches: 8 to 15 seconds per page or item, depending on the collector.
- Stops at once on a CAPTCHA, a "verify you are human" page or an error. It never tries to get past one.
- Respects `robots.txt` for background fetches. Anything the site blocks there is read only from a page you open yourself.
- Agent names, phones and emails are not collected.
- Export is a CSV (opens in Excel). `scripts/collector_csv_to_excel.py` turns a CSV into a formatted workbook. `scripts/amazon_creators_export.py` does the same for Amazon.

## Real Estate Collector

One collector for US real-estate sites. It finds the listings on any results page, collects further result pages and each home's details, and exports a CSV.

How to use:

1. Open a search results page on any site and click the bookmark. It adds the homes on that page.
2. From then on it follows you: each page you open, filter you change or map move adds what the site loaded. If a page does a full reload (like HAR), click the bookmark again. Homes accumulate per site.
3. **Collect pages** fetches the next pages itself (10 s apart) where `robots.txt` allows it and the page has the data.
4. **Collect details** opens each home in the background (12 s apart) for description, photos, year built, HOA and MLS number.
5. **Download CSV**, then optionally convert to a formatted workbook:

```bash
python scripts/collector_csv_to_excel.py "%USERPROFILE%\Downloads\real_estate_trulia_com_400_listings.csv"
```

### Sites tested (Houston search, October 2026)

The number is the homes found on one results page.

**For sale**

| Site | Homes per page | Collect pages | Notes |
| --- | --- | --- | --- |
| HAR.com | 120 | Works (3 pages = 326) | Year built, lot, status, type and brokerage come from the cards |
| Trulia | 40 | Works | Details tested: year built, HOA, MLS, facts |
| Coldwell Banker | 24 | Works | |
| Weichert | 21 | Works | MLS number and year built |
| Estately | 15 | Works | Brokerage and lot size |
| Opendoor | 30 plus map points | Works | Map points have no link |
| StreetEasy (New York) | 38 | Works | |
| Windermere (Seattle) | 10 | No | Office pages |
| RE/MAX | 24 | First page only | The site loads later pages inside the browser; open them yourself with the collector running |
| Douglas Elliman, BHGRE, ERA, @properties, United Country | 0 to 2 | No | They build results in the browser; browse and the collector adds what loads |

**For rent**

| Site | Homes per page | Collect pages | Notes |
| --- | --- | --- | --- |
| HotPads | 40 | Works | Rent from/to |
| ApartmentGuide | 50 | Works | Few photos (the site loads them late) |
| ApartmentFinder | 74 | Works | |
| Trulia rentals | 39 | Works | |
| Craigslist | 349 | No, by design | Its terms forbid automated collection, so only pages you open are read, with no details |

**Land, commercial, auctions, new homes**

| Site | Homes per page | Collect pages | Notes |
| --- | --- | --- | --- |
| LandWatch | 25 | Works | |
| RealtyTrac | 18 | Works | No bedroom count |
| Showcase (commercial rent) | 22 | Works | Price is per sq ft per year |
| NewHomeSource | 83 | Sometimes | Occasionally returns a 403 protection page, and collection stops |
| VRM Properties (VA homes) | 8 in Houston | Works | VA-owned homes for sale |
| Craigslist real estate by owner | 277 | No, by design | Same rule as above |
| USDA REO | 16 | n/a | 16 homes nationwide (7 states, none in Texas) |
| Fclosure | n/a | n/a | Paid service; subscribe and export from your account |

### Count

- **Fully working (14 sites):** HAR, Trulia (sale and rentals), Coldwell Banker, Weichert, Estately, Opendoor, StreetEasy, HotPads, ApartmentGuide, ApartmentFinder, LandWatch, RealtyTrac, Showcase, VRM.
- **Partial (3 sites):** Windermere, RE/MAX, NewHomeSource.
- **Browse-and-collect only (5 sites):** Douglas Elliman, BHGRE, ERA, @properties, United Country.
- **Page-only by design:** Craigslist.
- **Minimal:** USDA REO.
- **Roughly 25 sites tested in total.**

### Not tested

These refused the automated test (Cloudflare or a verification page). They may still work on a page open in your normal Chrome, because the collector reads the page in front of you and adds what the site loads while you browse:

Redfin, Realtor.com, Homes.com, Apartments.com, Movoto, Compass, Zumper, PadMapper, Rent.com, Apartment List, RentCafe, Rentals.com, ForRent, LoopNet, Crexi, CommercialCafe, LandSearch, Land.com, LandFlip, Point2Homes, Keller Williams, eXp, Berkshire Hathaway, Realty.com, Howard Hanna, Long & Foster, Sotheby's, Hubzu, Foreclosure.com, HomeFinder, Avail, Furnished Finder, ForSaleByOwner.

### Not available

- **Xome** blocks visits from outside the US.
- **Century 21 and HUD Home Store** disallow search pages in `robots.txt`. Only the page you open is read.
- **Zillow** has its own collector (below).

### Public records (not a bookmarklet)

Harris County tax sales, County Clerk foreclosure notices and HCAD appraisal data run through `scripts/public_records.py` (5 s between requests, honors `robots.txt`), not the bookmark. It writes a CSV and a formatted Excel file to Downloads.

## Zillow Collector

Open a Zillow search and click the bookmark once. It then follows the search.

- **Filtered search or after moving the map** (the link has `searchQueryState`): it sends no requests. Each results page you open, map move or filter change adds what Zillow showed: 41 homes in the list and up to about 500 from the map in each view. Pan or zoom across the area to collect more.
- **Plain area link** (like `zillow.com/houston-tx/` or `zillow.com/77019/`): **Collect pages** also fetches the 20 pages itself, 10 s apart. Background fetching does not work with filters because Zillow's `robots.txt` blocks those links.
- **Collect details** opens each home in the background (15 s apart) for year built, lot, price per sq ft, HOA, tax rate, views, MLS number, listing type, description, available facts and all photos.
- **Full details** (complete Facts and features, Price history, Public tax history, Nearby schools) load from `/graphql/`, which `robots.txt` blocks, so they are not fetched in the background. Open the home yourself and click the bookmark; it scrolls the page and saves everything that appears.
- All homes from every area and filter go into one file without duplicates. The CSV has a column per shared fact (for example `Fact: Heating`).
- Stops at once on "Press & Hold" or any error. Agent names, phones and emails are not collected.
- Export: `python scripts/collector_csv_to_excel.py "%USERPROFILE%\Downloads\zillow_houston-tx_820_homes.csv"`

## Amazon Collector

Open an Amazon search results page and click the bookmark. A box appears at the bottom right.

1. **Collect pages:** set a Target count and it fetches result pages itself, 8 s apart.
2. **Collect details:** opens each product in the background (10 s apart) and reads Product details, About this item, Features and specs, Product description, extra details, seller and images. 1,000 products take about 3 hours; keep the tab open. If you close it or press **Stop**, click the bookmark and **Collect details** again to resume where it stopped. If you open a product page yourself and click the bookmark, it saves that product.
3. **Download CSV**, or export a formatted Excel with a column per specification (Material, Color and so on):

```bash
python scripts/amazon_creators_export.py --from-collector "%USERPROFILE%\Downloads\amazon_eg_1008_products_details.csv"
```

- Works on 23 regional domains (amazon.com, .co.uk, .eg, .ae, .sa, .ca, .de and others).
- If Amazon returns anything other than a normal page, collection stops at once. Wait, open the site normally, then resume.

## Plan: the DataForge Collector extension

Status: plan only. Nothing here is built. Items marked **Proposed** are my recommendation and still need a yes.

### 1. Goal

Collect real-estate listings faster and more completely than the bookmarklets, with the job set up in DataForge and the collecting done in the user's own Chrome.

- You choose the area in Scrape Studio's built-in view.
- DataForge cuts the job into parts and sends it to a browser extension.
- The extension collects each part in Chrome tabs and sends the listings back.
- DataForge saves them as a dataset and shows the run live.

Out of scope for now: Amazon (its bookmarklet stays as it is), several areas in one run, a store release, Firefox.

### 2. Decisions made

| Topic | Decision |
| --- | --- |
| Focus | Real-estate sites. Zillow first. |
| Extension | Universal. It knows no site and runs the job the app gives it. |
| Site knowledge | Lives in site presets in the app. Adding a site does not change the extension. |
| Choosing the job | The built-in view in Studio, limited to sites that have a preset. |
| Collecting | In Chrome, by the extension. In-app collecting is removed. |
| Split | Any number of parts. Duplicates are accepted during the run. |
| Pace | Each tab has its own delay: 1 s, 1.5 s, 2 s, then +0.5 s per tab, with a small random variation. |
| Install | Unpacked, from a button in DataForge. Store release later. |
| Live view | Full view in Studio, small panel in Chrome. Both show fetched listings and covered area. |
| Ease of use | Estimate, sample run, three speed choices, plain stop reasons, notification at the end, extension status. |

### 3. The blocker to settle first: Zillow's robots.txt

Zillow's `robots.txt` (checked 7 October 2026) contains `Disallow: /*?searchQueryState=*` and `Disallow: /graphql/` for all agents.

What this means for the plan:

- The address Zillow writes when you move the map contains `searchQueryState`. Cutting the map into tiles produces more addresses of that kind. Having the extension open them is automated access to addresses the site disallows.
- Today's Zillow bookmarklet follows this rule: on a filtered or moved search it sends nothing and only records what Zillow shows while you browse. Every preset in the app also says `robots_policy: respect`, and the service already checks each address (`scrape.check_url`).
- So map-bounds tiles, as discussed, do not fit Zillow under the current rules.

**Proposed** way through, which keeps the rule:

1. The extension checks every address against the site's `robots.txt` before opening it, and skips what is disallowed. This is a fixed behavior of the engine, not a preset option.
2. Each site preset names the **way to cut an area** that the site allows:
   - **By place address:** addresses such as `zillow.com/77019/` or `zillow.com/houston-tx/` are allowed, and the bookmarklet already collects their 20 result pages. The area is cut into ZIP codes, one part per ZIP.
   - **By map bounds:** used only on sites whose `robots.txt` allows those addresses.
3. For Zillow the ZIP list comes from the area you confirmed: the listings Zillow shows while you pick the area carry their ZIP codes, and those become the parts.
4. Moving the map yourself still works as it does today: the extension records what Zillow shows, with no requests of its own.

Limits of this route on Zillow:

- A ZIP gives up to 20 pages of about 41 listings (about 820). A ZIP with more is reported as incomplete, not hidden.
- Zillow's filters live in `searchQueryState`, so automated parts cannot carry them. Filtering happens on the dataset in DataForge after collection.
- Full details (price history, tax history, schools) load from `/graphql/` and stay manual: open the home yourself and the extension saves what appears.

The other choice is to change the rule and let the extension open disallowed addresses. I do not recommend it, and it would need the preset spec's policy section rewritten, not an exception.

#### Observation: moving the map changes only the bounds

Two addresses taken before and after dragging the Houston map (7 October 2026) differ only in `mapBounds`. The region (`regionId` 39051), the zoom (12), the filters and the sort are identical.

| | West | East | South | North |
| --- | --- | --- | --- | --- |
| First view | -95.5757 | -95.3371 | 29.7698 | 29.9303 |
| Second view | -95.7179 | -95.4792 | 29.7048 | 29.8654 |

- The view keeps its size: about 0.2386 degrees wide and 0.1605 degrees tall at zoom 12.
- So any tile's address can be written by arithmetic: copy the address and replace the four bounds. Nothing has to be read from the page.
- This makes map tiles easy to build. It does not change the `robots.txt` point above, because every one of these addresses contains `searchQueryState`.

A middle route, **Proposed** for discussion: **guided tiles.** Studio works out the grid and shows it. A "Next tile" button moves the map to the next tile, one press per tile, and the extension records what Zillow shows, as the bookmarklet does today when you drag the map. No queue runs unattended. Each page is opened by your own press, which is close to dragging the map by hand, though it is still a judgement call under the rule.

### 4. How a run works

1. **Check.** Studio shows the extension status. If it is not connected, the button that fixes it is shown.
2. **Choose.** You open a site with a preset in the built-in view and move to the area you want. Studio reads the address and the listings on show.
3. **Estimate.** Studio shows the listing count the site reports, the number of parts and a rough time.
4. **Sample.** The extension collects one part and Studio shows a few listings with their fields. You confirm.
5. **Run.** Studio sends the job. The extension opens tabs in its own Chrome window, each at its own pace, and takes parts from the queue.
6. **Watch.** Studio shows the map with covered parts and listing dots. Each Chrome tab shows a small panel.
7. **Save.** Listings reach the dataset as they arrive. At the end repeats are dropped by listing ID and a field-coverage figure is shown.
8. **Stop and resume.** A challenge page stops every tab and names the tab to look at. Resume continues from the saved queue.

### 5. Parts to build

#### 5.1 The extension (`extensions/dataforge-collector/`)

Chrome, Edge and Brave (Manifest V3). No site-specific code.

- **Background worker.** Holds the run: the queue of parts, each tab's state, the pacing clock and the stop flag. Keeps the queue in extension storage so a run survives a closed browser.
- **Page script.** Injected only into tabs the extension opened for a run. Carries out the abilities and draws the small panel.
- **Link client.** Connects to DataForge, receives jobs, sends listings and progress.
- **Toolbar icon.** Shows the running total and the connection state.
- **Fixed ID.** A key in the manifest keeps the extension ID the same on every machine, so DataForge can recognize it.

#### 5.2 The link between app and extension

**Proposed:** DataForge listens on a local port on `127.0.0.1` and the extension's background worker connects to it.

- Bound to the local machine only, on a port chosen at start.
- A random token is written into the extension folder by the Install button, so there is nothing to copy by hand. The app also checks that the caller is the DataForge extension ID.
- One connection carries jobs one way and listings and progress the other.

Earlier I leaned towards Chrome Native Messaging. I now prefer the local port: Native Messaging starts a separate helper program that would then need its own link to the running app, which is two hops for the same result. To confirm in phase 1: that the worker can read the token file from its folder and keep the connection open through a long run.

#### 5.3 Site presets

A new kind of preset for the extension, alongside today's presets and validated the same way.

Each one states:

- the site's hosts (this is also the allow-list for the built-in view);
- how to read the search from the address;
- how to cut an area (by place address or by map bounds) and the limit per part;
- where the listings are (embedded data, data the site loads, repeated cards, or automatic detection);
- how to reach the next page;
- the fields and how they map to the `property` entity;
- what a challenge page looks like on that site;
- the default pace and the largest number of tabs.

First preset: `zillow.listings`. Second, to prove the engine: HAR.

#### 5.4 Scrape Studio

- **Kept:** the built-in view, for choosing the area. It opens only hosts that have a site preset.
- **New:** reading the full address from the page (today's page events carry the address without its query part), the estimate, the sample step, the run panel, the live map, the extension status and the Install button.
- **Removed:** the Listing Collector panel and the `generic.listings` and `generic.products` collector presets, once the extension covers them. Until then they stay.

#### 5.5 The service

- Listings arrive in batches and are added to one dataset during the run. The existing `scrape.stage_rendered` command already turns collected pages into a dataset; it needs a way to add to a run in progress.
- Address checks reuse `scrape.check_url`.
- Re-runs later reuse `dataset.diff`.

### 6. What the app sends: the job

A job is a description. Chrome does not allow an extension to run program code that arrives from outside, so the app never sends code.

```json
{
  "job_id": "…",
  "preset": "zillow.listings@1.0.0",
  "mode": "sample",
  "parts": [
    { "id": "77009", "address": "https://www.zillow.com/77009/" },
    { "id": "77018", "address": "https://www.zillow.com/77018/" }
  ],
  "read": { "from": ["embedded_data", "loaded_data", "cards"], "fields": ["…"] },
  "next_page": { "kind": "numbered", "max_pages": 20 },
  "pace": { "tabs": 3, "delays_ms": [1000, 1500, 2000], "variation": 0.2 },
  "stop_on": ["challenge_page", "error_page", "robots_disallowed"]
}
```

### 7. Abilities of the engine

| Ability | Use |
| --- | --- |
| Open, wait, scroll | Load an address in a run tab and let it finish. |
| Read embedded data | Listing data the page carries inside itself. |
| Capture loaded data | Data the site loads while the page is used (Zillow's map pins). |
| Read cards | Repeated cards, by selectors from the preset. |
| Detect listings | No recipe: structured data first, then repeated cards with a price and a link. This is the Real Estate Collector's method. |
| Next page | Numbered pages, a next link, or scrolling. |
| Cut an area | By place address or by map bounds, from the preset's pattern. |
| Check an address | `robots.txt` before every open. |
| Detect a challenge | Stop every tab and report which one. |
| Report | Listings, counts, state and pace back to the app. |

Never collected: agent names, phone numbers and e-mail addresses, as today.

### 8. Splitting and pace

- **Parts** and **tabs at once** are separate. Many parts with few tabs is safe; the queue feeds whichever tab is free.
- **Self-dividing parts** apply where an area is cut by map bounds: a part that returns the site's limit is cut into four and collected again. For parts cut by place address, a part that hits its limit is reported as incomplete.
- **Pace:** tab 1 waits 1 s, tab 2 waits 1.5 s, tab 3 waits 2 s, each further tab +0.5 s, with a random variation on every wait. The background worker keeps time, because Chrome slows timers in tabs that are not in front.
- **Adjusting pace:** slower when the site answers slowly, and slower after a resume that follows a challenge page.
- **Three choices** (**Proposed** values):

| Choice | Tabs | Delays |
| --- | --- | --- |
| Careful | 1 | 8 s (today's bookmarklet pace) |
| Normal | 2 | 2 s and 3 s |
| Fast | 3 | 1 s, 1.5 s and 2 s (the values you chose) |

- **Proposed** hard cap: 4 tabs per site.
- Tabs open a few seconds apart, in the extension's own Chrome window.

### 9. Safety rules of the engine

These are fixed in the extension and cannot be switched off by a preset.

1. Runs only in tabs it opened for a run.
2. Opens only hosts named in the job's preset.
3. Checks `robots.txt` before every address.
4. Stops every tab on a challenge page, an error page or a sign-in page. It never tries to pass one. You complete a check yourself, then press Resume.
5. Never types into forms and never signs in.
6. Never collects agent names, phones or e-mails.
7. Accepts jobs only from the DataForge app on the same machine, with the token.

### 10. Live view

One source: the background worker holds the state and both views read it.

**Studio (full view)**

- The confirmed area with an outline, drawn on DataForge's own simple map of the area. Drawing over the live site view is not planned for the first version.
- Each part filled by state: done solid, collecting outlined in its tab's color, waiting empty, stopped marked as a problem. A divided part shows its smaller parts.
- Each listing as a dot when it arrives, colored by tab, with a second mark when its details are complete.
- Coverage figure, listings per tab, time left.

**Chrome (small view)**

- A panel in each run tab: its part, listing count, pace and Stop.
- An outline on result cards once collected. The site's own map pins are not marked.
- The total on the toolbar icon.

Stop works from either place and stops every tab.

### 11. Ease of use

1. **Estimate before starting:** listing count, parts, rough time.
2. **Sample first:** one part, a few listings shown, then the full run. This also meets the preset spec's rule of a test run before a full run.
3. **Three speed choices:** Careful, Normal, Fast. Raw numbers under Advanced.
4. **Plain stop reasons with the next step,** and a button that brings the tab to the front.
5. **Notification** when a run ends or stops, and the computer stays awake during a run.
6. **Extension status in Studio:** not installed, installed but not connected, connected, out of date, each with its fix.

### 12. Installing

The Install button in DataForge:

1. Puts the extension folder in DataForge's data folder and writes the token into it.
2. Copies the folder path.
3. Opens the browser's extensions page, or shows the address to paste if the browser refuses.
4. Shows three steps: turn on Developer mode, press Load unpacked, pick the folder.
5. Turns to "Connected" when the extension calls in.

Updates: a new DataForge version refreshes the folder and Studio shows "out of date" until the extension is reloaded.

### 13. Rules and documents that must change

- **Website Preset Specification.** It says never to launch a separate browser window or automation process. That line needs rewriting to allow the user's browser through the DataForge extension.
- **Request limits.** Today's presets set one request at a time and 8 s between requests. The extension presets need their own limits for several tabs and shorter delays.
- **Preset schema** (`packages/contracts/preset.schema.json`): the new preset kind.
- **IPC contract** (`docs/ipc-contract.md`): the link, the new host commands and the run events.
- **`docs/decisions.md`:** a record of this change of direction.

### 14. Build order

Each phase ends with something that can be checked.

| Phase | Work | Done when |
| --- | --- | --- |
| 0. Checks | Settle section 3. Confirm which Zillow addresses `robots.txt` allows and that ZIP pages paginate as the bookmarklet expects. Confirm HAR's rules. | A written answer for both sites. |
| 1. Link and install | Local port and token, Install button, extension status. | Studio shows "Connected" after a fresh install on a clean Chrome profile. |
| 2. Engine, one tab | Abilities, safety rules, job format, small panel. Zillow preset. | A sample of one Zillow ZIP reaches a DataForge dataset with the same fields as the bookmarklet. |
| 3. Choosing in Studio | Read the address, restrict hosts to presets, estimate, sample step. | An area picked in the built-in view becomes a job with no typing. |
| 4. Split and pace | Queue of parts, several tabs, per-tab delays, three speed choices, saved queue and resume, stop on challenge. | A multi-ZIP run finishes; a stopped run resumes without repeating finished parts. |
| 5. Live view | Studio map with parts and dots, coverage, Chrome card outlines. | The map matches the dataset at the end of a run. |
| 6. Finish | Notification, keep awake, plain stop reasons, clean-up at save. | A full run needs no watching and ends with a de-duplicated dataset. |
| 7. Second site | HAR preset only. | HAR works with no change to the extension. If it needs one, the ability list is extended and that is recorded. |
| 8. Removal | Remove the Listing Collector panel and the collector presets. Update the documents in section 13. | Tests pass with the old collector gone. |

### 15. Testing

- **Engine:** saved copies of real pages (fixtures) per preset, as presets have today, so reading listings is tested without touching a live site.
- **Link:** refused without the token, refused from another extension ID.
- **Safety:** a fixture challenge page stops every tab; a disallowed address is skipped; a host outside the preset is refused.
- **Resume:** stop a run halfway, restart Chrome, resume.
- **Live sites:** a short manual check per site before each release, at the Careful pace.

### 16. Risks

| Risk | Effect | Answer |
| --- | --- | --- |
| Short delays bring challenge pages | Runs stop often | Careful and Normal choices, adjusting pace, resume from the queue |
| A site changes its pages | Fields come back empty | The sample step shows it before a full run; the fix is in the preset |
| Chrome puts the worker to sleep | Pace drifts or the link drops | Queue is saved; the link reconnects; confirm in phase 1 |
| A site needs an ability the engine lacks | Extension must be updated | Expected for some sites; the HAR phase measures how often |
| Local port misuse by another program | Fake jobs or data | Local-only, token, extension ID check |
| The account or address gets limited by a site | Collection blocked for a while | Conservative defaults, hard cap on tabs, immediate stop |

### 17. Still to decide

1. **Section 3:** keep the `robots.txt` rule and cut Zillow by ZIP (**Proposed**), or change the rule.
2. **The link:** local port (**Proposed**) or Native Messaging.
3. **Hard cap on tabs:** 4 (**Proposed**).
4. **Default speed choice:** Normal (**Proposed**) or Fast.
5. **The element picker** in the built-in view: keep for building presets (**Proposed**) or remove.
6. **Row-driven workflows:** leave as they are (**Proposed**) or move to the extension later.

### 18. Later

- Store release with one-click install.
- Saved searches and re-runs that mark new, gone and changed listings.
- Details only for listings that pass a filter.
- Several areas in one run.
- More real-estate sites, in the order of the tested list above.

## Open items for the bookmarklets

- Rerun the sweep when a site changes. The test date is October 2026.
- Decide whether the untested sites deserve a trial in a normal Chrome profile.
