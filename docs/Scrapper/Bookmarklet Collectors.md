# Bookmarklet Collectors

Working notes on the browser bookmarklets we built to collect data. Each one is a self-contained HTML page in `scripts/`. The page shows a draggable button whose `javascript:` URL is the collector. Save it to the bookmarks bar (`Ctrl+Shift+B` shows the bar), open a results page, and click it. A panel appears in the page, collects the listings, and exports a CSV.

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

## Idea: split collection with a Chrome extension (not built)

Goal: start a search once, confirm the listings found, then split the job across two tabs so each collects half.

Why an extension: a bookmarklet in one tab cannot inject code into a tab on another site, and needs a click in every tab. An extension can run the collector in the tabs it opens, with no clicks, and site Content-Security-Policy does not block it.

Parts:

- **Hub page (the script).** The search form, live listing counts per tab, a **Split in 2** button and **Download CSV**. It lives inside the extension and opens from the toolbar icon. It never touches the site directly.
- **Extension.** Opens the tabs the hub asks for, runs the existing collector code in them, and passes the listings back to the hub.

Flow (Zillow first):

1. Type the search in the hub and press Open. The extension opens Zillow with that search and starts the collector in that tab.
2. Pan and zoom until the area is right. The hub shows the listings gathered.
3. Press **Split in 2**. The extension opens two tabs with the same search, with the confirmed map area cut in half (east/west by default, north/south as a toggle).
4. Each tab collects its half. The hub merges the results and exports one CSV.

Rules:

- The collector runs only in tabs the hub opened, not whenever Zillow is browsed.
- Duplicates are accepted. Listings on the seam between halves appear twice.
- At most 2 tabs per site.
- No background requests for filtered Zillow searches (`robots.txt` blocks them).
- A "Press & Hold" page or an error in any tab stops all tabs.
- Installed by hand through `chrome://extensions` (Developer mode, Load unpacked). Not published to the Chrome Web Store.
- Planned location: `extensions/dataforge-collector/`. The Real Estate and Amazon collectors can join later.

### Under discussion: driving it from Scrape Studio

Idea: Scrape Studio stays the place where you set up the job and where the dataset lands, while Chrome and the extension do the collecting.

- Studio defines the search, the site and the split, and sends the job to the extension.
- The extension collects in Chrome and sends the listings back.
- Studio saves them as a dataset, so Clean & Combine and the Excel export work as they do now.

#### Refinement: the built-in view picks the area, the extension collects

The built-in view stays, but only for choosing what to collect. It stops collecting.

1. Open the site in Studio's built-in view and move the map or set filters.
2. On Zillow the address changes as the map moves. It carries the full search (`searchQueryState`: map bounds, zoom, filters), so the address alone describes the job.
3. Studio reads that address and sends it to the extension.
4. The extension opens it in Chrome and collects. For a split, Studio halves the map bounds in the address and sends two addresses.

Notes:

- The built-in view opens only allowed sites. The allow-list needs a source: today's presets are generic and open-data, and Zillow is only in the site catalog, so either the catalog is the list or each site gets its own preset.
- This works for sites that keep the search in the address. Sites that keep it in page state only would need another way to hand over the job.
- In-app collecting (the Listing Collector panel and the collector presets) would be removed. The element picker and the row-driven workflows are still undecided.

#### Decided so far

- **Focus:** real-estate sites. Amazon stays as it is (its bookmarklet keeps working, with no extension work).
- **Sites:** Zillow first, then other real-estate sites. Each gets a site preset, and those presets are the allow-list for the built-in view.
- **Split:** any number of parts, not only 2.

#### Multi-split

Two separate settings:

- **Parts:** how many pieces the job is cut into.
- **Tabs at once:** how many pieces are collected at the same time. The rest wait in a queue.

How each site is cut:

- **Zillow:** the map bounds become a grid of tiles (2x2, 3x3 and so on). Each tile is its own address. Zillow shows about 500 pins per view, so more tiles means more listings found, even with one tab.
- **Amazon:** the result pages are dealt out in ranges, and the product list for details is cut into equal chunks.

Limits:

- Tabs at once starts at 2, with a hard cap to be agreed. More tabs means more requests to the same site at once and a higher chance of a challenge page.
- Tabs open a few seconds apart, not all together.
- A challenge page in any tab stops every tab and keeps the queue, so the run can resume.
- Duplicates are accepted. Listings on tile edges repeat.

#### Per-tab speed

Each tab collects at its own pace, so the tabs never fire together.

- Starting values: tab 1 waits 1 s, tab 2 waits 1.5 s, tab 3 waits 2 s, and each further tab adds 0.5 s. Every wait also gets a small random variation.
- These are settings, per site, so they can be raised without a code change.
- Risk to watch: this is much faster than the bookmarklets (8 to 15 s). Three tabs together send about 2 requests a second. If challenge pages appear, raise the delays first.
- A challenge page still stops every tab at once.

#### Installing the extension from DataForge

Browsers do not let a desktop app add an extension silently. What DataForge can do:

- **Decision:** the unpacked install is the one to build. The store install stays in the plan for later.
- **Now (unpacked):** an "Install extension" button puts the extension folder on disk, copies its path, and opens the browser's extensions page with three steps shown: turn on Developer mode, press Load unpacked, pick the folder. Chrome, Edge and Brave take the same folder.
- **Later (store):** publish it on the Chrome Web Store (it can be unlisted). The button then opens the store page and the install is one click, with automatic updates. Edge has its own store.
- **Firefox:** a separate port and Mozilla signing. Not planned.
- DataForge can also show whether the extension is connected, once the link between the two exists.

