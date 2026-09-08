# Writing a spider

A spider yields dicts. Everything else — rate limiting, retries, the record cap,
error capture — is handled for you.

```python
from typing import Any, Iterator
import httpx

from dataforge.scraping.http import HttpSpider
from dataforge.scraping.registry import register_spider


@register_spider
class CountyRecordsSpider(HttpSpider):
    name = "county-records"
    description = "Scrape parcel records from a county assessor site."

    def parse(self, response: httpx.Response) -> Iterator[dict[str, Any]]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(response.text, "lxml")
        for row in soup.select("table.results tr[data-parcel]"):
            cells = [c.get_text(strip=True) for c in row.select("td")]
            yield {
                "APN": row["data-parcel"],
                "Address": cells[0],
                "City": cells[1],
                "Owner Name": cells[2],
                "source_url": str(response.url),
            }
```

Save it under `dataforge/scraping/spiders/` and import it from that package's
`__init__.py` so the registration runs. It is then available everywhere:

```bash
dataforge scrape list
dataforge scrape run county-records "https://example.gov/search?q=85701" -o out.csv
```

```http
POST /api/scrape
{"spider": "county-records", "targets": ["https://example.gov/search?q=85701"], "max_records": 500}
```

## What you get for free

- **robots.txt** checked once per origin before any fetch. Set
  `DATAFORGE_SCRAPE_RESPECT_ROBOTS=false` only where you have permission to.
- **Rate limiting** — `DATAFORGE_SCRAPE_DELAY_SECONDS` between requests.
- **Retries** with exponential backoff on transient HTTP errors.
- **An honest user agent**, configurable via `DATAFORGE_SCRAPE_USER_AGENT`.
- **Error capture** — an exception mid-crawl ends the run with the records
  gathered so far and the error in `result.errors`, rather than losing the batch.
- **`max_records`** enforced by the driver, so `crawl()` can be an infinite
  generator.

## Naming your fields

Emit column names that the role detector understands — `Address`, `City`,
`State`, `Zip`, `Phone`, `Email`, `Owner First Name` — and scraped records feed
straight into deduplication with no mapping step. Check with:

```python
from dataforge.core.normalize import detect_roles

detect_roles(list(frame.columns))
```

## Scraping responsibly

Scrape only what you are permitted to. Honour robots.txt and a site's terms,
keep the delay high enough not to degrade the service, and identify yourself in
the user agent. Personal data you collect is subject to the rules in
[data-handling.md](data-handling.md).
