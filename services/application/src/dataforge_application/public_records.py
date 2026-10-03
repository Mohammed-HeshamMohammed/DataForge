"""Collect public property records for Harris County, TX (Houston) into formatted Excel workbooks.

Sources (all public, no account):

* tax-sales    - Harris County Tax Office list of properties in the next delinquent-tax sale (one page):
                 address, status, precinct, minimum bid, adjudged value, judgment, HCAD account.
* foreclosures - Harris County Clerk index of trustee foreclosure notices by sale month:
                 document number, sale date, filing date, pages and a link to the notice.
* hcad         - the Harris Central Appraisal District appraisal roll (bulk files you download from hcad.org), filtered
                 by ZIP, property class, absentee / out-of-state owner, homestead, years owned, year built and value.

Each command saves a CSV and an .xlsx next to it (Summary sheet with live formulas, a filterable table).
Requests identify this script honestly, follow robots.txt, wait between requests, and stop on any error page;
nothing is retried around a block. Tax sales and foreclosure notices carry no owner or borrower names. The HCAD
list keeps the owner name and mailing address exactly as HCAD publishes them; of the exemptions only "Homestead"
is shown (age, disability and veteran exemptions are left out).

In DataForge these run as jobs from the Public Records tab and create datasets. As a script:
    python -m dataforge_application.public_records tax-sales --hcad "%USERPROFILE%/Downloads/hcad_2026"
    python -m dataforge_application.public_records foreclosures --from 2026-10 --to 2027-01
    python -m dataforge_application.public_records hcad --data "%USERPROFILE%/Downloads/hcad_2026" --zip 77009 77018 --absentee
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from . import workbook as excel

USER_AGENT = "DataForge-public-records/1.0 (personal property research; python-httpx)"
DELAY_SECONDS = 5
TIMEOUT_SECONDS = 45
TRIES = 3
TAX_SALES_URL = "https://www.hctax.net/Property/listings/taxsalelisting"
FORECLOSURES_URL = "https://www.cclerk.hctx.net/applications/websearch/FRCL_R.aspx"
FORECLOSURE_PREFIX = "ctl00$ContentPlaceHolder1$"


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


class Stopped(Exception):
    """The site answered with something other than a normal page, or robots.txt disallows the page."""


class Fetcher:
    def __init__(self) -> None:
        self.session = httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS,
                                    headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        self.robots: dict[str, list[tuple[bool, int, re.Pattern]]] = {}
        self.last = 0.0

    def rules(self, origin: str) -> list[tuple[bool, int, re.Pattern]]:
        """robots.txt rules for user-agent * (RFC 9309: longest match wins, Allow wins ties, * and $ wildcards)."""
        if origin not in self.robots:
            rules = []
            try:
                response = self.session.get(origin + "/robots.txt")
                body = response.text if response.status_code == 200 else ""
            except httpx.HTTPError:
                body = ""
            agents, in_rules = [], False
            for raw in body.splitlines():
                match = re.match(r"^\s*(user-agent|allow|disallow)\s*:\s*(.*?)\s*$", raw.split("#")[0], re.IGNORECASE)
                if not match:
                    continue
                field, value = match.group(1).lower(), match.group(2)
                if field == "user-agent":
                    if in_rules:
                        agents, in_rules = [], False
                    agents.append(value.lower())
                    continue
                in_rules = True
                if "*" in agents and value:
                    pattern = re.escape(value).replace(r"\*", ".*")
                    pattern = pattern[:-2] + "$" if pattern.endswith(r"\$") else pattern
                    rules.append((field == "allow", len(value), re.compile("^" + pattern)))
            self.robots[origin] = rules
        return self.robots[origin]

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = parts.path + ("?" + parts.query if parts.query else "")
        best = None
        for allow, length, pattern in self.rules(f"{parts.scheme}://{parts.netloc}"):
            if pattern.match(path) and (best is None or length > best[1] or (length == best[1] and allow)):
                best = (allow, length)
        return best is None or best[0]

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        if not self.allowed(url):
            raise Stopped(f"robots.txt of {urlsplit(url).netloc} does not allow {urlsplit(url).path}")
        for attempt in range(1, TRIES + 1):
            wait = self.last + DELAY_SECONDS - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.session.request(method, url, **kwargs)
            except httpx.HTTPError as error:  # network trouble only; never around a block
                self.last = time.monotonic()
                if attempt == TRIES:
                    raise Stopped(f"{urlsplit(url).netloc} did not answer after {TRIES} tries ({error.__class__.__name__})")
                continue
            self.last = time.monotonic()
            title = (re.search(r"<title[^>]*>(.*?)</title>", response.text[:5000], re.S | re.I) or [None, ""])[1].strip()
            if response.status_code != 200 or re.search(r"captcha|are you a robot|access denied|request blocked|unusual traffic",
                                                        title + response.text[:2000], re.I):
                raise Stopped(f"{urlsplit(url).netloc} answered HTTP {response.status_code} \"{title[:60]}\" instead of the page")
            return response
        raise AssertionError("unreachable")


def us_date(text: str) -> str:
    """'10/06/2026' or 'October 06, 2026' -> '2026-10-06' (empty when it is not a date)."""
    text = (text or "").strip()
    for pattern in ("%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return ""


def money(text: str) -> str:
    match = re.search(r"-?[\d,]+(?:\.\d+)?", text or "")
    return match.group(0).replace(",", "") if match else ""


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ---- tax sales ----------------------------------------------------------------------------------------------------

TAX_COLUMNS = ["Sale date", "Address", "City", "State", "Zip", "Status", "Sale type", "Precinct", "Sale #", "Minimum bid",
               "Adjudged value", "Judgment date", "Tax years", "Cause #", "HCAD account", "HCAD link", "Sale details URL",
               "Image", "Legal description"]


def sale_type(text: str) -> str:
    """'SALE' / 'ReSale' -> 'Sale' / 'Resale'; codes such as EOS (sale by execution order) stay as written."""
    return text.capitalize() if text.lower() in ("sale", "resale") else text


def tax_sales(fetcher: Fetcher) -> list[dict]:
    page = fetcher.request("GET", TAX_SALES_URL)
    soup = BeautifulSoup(page.text, "lxml")
    sale_date = us_date((re.search(r"Sale Date:\s*([A-Za-z]+ \d{1,2}, \d{4})", soup.get_text(" ")) or [None, ""])[1])
    rows = []
    for item in soup.select("li.listing"):
        body = item.select_one(".listing-body")
        if not body:
            continue
        text = lambda selector: clean(body.select_one(selector).get_text()) if body.select_one(selector) else ""  # noqa: E731
        account = text(".account strong")
        modal = soup.find(id=f"listing-{account}") if account else None
        facts = {}
        if modal:
            for line in modal.select("tr"):
                cells = [clean(cell.get_text(" ")) for cell in line.select("td")]
                if len(cells) >= 2 and cells[0].endswith(":"):
                    facts[cells[0].rstrip(": ")] = cells[1]
        sale = clean(body.select_one("h4").get_text(" ")) if body.select_one("h4") else ""
        status = body.find(lambda tag: tag.name == "div" and tag.get("class") and any(c.endswith("-small") for c in tag["class"]))
        image = item.select_one("img[src]")
        detail = item.find("a", href=re.compile(r"saledetail", re.I)) or (modal.find("a", href=re.compile(r"saledetail", re.I)) if modal else None)
        legal = ""
        if modal:
            heading = modal.find(string=re.compile(r"^\s*Description\s*$"))
            if heading:
                following = heading.find_next(string=lambda s: clean(s) and clean(s) != "Description")
                legal = clean(following or "")
        rows.append({
            "Sale date": sale_date,
            "Address": text(".address").title(),
            "City": text(".city").title(),
            "State": text(".state"),
            "Zip": text(".zip")[:5],
            "Status": clean(status.get_text()) if status else "",
            "Sale type": sale_type(facts.get("Type", "") or (re.search(r"Type:\s*(\S+)", sale) or [None, ""])[1]),
            "Precinct": text(".precinct") or facts.get("Precinct", ""),
            "Sale #": facts.get("Sale#", ""),
            "Minimum bid": money(facts.get("Minimum Bid", "") or text(".minBid")),
            "Adjudged value": money(facts.get("Adjudged Value", "") or text(".adjudgedValue")),
            "Judgment date": us_date(facts.get("Judgment", "")),
            "Tax years": "" if "null" in facts.get("Tax Years in Judgement", "") else facts.get("Tax Years in Judgement", ""),
            "Cause #": facts.get("Cause#", "") or (re.search(r"Cause#:\s*(\S+)", body.get_text(" ")) or [None, ""])[1],
            "HCAD account": account,
            "HCAD link": f"https://public.hcad.org/records/outsider/hc.asp?acct={account}" if account else "",
            "Sale details URL": urljoin(TAX_SALES_URL, detail["href"]) if detail else "",
            "Image": urljoin(TAX_SALES_URL, image["src"]) if image else "",
            "Legal description": legal,
        })
    if not rows:
        raise Stopped("the tax sale page had no property listings (its layout may have changed)")
    return rows


# ---- foreclosure notices ------------------------------------------------------------------------------------------

FORECLOSURE_COLUMNS = ["Doc ID", "Sale date", "File date", "Pages", "Notice URL", "County"]


def form_fields(soup: BeautifulSoup) -> dict[str, str]:
    """The ASP.NET page's hidden state plus the current value of each select, as the browser would post them."""
    fields = {tag["name"]: tag.get("value", "") for tag in soup.select("input[type=hidden][name]")}
    for select in soup.select("select[name]"):
        chosen = select.select_one("option[selected]") or select.select_one("option")
        fields[select["name"]] = chosen.get("value", "") if chosen else ""
    return fields