#### Candidate improvements (not decided)

Coverage:

1. **Self-dividing tiles.** If a tile comes back at Zillow's limit (about 500 pins), cut that tile into four and collect again, until every tile is under the limit. Dense areas get small tiles and empty areas stay large, with no guessing a grid size.
2. **One preset per site, held by the app.** Each real-estate site's preset says how to read its address, how to cut its area and how to read its listings. Adding HAR or Trulia does not touch the extension (see "The extension is universal").

Reliability:

3. **Saved queue and resume.** The list of parts lives in the extension's storage, so a closed browser or a challenge page resumes where it stopped.
4. **Save as they arrive.** Listings go to the DataForge dataset during the run, not at the end, so a crash loses nothing.
5. **Pacing that adjusts.** Slow down when the site answers slowly, and after a challenge page resume at a slower pace, not the same one.
6. **Pacing driven by the extension, not the page.** Chrome slows timers in tabs that are not in front, so waits inside the page can stretch. The extension's background worker should keep time.
7. **Its own Chrome window.** Collection tabs open in a separate window, reused across parts, so your normal tabs stay clear.

In Studio:

8. **Live view, in both places.** The extension's background worker holds the run's state, and both views read from it, so they always agree.
   - **Studio (full view):** the tile grid over the map, each tile's state (waiting, collecting, done, stopped), listings per tab and time left.
   - **Chrome (small view):** a small panel in each collecting tab with that tab's part, its listing count, its pace and a Stop button, plus the total on the extension's toolbar icon.
   - Stop works from either place and stops every tab.
   - **Highlighting what was fetched:**
     - In Studio, each collected listing appears as a dot at its coordinates as it arrives, colored by the tab that fetched it, with tiles shading in as they finish.
     - In Chrome, listing cards in the results list get an outline once collected. Zillow's own map pins are not marked, because Zillow draws them and its markup changes.
     - Listings with full details collected get a second mark, so it is clear which ones are complete.
   - **Highlighting the covered area (Studio map):**
     - The confirmed search area has an outline.
     - Each tile is filled by state: done is solid, collecting is outlined in its tab's color, waiting is empty, stopped is marked as a problem.
     - A tile that hit the pin limit and was cut into four shows the smaller tiles inside it.
     - A coverage figure shows how much of the area is done.
9. **Saved searches and re-runs.** Run the same area again later and mark what is new, gone or changed in price.
10. **Clean up at save.** Drop repeats by listing ID when the dataset is saved, and show how many fields were filled.
11. **Details only where wanted.** Filter the listings first, then collect full details for the ones that pass.

Ease of use:

12. **Estimate before starting.** After the area is confirmed, show the listing count the site reports, the number of parts and a rough time. A job that is too big can be shrunk before it runs.
13. **Sample first.** Collect one tile and show a few listings with their fields. The full run starts after that looks right. This also meets the preset spec's rule of a test run before a full run.
14. **Stop reasons in plain words, with the next step.** For example: "Zillow asked for a check in tab 2. Complete it there, then press Resume," with a button that brings that tab to the front.
15. **Speed as three choices.** Careful, Normal and Fast set the tabs and delays together. The raw numbers stay under Advanced.
16. **Extension status in Studio.** Shows not installed, installed but not connected, connected, or out of date, each with the button that fixes it.
17. **Tell me when it ends.** A Windows notification when a run finishes or stops, and the computer stays awake during a run.
18. **Several areas in one run.** A list of ZIP codes or cities is queued and collected one after another into one dataset. *Not needed for now.*

Items 12 to 17 are the ones to focus on.

#### The extension is universal (decided)

The extension is not built for Zillow. It knows no site. It runs the job the app gives it.

- **The app holds the site knowledge.** Each site preset in DataForge says where the listings are, how the area is cut, how pages advance and which fields to read.
- **The job is data, not code.** The app sends a description, and the extension follows it with a fixed set of abilities. Chrome does not let an extension run program code that arrives from outside, and a description also keeps a later store release possible.
- **Abilities the extension offers:**
  - open an address in a tab, wait, scroll;
  - read listing data embedded in the page;
  - capture the data a site loads while the page is used (how Zillow's map pins arrive);
  - read repeated cards by selectors;
  - find listings without a recipe (the method the Real Estate Collector uses today: structured data, then repeated cards with a price and a link);
  - go to the next page;
  - cut an area into tiles from an address pattern;
  - report progress, stop on a challenge page.
- **Adding a site** means writing a preset in the app. The extension is not reinstalled.
- **Order of work:** build the engine with the Zillow preset as the first proof, then add the next real-estate site to check that it needed no extension change.
- **Limit:** a site that needs something outside these abilities needs a new ability added to the extension once. After that every preset can use it.

Open points:

- **Link between the app and the extension.** The two candidates are Chrome Native Messaging (the extension talks to a small DataForge helper program) and a local port on `127.0.0.1` in the app that the extension's background worker calls.
- **Conflict with the preset spec.** The Website Preset Specification says never to launch a separate browser window or automation process. This idea needs that rule changed or an explicit exception.
- **Session difference.** Studio's built-in view is incognito, with no stored cookies or logins. Chrome is your real profile, so sites see your normal session.

## Open items

- Rerun the sweep when a site changes. The test date is October 2026.
- Decide whether the untested sites deserve a trial in a normal Chrome profile.
- Decide whether any of this should become an in-app preset instead of a bookmark.