def options(soup: BeautifulSoup, name: str) -> list[str]:
    select = soup.find("select", attrs={"name": FORECLOSURE_PREFIX + name})
    return [option.get("value", "") for option in select.select("option")] if select else []


def foreclosure_month(fetcher: Fetcher, year: int, month: int, progress=_stderr) -> list[dict]:
    """Notices whose sale date falls in the month: choose the year (the page reloads its month list), then search."""
    soup = BeautifulSoup(fetcher.request("GET", FORECLOSURES_URL).text, "lxml")
    if str(year) not in options(soup, "ddlYear"):
        return []
    if str(month) not in options(soup, "ddlMonth") or form_fields(soup).get(FORECLOSURE_PREFIX + "ddlYear") != str(year):
        fields = form_fields(soup)
        fields.update({"__EVENTTARGET": FORECLOSURE_PREFIX + "ddlYear", "__EVENTARGUMENT": "",
                       FORECLOSURE_PREFIX + "ddlYear": str(year), FORECLOSURE_PREFIX + "rbtlDate": "SaleDate"})
        soup = BeautifulSoup(fetcher.request("POST", FORECLOSURES_URL, data=fields).text, "lxml")
    if str(month) not in options(soup, "ddlMonth"):
        return []
    fields = form_fields(soup)
    fields.update({"__EVENTTARGET": "", "__EVENTARGUMENT": "", FORECLOSURE_PREFIX + "ddlYear": str(year),
                   FORECLOSURE_PREFIX + "ddlMonth": str(month), FORECLOSURE_PREFIX + "rbtlDate": "SaleDate",
                   FORECLOSURE_PREFIX + "txtFileNo": "", FORECLOSURE_PREFIX + "btnSearch": "Search"})
    result = BeautifulSoup(fetcher.request("POST", FORECLOSURES_URL, data=fields).text, "lxml")
    rows, seen, page = [], set(), 1
    while True:
        for line in result.select("table[id$=GridView1] tr"):
            link = line.select_one("a.doclinks[href]")
            spans = {span["id"].rsplit("_", 1)[-1]: clean(span.get_text()) for span in line.select("span[id]")}
            if link and clean(link.get_text()) not in seen:
                seen.add(clean(link.get_text()))
                rows.append({"Doc ID": clean(link.get_text()), "Sale date": us_date(spans.get("lblSaleDate", "")),
                             "File date": us_date(spans.get("lblFileDate", "")), "Pages": spans.get("lblPages", ""),
                             "Notice URL": urljoin(FORECLOSURES_URL, link["href"]), "County": "Harris"})
        # the results come 38 to a page; the pager posts back "Page$N" to the grid
        target = next((m.group(1) for m in re.finditer(r"__doPostBack\('([^']+GridView1)','Page\$(\d+)'\)", str(result))
                       if int(m.group(2)) == page + 1), None)
        if not target:
            return rows
        page += 1
        progress(f"{year}-{month:02d}: page {page}")
        fields = form_fields(result)
        fields.update({"__EVENTTARGET": target, "__EVENTARGUMENT": f"Page${page}", FORECLOSURE_PREFIX + "ddlYear": str(year),
                       FORECLOSURE_PREFIX + "ddlMonth": str(month), FORECLOSURE_PREFIX + "rbtlDate": "SaleDate",
                       FORECLOSURE_PREFIX + "txtFileNo": ""})
        result = BeautifulSoup(fetcher.request("POST", FORECLOSURES_URL, data=fields).text, "lxml")


def month_range(start: str, end: str) -> list[tuple[int, int]]:
    first = datetime.strptime(start, "%Y-%m").date()
    last = datetime.strptime(end, "%Y-%m").date()
    months = []
    while first <= last:
        months.append((first.year, first.month))
        first = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return months


# ---- HCAD appraisal roll (bulk files) -----------------------------------------------------------------------------
# The yearly public data files from hcad.org -> Public Data -> Download Property Data. Download them yourself into one
# folder: Real_acct_owner.zip, Real_building_land.zip, Real_jur_exempt.zip and Code_description_real.zip.

HCAD_COLUMNS = ["HCAD account", "Address", "City", "Zip", "Property class", "Market area", "Neighborhood", "Year built",
                "Living area (sqft)", "Land area (sqft)", "Acreage", "Bedrooms", "Full baths", "Half baths", "Quality",
                "Market value", "Appraised value", "Land value", "Building value", "Prior market value", "Owner since",
                "Owner", "Owner type", "Mailing address", "Mailing city", "Mailing state", "Mailing zip", "Absentee owner",
                "Out of state owner", "Homestead", "Mail undeliverable", "Legal description", "HCAD link"]
BUILDING_FIELDS = ["Year built", "Living area (sqft)", "Bedrooms", "Full baths", "Half baths", "Quality"]
# Homestead-type exemptions only; age, disability and veteran exemptions are left out on purpose.
HOMESTEAD = {"RES", "PAR", "APR", "FIR"}
OWNER_TYPES = [
    ("Government", re.compile(r"\b(CITY OF|COUNTY|STATE OF|HOUSING AUTH|ISD|UNITED STATES|USA|TXDOT|PORT OF|MUD|"
                              r"MUNICIPAL|DISTRICT|AUTHORITY)\b")),
    ("Estate", re.compile(r"\b(ESTATE OF|EST OF|ESTATE)\b|\bEST$")),
    ("Trust", re.compile(r"\b(TRUST|TRUSTEE|TRUSTEES|TRST)\b|\bTR$")),
    ("Company", re.compile(r"\b(LLC|L L C|INC|CORP|CORPORATION|CO|COMPANY|LP|LLP|LTD|PARTNERS|PARTNERSHIP|HOLDINGS|"
                           r"PROPERTIES|PROPERTY|INVESTMENTS?|REALTY|BANK|ASSN|ASSOCIATION|FUND|GROUP|VENTURES|HOMES|"
                           r"DEVELOPMENT|BUILDERS|CHURCH|MINISTRIES|CAPITAL|ENTERPRISES|MANAGEMENT|RENTALS?|ASSETS)\b")),
]
STREET_WORDS = {"STREET": "ST", "DRIVE": "DR", "AVENUE": "AVE", "ROAD": "RD", "LANE": "LN", "COURT": "CT",
                "BOULEVARD": "BLVD", "CIRCLE": "CIR", "PLACE": "PL", "PARKWAY": "PKWY", "TRAIL": "TRL", "HIGHWAY": "HWY"}


def hcad_rows(folder: Path, archive: str, member: str, wanted: list[str]):
    """Rows of one tab-separated file inside an HCAD zip, as dicts of the wanted columns (values stripped)."""
    import io
    import zipfile
    path = folder / archive
    if not path.is_file():
        raise Stopped(f"{path} is missing; download it from hcad.org (Public Data -> Download Property Data)")
    with zipfile.ZipFile(path) as bundle, io.TextIOWrapper(bundle.open(member), encoding="cp1252", errors="replace", newline="") as file:
        reader = csv.reader(file, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = [name.strip() for name in next(reader)]
        index = [header.index(name) for name in wanted]
        for line in reader:
            if len(line) >= len(header):
                yield {name: line[position].strip() for name, position in zip(wanted, index)}


def owner_type(name: str) -> str:
    upper = re.sub(r"[.,]", " ", name.upper())
    return next((label for label, pattern in OWNER_TYPES if pattern.search(upper)), "Individual" if upper.strip() else "")


STREET_TYPES = set(STREET_WORDS.values()) | {"WAY", "LOOP", "TER", "PT", "XING", "SQ", "FWY", "EXPY", "BND", "CV", "GLN", "HOLW",
                                             "MDWS", "PATH", "RUN", "TRCE", "VW", "VIS", "WALK", "ROW", "PASS"}
DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST"}


def street_key(address: str) -> tuple[str, str]:
    """('916', 'HARRINGTON') for '916 HARRINGTON ST' and '916 Harrington'; empty when there is no house number."""
    words = [STREET_WORDS.get(word, word) for word in re.sub(r"[^A-Z0-9 ]", " ", address.upper()).split()]
    if not words or not words[0].isdigit():
        return ("", "")
    name = next((word for word in words[1:] if word not in DIRECTIONS and word not in STREET_TYPES), "")
    return (words[0].lstrip("0") or "0", name)


def absentee(row: dict) -> str:
    """Yes / No / PO box / Unknown: does the owner's mailing address point somewhere other than the property?"""
    mail = clean(row["mail_addr_1"])
    if not mail:
        return "Unknown"
    if re.match(r"P\.?\s*O\.?\s*BOX|BOX\s+\d", mail.upper()):
        return "PO box"
    mail_key, site_key = street_key(mail), street_key(row["site_addr_1"])
    if not mail_key[0] or not site_key[0]:
        return "Unknown"
    mail_zip, site_zip = row["mail_zip"][:5], row["site_addr_3"][:5]
    if mail_key == site_key and (not mail_zip or not site_zip or mail_zip == site_zip):
        return "No"
    return "Yes"


def hcad_date(text: str) -> str:
    return us_date(text) if text and not text.startswith("12/30/1899") else ""


def hcad_number(text: str) -> str:
    return "" if not text or re.fullmatch(r"0+(\.0+)?", text) else text


def hcad_buildings(folder: Path, accounts: set[str]) -> dict[str, dict]:
    """Year built, living area and quality from the first building, and room counts, for the given accounts."""
    found: dict[str, dict] = {}
    for row in hcad_rows(folder, "Real_building_land.zip", "building_res.txt",
                         ["acct", "bld_num", "date_erected", "heat_ar", "dscr"]):
        if row["acct"] in accounts and (row["acct"] not in found or row["bld_num"] == "1"):
            found[row["acct"]] = {"Year built": hcad_number(row["date_erected"]), "Living area (sqft)": hcad_number(row["heat_ar"]),
                                  "Quality": row["dscr"]}
    rooms = {"RMB": "Bedrooms", "RMF": "Full baths", "RMH": "Half baths"}
    for row in hcad_rows(folder, "Real_building_land.zip", "fixtures.txt", ["acct", "bld_num", "type", "units"]):
        if row["acct"] in accounts and row["type"] in rooms and row["bld_num"] in ("1", ""):
            found.setdefault(row["acct"], {})[rooms[row["type"]]] = hcad_number(row["units"].split(".")[0]) or "0"
    return found


def hcad_homesteads(folder: Path, accounts: set[str] | None = None) -> set[str]:
    if not (folder / "Real_jur_exempt.zip").is_file():
        return set()
    return {row["acct"] for row in hcad_rows(folder, "Real_jur_exempt.zip", "jur_exempt_cd.txt", ["acct", "exempt_cat"])
            if (accounts is None or row["acct"] in accounts) and HOMESTEAD & set(row["exempt_cat"].split())}


def hcad_codes(folder: Path) -> dict[str, str]:
    """State class code -> description, plus "n:<code>" -> neighborhood name."""
    codes = {}
    if (folder / "Code_description_real.zip").is_file():
        codes = {row["Code"]: re.sub(r"^Real,\s*", "", row["Description"])
                 for row in hcad_rows(folder, "Code_description_real.zip", "desc_r_01_state_class.txt", ["Code", "Description"])}
    codes.update({"n:" + row["cd"]: row["dscr"].title()
                  for row in hcad_rows(folder, "Real_acct_owner.zip", "real_neighborhood_code.txt", ["cd", "dscr"])})
    return codes


ACCOUNT_FIELDS = ["acct", "mailto", "mail_addr_1", "mail_addr_2", "mail_city", "mail_state", "mail_zip", "mail_country",
                  "undeliverable", "site_addr_1", "site_addr_2", "site_addr_3", "state_class", "Market_Area_1_Dscr",
                  "Neighborhood_Code", "land_ar", "acreage", "land_val", "bld_val", "tot_appr_val", "tot_mkt_val",
                  "prior_tot_mkt_val", "new_own_dt", "lgl_1", "lgl_2", "lgl_3", "lgl_4"]


def hcad_record(row: dict, classes: dict[str, str]) -> dict:
    mail = clean(" ".join([row["mail_addr_1"], row["mail_addr_2"]]))
    abroad = bool(row["mail_country"]) and row["mail_country"].upper() not in ("US", "USA", "UNITED STATES")
    return {
        "HCAD account": row["acct"],
        "Address": row["site_addr_1"].title(),
        "City": row["site_addr_2"].title(),
        "Zip": row["site_addr_3"],
        "Property class": f"{row['state_class']} {classes.get(row['state_class'], '')}".strip(),
        "Market area": row["Market_Area_1_Dscr"],
        "Neighborhood": classes.get("n:" + row["Neighborhood_Code"], row["Neighborhood_Code"]),
        "Land area (sqft)": hcad_number(row["land_ar"]),
        "Acreage": hcad_number(row["acreage"]),
        "Market value": hcad_number(row["tot_mkt_val"]),
        "Appraised value": hcad_number(row["tot_appr_val"]),
        "Land value": hcad_number(row["land_val"]),
        "Building value": hcad_number(row["bld_val"]),
        "Prior market value": hcad_number(row["prior_tot_mkt_val"]),
        "Owner since": hcad_date(row["new_own_dt"]),
        "Owner": row["mailto"],
        "Owner type": owner_type(row["mailto"]),
        "Mailing address": mail,
        "Mailing city": row["mail_city"],
        "Mailing state": row["mail_state"],
        "Mailing zip": row["mail_zip"],
        "Absentee owner": absentee(row),
        "Out of state owner": "Yes" if abroad or (row["mail_state"] and row["mail_state"].upper() != "TX") else
                              ("Unknown" if not row["mail_state"] else "No"),
        "Mail undeliverable": "Yes" if row["undeliverable"].upper() == "Y" else "No",
        "Legal description": clean(" ".join(row[f"lgl_{n}"] for n in range(1, 5))),
        "HCAD link": f"https://public.hcad.org/records/outsider/hc.asp?acct={row['acct']}",
    }


def hcad_properties(folder: Path, args: argparse.Namespace, progress=_stderr) -> list[dict]:
    classes = hcad_codes(folder)
    zips = tuple(args.zip or ())
    wanted_classes = {code.upper() for code in args.property_class}
    wanted_types = {kind.capitalize() for kind in args.owner_type or ()}
    today = date.today()
    rows = []
    progress("reading the appraisal roll (about 1.6 million accounts)")
    for row in hcad_rows(folder, "Real_acct_owner.zip", "real_acct.txt", ACCOUNT_FIELDS):
        if wanted_classes and row["state_class"].upper() not in wanted_classes:
            continue
        if zips and not row["site_addr_3"].startswith(zips):
            continue
        value = float(row["tot_mkt_val"] or 0)
        if (args.min_value and value < args.min_value) or (args.max_value and value > args.max_value):
            continue
        record = hcad_record(row, classes)
        if args.absentee and record["Absentee owner"] != "Yes":
            continue
        if args.out_of_state and record["Out of state owner"] != "Yes":
            continue
        if wanted_types and record["Owner type"] not in wanted_types:
            continue
        if args.owned_years:
            since = record["Owner since"]
            if not since or (today - date.fromisoformat(since)).days < args.owned_years * 365.25:
                continue
        rows.append(record)
    accounts = {record["HCAD account"] for record in rows}
    progress(f"{len(rows):,} accounts match; adding exemptions and buildings")
    homesteads = hcad_homesteads(folder, accounts)
    for record in rows:
        record["Homestead"] = "Yes" if record["HCAD account"] in homesteads else "No"
    if args.no_homestead:
        rows = [record for record in rows if record["Homestead"] == "No"]
        accounts = {record["HCAD account"] for record in rows}
    buildings = hcad_buildings(folder, accounts)
    for record in rows:
        record.update(buildings.get(record["HCAD account"], {}))
    if args.built_before:
        rows = [record for record in rows if record.get("Year built") and int(record["Year built"]) < args.built_before]
    return rows


def add_hcad_details(rows: list[dict], folder: Path) -> list[str]:
    """Property facts from the HCAD files for rows that carry an HCAD account (no owner fields)."""
    accounts = {row["HCAD account"] for row in rows if row.get("HCAD account")}
    classes = hcad_codes(folder)
    facts = {}
    for row in hcad_rows(folder, "Real_acct_owner.zip", "real_acct.txt", ACCOUNT_FIELDS):
        if row["acct"] in accounts:
            record = hcad_record(row, classes)
            facts[row["acct"]] = {name: record[name] for name in ("Property class", "Land area (sqft)", "Market value")}
    homesteads = hcad_homesteads(folder, accounts)
    for account, extra in hcad_buildings(folder, accounts).items():
        facts.setdefault(account, {}).update(extra)
    for row in rows:
        row.update(facts.get(row.get("HCAD account"), {}))
        if row.get("HCAD account") in facts:
            row["Homestead"] = "Yes" if row["HCAD account"] in homesteads else "No"
    return ["Property class", "Year built", "Living area (sqft)", "Land area (sqft)", "Bedrooms", "Full baths", "Half baths",
            "Quality", "Market value", "Homestead"]


# ---- output -------------------------------------------------------------------------------------------------------

def save(rows: list[dict], columns: list[str], name: str, out_dir: Path, excel_limit: int = 0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    source = out_dir / f"{name}.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    if excel_limit and len(rows) > excel_limit:
        print(f"wrote {len(rows):,} rows to {source}. That is more than --limit {excel_limit:,}, so no Excel file was made: "
              "narrow the filters (--zip, --class, --absentee, ...) or raise --limit (Excel holds up to 1,048,575 rows).",
              file=sys.stderr)
        return source
    result = excel.build(source, source.with_suffix(".xlsx"))
    print(f"wrote {result['rows']:,} rows to {source} and {source.with_suffix('.xlsx')}", file=sys.stderr)
    return source.with_suffix(".xlsx")


# ---- DataForge jobs ------------------------------------------------------------------------------------------------

HCAD_FILTERS = ("zip", "property_class", "absentee", "out_of_state", "no_homestead", "owner_type", "owned_years", "built_before",
                "min_value", "max_value")
HCAD_MAX_ROWS = 300_000


def make_job_kinds() -> dict:
    """public_tax_sales, public_foreclosures and public_hcad: each stages its rows as a new dataset."""
    from types import SimpleNamespace

    from dataforge_scraping.signals import PURPOSES

    from . import datasets, sources
    from .jobs import JobKind, JobValidationError

    def common(params: dict) -> dict:
        if params.get("policy_acknowledgement") is not True:
            raise JobValidationError("Confirm that you are authorized to collect and use this data")
        if params.get("purpose") not in PURPOSES:
            raise JobValidationError("Choose the purpose of this collection (for example internal analysis or lead research)")
        return {"policy_acknowledgement": True, "purpose": params["purpose"]}

    def hcad_folder(raw: object, required: bool) -> str | None:
        if not raw:
            if required:
                raise JobValidationError("Choose the folder with the HCAD files (Real_acct_owner.zip and the others)")
            return None
        folder = Path(str(raw))
        if not folder.is_absolute() or not (folder / "Real_acct_owner.zip").is_file():
            raise JobValidationError("The HCAD folder must contain Real_acct_owner.zip (download it from hcad.org, Public Data)")
        return str(folder.resolve())

    def allowed(store, url: str, purpose: str) -> None:
        decision = sources.check_navigation(store, url, purpose)
        if not decision["allowed"]:
            raise Stopped(decision["reason"])

    def stage(context, rows: list[dict], source_name: str, name: str) -> dict:
        if not rows:
            return {"dataset_id": None, "rows": 0, "message": "Nothing matched; no dataset was created"}
        project_id = context.store.job(context.job_id)["project_id"]
        imported = datasets.register_staged_rows(context.store, project_id, rows, source_name, name)
        return {"dataset_id": imported.dataset_id, "rows": len(rows)}

    def validate_tax(store, params: dict) -> dict:
        return {**common(params), "hcad_dir": hcad_folder(params.get("hcad_dir"), False)}

    def run_tax(context) -> dict:
        allowed(context.store, TAX_SALES_URL, context.params["purpose"])
        context.stage("reading_tax_sale_list")
        rows = tax_sales(Fetcher())
        if context.params.get("hcad_dir"):
            context.checkpoint()
            context.stage("adding_hcad_facts")
            add_hcad_details(rows, Path(context.params["hcad_dir"]))
        sale = rows[0]["Sale date"] or date.today().isoformat()
        return stage(context, rows, f"harris_tax_sale_{sale}.json", f"Harris County tax sale {sale} ({len(rows)} properties)")

    def validate_notices(store, params: dict) -> dict:
        start = str(params.get("from") or date.today().strftime("%Y-%m"))
        end = str(params.get("to") or start)
        try:
            months = month_range(start, end)
        except ValueError as error:
            raise JobValidationError("Sale months must look like 2026-11") from error
        if not 1 <= len(months) <= 12:
            raise JobValidationError("Choose between 1 and 12 sale months")
        return {**common(params), "from": start, "to": end}

    def run_notices(context) -> dict:
        allowed(context.store, FORECLOSURES_URL, context.params["purpose"])
        fetcher, rows = Fetcher(), []
        for year, month in month_range(context.params["from"], context.params["to"]):
            context.checkpoint()
            context.stage("reading_month", {"month": f"{year}-{month:02d}"})
            rows += foreclosure_month(fetcher, year, month, progress=lambda message: context.stage("reading_page", {"detail": message}))
        start, end = context.params["from"], context.params["to"]
        span = start if start == end else f"{start} to {end}"
        return stage(context, rows, "harris_foreclosure_notices.json", f"Harris County foreclosure notices {span} ({len(rows)})")

    def validate_hcad(store, params: dict) -> dict:
        confirmed = common(params)
        folder = hcad_folder(params.get("data_dir"), True)
        filters = {key: params.get(key) for key in HCAD_FILTERS if params.get(key) not in (None, "", [], False)}
        filters["property_class"] = [str(code).upper() for code in (filters.get("property_class") or ["A1"])]
        if filters.get("owner_type") and not set(filters["owner_type"]) <= {"individual", "company", "trust", "estate", "government"}:
            raise JobValidationError("Owner types are individual, company, trust, estate, or government")
        narrowing = ("zip", "absentee", "out_of_state", "no_homestead", "owner_type", "owned_years", "built_before", "min_value", "max_value")
        if not any(filters.get(key) for key in narrowing):
            raise JobValidationError("Add at least one filter (ZIP codes, absentee owners, ...): the whole county is about 1.6 million accounts")
        return {**confirmed, "data_dir": folder, **filters}

    def run_hcad(context) -> dict:
        params = context.params
        args = SimpleNamespace(**{key: params.get(key) for key in HCAD_FILTERS})
        args.property_class = params.get("property_class") or ["A1"]
        for flag in ("absentee", "out_of_state", "no_homestead"):
            setattr(args, flag, bool(getattr(args, flag)))
        rows = hcad_properties(Path(params["data_dir"]), args, progress=lambda message: context.stage("hcad", {"detail": message}))
        if len(rows) > HCAD_MAX_ROWS:
            raise ValueError(f"{len(rows):,} properties match: narrow the filters to at most {HCAD_MAX_ROWS:,}")
        flags = "".join(f" {flag.replace('_', '-')}" for flag in ("absentee", "out_of_state", "no_homestead") if getattr(args, flag))
        label = " ".join(args.property_class) + (" " + " ".join(args.zip) if args.zip else "") + flags
        return stage(context, rows, "harris_hcad.json", f"HCAD {label} ({len(rows):,} properties)")

    return {
        "public_tax_sales": JobKind(run=run_tax, validate=validate_tax),
        "public_foreclosures": JobKind(run=run_notices, validate=validate_notices),
        "public_hcad": JobKind(run=run_hcad, validate=validate_hcad),
    }


def main(argv: list[str] | None = None) -> Path | None:
    parser = argparse.ArgumentParser(description="Collect Harris County public property records into Excel.")
    parser.add_argument("--out-dir", type=Path, default=Path.home() / "Downloads", help="where to save (default: Downloads)")
    commands = parser.add_subparsers(dest="command", required=True)
    taxes = commands.add_parser("tax-sales", help="properties in the next Harris County delinquent-tax sale")
    taxes.add_argument("--hcad", type=Path, help="folder with the HCAD files, to add year built, area, rooms and market value")
    notices = commands.add_parser("foreclosures", help="trustee foreclosure notices by sale month")
    this_month = date.today().strftime("%Y-%m")
    notices.add_argument("--from", dest="start", default=this_month, help="first sale month, YYYY-MM (default: this month)")
    notices.add_argument("--to", dest="end", help="last sale month, YYYY-MM (default: same as --from)")
    roll = commands.add_parser("hcad", help="filter the HCAD appraisal roll (files you downloaded from hcad.org)")
    roll.add_argument("--data", type=Path, required=True, help="folder with Real_acct_owner.zip, Real_building_land.zip, ...")
    roll.add_argument("--zip", nargs="+", help="property ZIP codes (or prefixes such as 770)")
    roll.add_argument("--class", dest="property_class", nargs="+", default=["A1"],
                      help="HCAD state classes (default A1 = single-family; B1/B2 multi-family, C1 vacant lots, ...)")
    roll.add_argument("--absentee", action="store_true", help="owner mails to another address (not a PO box)")
    roll.add_argument("--out-of-state", action="store_true", help="owner mails outside Texas")
    roll.add_argument("--no-homestead", action="store_true", help="no homestead exemption (likely not owner-occupied)")
    roll.add_argument("--owner-type", nargs="+", choices=["individual", "company", "trust", "estate", "government"])
    roll.add_argument("--owned-years", type=int, help="owned for at least this many years")
    roll.add_argument("--built-before", type=int, help="year built before this year")
    roll.add_argument("--min-value", type=float, help="minimum market value")
    roll.add_argument("--max-value", type=float, help="maximum market value")
    roll.add_argument("--limit", type=int, default=50_000, help="largest list written to Excel (the CSV always has all)")
    args = parser.parse_args(argv)

    fetcher = Fetcher()
    try:
        if args.command == "hcad":
            rows = hcad_properties(args.data, args)
            if not rows:
                print("no properties match those filters", file=sys.stderr)
                return None
            parts = ["harris_hcad", "-".join(args.property_class).lower()] + (["-".join(args.zip)] if args.zip else []) + \
                [flag for flag in ("absentee", "out_of_state", "no_homestead") if getattr(args, flag)]
            return save(rows, HCAD_COLUMNS, "_".join(parts + [f"{len(rows)}_properties"]), args.out_dir, args.limit)
        if args.command == "tax-sales":
            rows = tax_sales(fetcher)
            columns = TAX_COLUMNS
            if args.hcad:
                print("  adding HCAD property facts...", file=sys.stderr)
                extra = add_hcad_details(rows, args.hcad)
                columns = TAX_COLUMNS[:TAX_COLUMNS.index("HCAD account")] + extra + TAX_COLUMNS[TAX_COLUMNS.index("HCAD account"):]
            stamp = rows[0]["Sale date"] or date.today().isoformat()
            return save(rows, columns, f"harris_tax_sale_{stamp}_{len(rows)}_properties", args.out_dir)
        rows = []
        months = month_range(args.start, args.end or args.start)
        for year, month in months:
            found = foreclosure_month(fetcher, year, month)
            print(f"  {year}-{month:02d}: {len(found)} notices", file=sys.stderr)
            rows += found
        if not rows:
            print("no notices for those sale months", file=sys.stderr)
            return None
        span = f"{args.start}_to_{args.end}" if args.end and args.end != args.start else args.start
        return save(rows, FORECLOSURE_COLUMNS, f"harris_foreclosure_notices_{span}_{len(rows)}", args.out_dir)
    except Stopped as stop:
        print(f"stopped: {stop}", file=sys.stderr)
        return None


if __name__ == "__main__":
    main()
