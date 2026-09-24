"""Record details: everything a scraped record's values, element, page, and detail page say about it, beyond the
preset's declared fields.

Levels (preset `details.level`, overridden per job with `detail_level`):

- ``none``: the preset's fields and provenance only.
- ``basic``: plus *value details* derived from each field: price amount and ISO currency, phone numbers in E.164
  with country and line type, e-mail and URL domains, absolute links, ISO dates, ratings with their scale, numbers
  with units, stock status, US address parts, and word counts.
- ``standard`` (default): plus the record's *element* (``item.*``: heading, text, every link and image, prices
  including struck-through originals, rating, availability, dates, contacts, data attributes, microdata) and the
  *page* it came from (``page.*``: title, description, canonical URL, language, Open Graph and Twitter tags,
  breadcrumbs, published and modified dates, structured-data types).
- ``full``: plus the record's *detail page* (``detail.*``): its link is followed under the same scope, robots.txt,
  usage-signal, challenge, and politeness rules as every other request, and the page's primary schema.org entity,
  metadata, main text, key-value specification tables, contacts, social profiles, and images are added.

Only ``full`` makes requests; the other levels read documents that were already fetched. Details never overwrite
a field the preset extracted, and every detail key is namespaced so exports stay traceable.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Callable
from urllib.parse import urljoin, urlparse

from .errors import PolicyViolation

LEVELS = ("none", "basic", "standard", "full")
# Library calls (health checks, archive replays, extract_document) make no requests, so they default to standard.
DEFAULT_LEVEL = "standard"
# Collection jobs read every record's own page unless a job or preset chooses a lower level.
JOB_DEFAULT_LEVEL = "full"
GROUP_PREFIXES = {"item.": "item", "page.": "page", "detail.": "detail"}
PROVENANCE = frozenset({
    "source_url", "source_retrieved_at", "preset_id", "preset_version", "strategy_used", "extraction_mode", "structured_syntax",
    "structured_conflicts", "schema_type", "source_page", "source_table", "source_row", "text_source",
})
VALUE_SUFFIXES = frozenset({
    "amount", "currency", "e164", "country", "type", "domain", "path", "file_type", "absolute", "normalized", "iso", "value", "scale",
    "in_stock", "quantity", "number", "unit", "street", "city", "state", "postal_code", "word_count",
})
LIST_SEPARATOR = " | "
MAX_TEXT = 5_000
MAX_DETAIL_TEXT = 20_000
MAX_VALUE = 500
MAX_GROUP_ENTRIES = 30
MAX_SPECS = 60
MAX_IMAGES = 20
_NOT_PAGES = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|svg|ico|bmp|pdf|zip|gz|rar|7z|mp[34]|m4a|wav|avi|mov|webm|css|js|json|xml|csv|xlsx?|docx?|pptx?)$", re.I)
_SOCIAL = {
    "facebook.com": "facebook", "fb.com": "facebook", "twitter.com": "twitter", "x.com": "twitter", "instagram.com": "instagram",
    "linkedin.com": "linkedin", "youtube.com": "youtube", "youtu.be": "youtube", "tiktok.com": "tiktok", "pinterest.com": "pinterest",
    "github.com": "github", "threads.net": "threads", "mastodon.social": "mastodon", "bsky.app": "bluesky", "wa.me": "whatsapp",
    "t.me": "telegram", "reddit.com": "reddit", "yelp.com": "yelp", "tripadvisor.com": "tripadvisor",
}


# --- levels ------------------------------------------------------------------------------------------

def level_of(preset: dict | None) -> str:
    details = (preset or {}).get("details") if isinstance((preset or {}).get("details"), dict) else {}
    level = details.get("level", DEFAULT_LEVEL)
    return level if level in LEVELS else DEFAULT_LEVEL


def at_least(preset: dict | None, level: str) -> bool:
    return LEVELS.index(level_of(preset)) >= LEVELS.index(level)


def with_level(preset: dict, level: str | None) -> dict:
    """A copy of the preset pinned to a detail level (jobs pin the resolved level so every engine agrees)."""
    if level is None:
        return preset
    if level not in LEVELS:
        raise ValueError(f"detail level must be one of {', '.join(LEVELS)}")
    return {**preset, "details": {**(preset.get("details") if isinstance(preset.get("details"), dict) else {}), "level": level}}


def detail_group(key: str, record: dict) -> str | None:
    """Which kind of detail a key is: item, page, detail, value, or None for a preset field or provenance."""
    for prefix, group in GROUP_PREFIXES.items():
        if key.startswith(prefix):
            return group
    if "." in key:
        base, suffix = key.rsplit(".", 1)
        if suffix in VALUE_SUFFIXES and base in record:
            return "value"
    return None


def _key(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:60]


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def _number(text: object) -> int | float | None:
    try:
        number = float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() and abs(number) < 1e15 else number


def _join(values: list) -> str:
    return LIST_SEPARATOR.join(dict.fromkeys(str(v) for v in values if v not in (None, "")))


# --- value details -----------------------------------------------------------------------------------

_WORDS = re.compile(r"[a-z]+")
_PRICE_HINTS = {"price", "prices", "cost", "amount", "fee", "fees", "salary", "total", "msrp", "rent", "deposit", "subtotal", "wage", "budget", "fare", "tax"}
_PHONE_HINTS = {"phone", "phones", "tel", "telephone", "mobile", "cell", "fax", "whatsapp"}
_DATE_HINTS = {"date", "time", "datetime", "published", "updated", "modified", "created", "posted", "expires", "expiry", "deadline", "start", "end",
               "since", "until", "through", "released", "release", "founded", "timestamp", "pubdate"}
_RATING_HINTS = {"rating", "stars", "star", "score", "grade"}  # "ratings" and "reviews" are counts, not a rating
_NUMBER_HINTS = {"count", "reviews", "ratings", "sales", "votes", "views", "quantity", "qty", "stock", "number", "num", "bedrooms", "bathrooms", "beds", "baths", "rooms",
                 "area", "size", "sqft", "floor", "year", "mileage", "weight", "length", "width", "height", "depth", "pages", "followers", "likes",
                 "comments", "downloads", "installs", "capacity", "seats", "doors", "age", "population", "employees", "distance", "duration"}
_AVAILABILITY_HINTS = {"availability", "available", "stock", "inventory", "instock"}
_ADDRESS_HINTS = {"address", "street"}
_LINK_HINTS = {"link", "links", "url", "href", "permalink", "website", "homepage", "site"}
_CURRENCY_MARK = re.compile(
    r"[$€£¥₹₩₽₺₪฿₫₴₦₱]|\b(?:USD|EUR|GBP|JPY|INR|CAD|AUD|CHF|CNY|RMB|SEK|NOK|DKK|PLN|CZK|HUF|BRL|MXN|ZAR|NZD|SGD|HKD|KRW|TRY|AED|SAR|EGP|zł|kr)\b"
)
_EMAIL_VALUE = re.compile(r"^[\w.+'-]+@[\w-]+(?:\.[\w-]+)+$")
_EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,24}\b", re.I)
_URL_VALUE = re.compile(r"^https?://\S+$", re.I)
_RELATIVE_URL = re.compile(r"^(?:/|\./|\.\./)\S*$")
_PHONE_VALUE = re.compile(r"^\+?[\d\s().\-/]{7,24}(?:\s*(?:x|ext\.?)\s*\d{1,6})?$", re.I)
_OUT_OF = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:out of|/|of)\s*(\d+(?:[.,]\d+)?)", re.I)
_RATING_WORD = re.compile(r"\b(zero|one|two|three|four|five)\b", re.I)
_RATING_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_PLAIN_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)")
_COMPACT_NUMBER = re.compile(r"^\s*\(?([+-]?\d{1,3}(?:[,  ]\d{3})+|[+-]?\d+)(?:\.(\d+))?\)?\s*([kKmMbB]\b|\+|%|[A-Za-z][A-Za-z .²]{0,24})?(?:\s*([A-Za-z][A-Za-z .]{0,24}))?\s*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_DATE_LIKE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
    r"|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    re.I,
)
_OUT_OF_STOCK = re.compile(r"out of stock|sold out|unavailable|not available|no longer available|discontinued|back-?order|pre-?order|currently unavailable", re.I)
_IN_STOCK = re.compile(r"in stock|in-stock|instock|available|ships (?:in|within|today)|ready to ship", re.I)
_STOCK_QUANTITY = re.compile(r"(\d[\d,]*)\s+(?:available|in stock|left|remaining)", re.I)
_PHONE_TYPES = {0: "fixed_line", 1: "mobile", 2: "fixed_line_or_mobile", 3: "toll_free", 4: "premium_rate", 5: "shared_cost", 6: "voip",
                7: "personal_number", 8: "pager", 9: "uan", 10: "voicemail"}


def _hints(key: str) -> set[str]:
    return set(_WORDS.findall(key.rsplit(".", 1)[-1].lower()))


def phone_details(text: str, region: str = "US", strict: bool = True) -> dict[str, str] | None:
    found = _phone_details(text, region, strict)
    return dict(found) if found is not None else None


# Listings repeat the same phones, prices, and dates on many records; parsing each distinct string once keeps large
# runs fast (dateparser in particular costs milliseconds per call).
@lru_cache(maxsize=8192)
def _phone_details(text: str, region: str, strict: bool) -> tuple[tuple[str, str], ...] | None:
    import phonenumbers

    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return None
    if phonenumbers.is_valid_number(number):
        out = [("e164", phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164))]
        country = phonenumbers.region_code_for_number(number)
        if country:
            out.append(("country", country))
        kind = _PHONE_TYPES.get(phonenumbers.number_type(number))
        if kind:
            out.append(("type", kind))
        return tuple(out)
    if not strict and phonenumbers.is_possible_number(number):
        return (("e164", phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)),)
    return None


@lru_cache(maxsize=8192)
def _date(text: str) -> str | None:
    from .normalize import parse_date

    try:
        return parse_date(text)
    except Exception:  # noqa: BLE001 - dateparser raises on odd input; a value simply gets no date
        return None


@lru_cache(maxsize=8192)
def _price(text: str) -> tuple[str | None, str | None]:
    from .normalize import parse_price

    return parse_price(text)


def _rating(text: str, hinted: bool) -> dict | None:
    match = _OUT_OF.search(text)
    if match and ("out of" in text.lower() or hinted):
        value, scale = _number(match.group(1).replace(",", ".")), _number(match.group(2).replace(",", "."))
        if value is not None and scale and 0 <= value <= scale <= 100:
            return {"value": value, "scale": scale}
    if not hinted:
        return None
    word = _RATING_WORD.search(text)
    if word:
        return {"value": _RATING_WORDS[word.group(1).lower()], "scale": 5}
    plain = _PLAIN_NUMBER.search(text)
    if plain:
        value = _number(plain.group(1).replace(",", "."))
        if value is not None and 0 <= value <= 10:
            return {"value": value}
    return None


def _compact_number(text: str, hinted: bool) -> dict | None:
    match = _COMPACT_NUMBER.match(text)
    if not match:
        return None
    whole, fraction, suffix = match.group(1), match.group(2), (match.group(3) or "").strip()
    if suffix == "+":  # "50+ bought in past month": at least 50, the words after it are the unit
        suffix = (match.group(4) or "").strip()
    formatted = bool(re.search(r"[,  ]", whole)) or bool(suffix)
    if not (hinted or formatted):
        return None
    number = _number(re.sub(r"[,  ]", "", whole) + (f".{fraction}" if fraction else ""))
    if number is None:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix.lower()) if len(suffix) == 1 else None
    out: dict[str, object] = {"number": _number(number * multiplier) if multiplier else number}
    if suffix and not multiplier:
        out["unit"] = suffix.lower()
    return out


def _derive(key: str, text: str, base_url: str | None, region: str, element: bool) -> dict[str, object]:
    """Details for one string value. The first kind that fits wins, so a value is never read two ways."""
    hints = _hints(key)
    if _URL_VALUE.match(text):
        if element or hints & {"image", "images", "logo", "photo", "thumbnail", "icon", "picture", "screenshot"}:
            return {}
        from .normalize import registrable_domain

        parsed = urlparse(text)
        out: dict[str, object] = {"domain": registrable_domain(text) or (parsed.hostname or "")}
        if parsed.path not in ("", "/"):
            out["path"] = parsed.path
            extension = re.search(r"\.([a-z0-9]{2,5})$", parsed.path.lower())
            if extension:
                out["file_type"] = extension.group(1)
        return out
    if _RELATIVE_URL.match(text) and hints & _LINK_HINTS and base_url and not element:
        from .normalize import registrable_domain

        absolute = urljoin(base_url, text)
        return {"absolute": absolute, "domain": registrable_domain(absolute) or (urlparse(absolute).hostname or "")}
    if "@" in text and _EMAIL_VALUE.match(text):
        return {} if element else {"normalized": text.lower(), "domain": text.rsplit("@", 1)[1].lower()}
    if len(text) <= 60 and (_DATE_LIKE.search(text) or hints & _DATE_HINTS):
        if _ISO_DATE.match(text):
            return {}
        if _DATE_LIKE.search(text) or not re.search(r"[$€£]", text):
            iso = _date(text)
            if iso:
                return {"iso": iso}
    digits = sum(ch.isdigit() for ch in text)
    if 7 <= digits <= 15 and _PHONE_VALUE.match(text) and (hints & _PHONE_HINTS or text.startswith("+") or "(" in text or re.search(r"\d[\s.\-]\d{3}[\s.\-]\d", text)):
        phone = phone_details(text, region, strict=not hints & _PHONE_HINTS)
        if phone:
            return phone
    if len(text) <= 80 and re.search(r"\d", text) and (_CURRENCY_MARK.search(text) or hints & _PRICE_HINTS):
        amount, currency = _price(text)
        if amount is not None:
            out = {"amount": _number(amount)}
            if currency:
                out["currency"] = currency
            return out
    if len(text) <= 60 and (hints & _RATING_HINTS or "out of" in text.lower()):
        rating = _rating(text, bool(hints & _RATING_HINTS))
        if rating:
            return rating
    if hints & _AVAILABILITY_HINTS and len(text) <= 200:
        out = {}
        if _OUT_OF_STOCK.search(text):
            out["in_stock"] = "false"
        elif _IN_STOCK.search(text):
            out["in_stock"] = "true"
        quantity = _STOCK_QUANTITY.search(text)
        if quantity:
            out["quantity"] = _number(quantity.group(1))
        if out:
            return out
    if len(text) <= 40:
        number = _compact_number(text, bool(hints & _NUMBER_HINTS))
        if number:
            return number
    if element:
        return {}
    if hints & _ADDRESS_HINTS and region == "US" and 8 <= len(text) <= 200:
        from .normalize import parse_us_address

        parts = parse_us_address(text)
        if len(parts) >= 2 and ("street" in parts or "postal_code" in parts):
            return dict(parts)
    words = len(text.split())
    if words >= 40:
        return {"word_count": words}
    return {}


_NO_VALUE_DETAILS = frozenset({"item.text", "item.heading", "item.title", "item.image_alt", "detail.text"})


def value_details(record: dict, base_url: str | None = None, region: str = "US", keys: list[str] | None = None) -> dict[str, object]:
    """Derived `<field>.<detail>` values for every string field (provenance, page metadata, and lists excluded)."""
    out: dict[str, object] = {}
    for key in keys if keys is not None else list(record):
        value = record.get(key)
        if key in PROVENANCE or key.startswith("page.") or key in _NO_VALUE_DETAILS or not isinstance(value, str) or LIST_SEPARATOR in value:
            continue
        text = value.strip()
        if not text:
            continue
        for suffix, derived in _derive(key, text, base_url, region, key.startswith("item.")).items():
            name = f"{key}.{suffix}"
            if derived not in (None, "") and name not in record:
                out[name] = derived
    return out


def region_of(preset: dict | None) -> str:
    return str(((preset or {}).get("normalization") or {}).get("default_region", "US"))


# --- element details (the record's own HTML element) -----------------------------------------------

_PRICE_TEXT = re.compile(
    r"(?:\b(?:US|C|A|NZ|HK|S)\$|[$€£¥₹₩₽₺₪฿₫₴₦₱]|\b(?:USD|EUR|GBP|CHF|CAD|AUD|INR|JPY)\s?)\s?\d(?:[\d,.]*\d)?"
    r"|\d(?:[\d,.]*\d)?\s?(?:€|£|zł|kr|Kč|USD|EUR|GBP|CHF)(?![A-Za-z])"
)
# One class token such as "price--old", "was-price", or "list_price" marks a struck-through original price.
_ORIGINAL_PRICE_CLASS = re.compile(r"(?:^|[-_])(?:old|was|original|regular|strike|strikethrough|compare|list|before|rrp|crossed)(?:$|[-_])", re.I)
_RATING_CLASS = re.compile(r"(?:^|[\s_-])(?:star-?rating|rating|ratings|stars?|review-?score|score)(?:$|[\s_-])", re.I)
_RATING_NOISE = re.compile(r"^(?:star|stars|rating|ratings|star-rating|review|reviews|score|icon|fa|fas|far|average|avg|value|small|large)$", re.I)
# Framework bookkeeping, tracking hooks, and lazy-loading image sources (already read as images) are not details.
_NOISY_DATA = re.compile(r"react|^v-|gtm|analytics|track|^ga-|^event|^testid$|^test-?id$|^qa|^nosnippet$|^(?:lazy-)?src(?:set)?$|^original$|^lazy|^dataforge", re.I)
_SKIP_HREF = re.compile(r"^(?:javascript:|#|data:)", re.I)


def _fragment(html: str):
    import lxml.html

    return lxml.html.fragment_fromstring(html or "<div></div>", create_parent="div")


def _document(html: str):
    import lxml.html

    return lxml.html.document_fromstring(re.sub(r"</(?:body|html)\s*>", "", html or "", flags=re.I) or "<html></html>")


def _drop_scripts(root) -> None:
    for node in root.xpath(".//script|.//style|.//noscript|.//template|.//svg"):
        node.drop_tree()


_BLOCKS = frozenset({"address", "article", "aside", "blockquote", "br", "button", "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure",
                     "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "label", "li", "main", "nav", "ol", "option", "p",
                     "section", "select", "summary", "table", "td", "th", "tr", "ul"})


def _text(node) -> str:
    return _collapse(node.text_content()) if node is not None else ""


def _space_blocks(root) -> None:
    """Separate block elements with spaces so "In stock</p><button>Add" reads as two phrases, not "In stockAdd"."""
    for node in root.iter():
        if isinstance(node.tag, str) and node.tag in _BLOCKS:
            node.text = " " + (node.text or "")
            node.tail = " " + (node.tail or "")


def _image_src(img, base_url: str) -> str | None:
    for attribute in ("src", "data-src", "data-lazy-src", "data-original", "data-srcset", "srcset"):
        value = (img.get(attribute) or "").strip()
        if not value:
            continue
        if "srcset" in attribute:
            value = value.split(",")[0].strip().split(" ")[0]
        if value.startswith("data:"):
            continue
        return urljoin(base_url, value)
    return None


# Something shaped like a phone number (+49 30..., (512) 555..., 512-555-0100); prices and counts never are.
_PHONE_SHAPE = re.compile(r"(?<![\w$€£¥.,])(?:\+\d[\d\s().-]{6,}\d|\(\d{2,5}\)\s?\d{3,4}[\s.-]?\d{3,4}|\d{2,5}[\s.-]\d{3,4}[\s.-]\d{3,5})(?![\d.,]*\d%)")


def _phones_in(text: str, region: str, limit: int = 5) -> list[str]:
    if not _PHONE_SHAPE.search(text[:MAX_TEXT]):
        return []  # the matcher costs about a millisecond per card; skip it when nothing looks like a phone number
    import phonenumbers

    found = []
    try:
        for match in phonenumbers.PhoneNumberMatcher(text[:MAX_TEXT], region):
            found.append(phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164))
            if len(found) >= limit:
                break
    except Exception:  # noqa: BLE001 - matcher edge cases must not fail extraction
        return found
    return list(dict.fromkeys(found))


def _contacts(root, text: str, region: str) -> tuple[list[str], list[str]]:
    emails, phones = [], []
    for a in root.xpath(".//a[@href]"):
        href = (a.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            address = href[7:].split("?")[0].strip()
            if _EMAIL_VALUE.match(address):
                emails.append(address.lower())
        elif href.lower().startswith("tel:"):
            phone = phone_details(href[4:].strip(), region, strict=False)
            if phone:
                phones.append(phone["e164"])
    emails.extend(e.lower() for e in _EMAIL_IN_TEXT.findall(text[:MAX_TEXT]) if not e.lower().endswith((".png", ".jpg", ".gif", ".webp", ".svg")))
    phones.extend(_phones_in(text, region))
    return list(dict.fromkeys(emails))[:10], list(dict.fromkeys(phones))[:10]


def _prices(root) -> tuple[list[str], list[str]]:
    """Visible prices in the element, split into current and original (struck-through or labelled was/old)."""
    current, original = [], []
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        own = _collapse((node.text or ""))
        if not own or not _PRICE_TEXT.search(own):
            continue
        struck = any(isinstance(n.tag, str) and (n.tag in ("del", "s", "strike") or any(_ORIGINAL_PRICE_CLASS.search(c) for c in (n.get("class") or "").split()))
                     for n in [node, *node.iterancestors()])
        for match in _PRICE_TEXT.finditer(own):
            (original if struck else current).append(match.group(0).strip())
    return list(dict.fromkeys(current)), list(dict.fromkeys(original))


def _rating_text(root) -> str | None:
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        classes = node.get("class") or ""
        label = node.get("aria-label") or node.get("title") or ""
        if _RATING_CLASS.search(f" {classes} ") or re.search(r"\b(?:stars?|rating)\b", label, re.I):
            if label and re.search(r"\d|one|two|three|four|five", label, re.I):
                return _collapse(label)
            words = [w for w in classes.split() if not _RATING_NOISE.match(w) and not _RATING_CLASS.search(f" {w} ")]
            word = next((w for w in words if w.lower() in _RATING_WORDS or re.fullmatch(r"\d(?:[._-]\d)?", w)), None)
            if word:
                return word.replace("_", ".").replace("-", ".")
            text = _text(node)
            if text and re.search(r"\d", text) and len(text) <= 40:
                return text
            value = node.get("content") or node.get("data-rating") or node.get("data-score")
            if value:
                return value
    return None


def _card_root(html: str):
    root = _fragment(html)
    _drop_scripts(root)
    _space_blocks(root)
    return root


def element_details(html: str, base_url: str, region: str = "US", prefix: str = "item.") -> dict[str, object]:
    """Everything inside one record's element: heading, text, links, images, prices, rating, availability, dates,
    contacts, data attributes, and microdata properties."""
    try:
        root = _card_root(html)
    except Exception:  # noqa: BLE001 - unparseable fragments get no element details
        return {}
    return _element_details(root, base_url, region, prefix)


def _element_details(root, base_url: str, region: str = "US", prefix: str = "item.") -> dict[str, object]:
    out: dict[str, object] = {}
    text = _text(root)
    heading = next((_text(h) for h in root.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6") if _text(h)), None)
    if heading:
        out[prefix + "heading"] = heading[:MAX_VALUE]
    titled = next((t.strip() for t in root.xpath(".//a/@title|.//*[@title]/@title") if t.strip()), None)
    if titled:
        out[prefix + "title"] = titled[:MAX_VALUE]
    if text:
        out[prefix + "text"] = text[:MAX_TEXT]
    links = [urljoin(base_url, h.strip()) for h in root.xpath(".//a/@href") if h.strip() and not _SKIP_HREF.match(h.strip()) and not h.strip().lower().startswith(("mailto:", "tel:"))]
    links = list(dict.fromkeys(links))
    if links:
        out[prefix + "link"] = links[0]
        out[prefix + "link_count"] = len(links)
        if len(links) > 1:
            out[prefix + "links"] = _join(links[:MAX_GROUP_ENTRIES])
    images = [src for img in root.xpath(".//img") if (src := _image_src(img, base_url))]
    images = list(dict.fromkeys(images))
    if images:
        out[prefix + "image"] = images[0]
        if len(images) > 1:
            out[prefix + "images"] = _join(images[:MAX_IMAGES])
    alt = next((a.strip() for a in root.xpath(".//img/@alt") if a.strip()), None)
    if alt:
        out[prefix + "image_alt"] = alt[:MAX_VALUE]
    current, original = _prices(root)
    if current or original:
        out[prefix + "price"] = (current or original)[0]
        if current and original:
            out[prefix + "price_original"] = original[0]
        if len(current) > 1:
            out[prefix + "prices"] = _join(current[:10])
    rating = _rating_text(root)
    if rating:
        out[prefix + "rating"] = rating[:MAX_VALUE]
    availability = next((_text(n) for n in root.xpath(".//*[contains(translate(@class,'AVILBESTOCK','avilbestock'),'avail') or contains(translate(@class,'AVILBESTOCK','avilbestock'),'stock')]") if _text(n)), None)
    if availability is None and text:  # the smallest element that says so, not just the matched words
        stock_nodes = [n for n in root.iter() if isinstance(n.tag, str) and len(_text(n)) <= 120
                       and (_OUT_OF_STOCK.search(_text(n)) or re.search(r"\bin stock\b", _text(n), re.I))]
        availability = min((_text(n) for n in stock_nodes), key=len) if stock_nodes else None
    if availability:
        out[prefix + "availability"] = availability[:MAX_VALUE]
    times = [t.get("datetime") or _text(t) for t in root.xpath(".//time")]
    times = [t for t in times if t]
    if times:
        out[prefix + "datetime"] = times[0][:MAX_VALUE]
    emails, phones = _contacts(root, text, region)
    if emails:
        out[prefix + "email"] = emails[0]
        if len(emails) > 1:
            out[prefix + "emails"] = _join(emails)
    if phones:
        out[prefix + "phone"] = phones[0]
        if len(phones) > 1:
            out[prefix + "phones"] = _join(phones)
    data: dict[str, str] = {}
    for node in root.iterdescendants():
        if not isinstance(node.tag, str):
            continue
        for name, value in node.attrib.items():
            if not name.startswith("data-") or _NOISY_DATA.search(name[5:]) or not value.strip() or len(value) > 200:
                continue
            key = _key(name[5:])
            if key and key not in data and len(data) < MAX_GROUP_ENTRIES:
                data[key] = _collapse(value)
        if len(data) >= MAX_GROUP_ENTRIES:
            break
    out.update({f"{prefix}data.{k}": v for k, v in data.items()})
    props: dict[str, str] = {}
    for node in root.xpath(".//*[@itemprop]"):
        value = node.get("content") or node.get("datetime") or node.get("href") or node.get("src") or _text(node)
        key = _key(node.get("itemprop", "").split()[0] if node.get("itemprop", "").split() else "")
        if key and value and key not in props and len(props) < MAX_GROUP_ENTRIES:
            props[key] = (urljoin(base_url, value) if node.get("href") or node.get("src") else _collapse(value))[:MAX_VALUE]
    out.update({f"{prefix}prop.{k}": v for k, v in props.items()})
    return out


# --- grid fields: every value the cards of one page share --------------------------------------------

MAX_GRID_FIELDS = 40
_STABLE_CLASS = re.compile(r"^[a-zA-Z][\w-]{1,40}$")
_UNSTABLE_CLASS = re.compile(r"\d{3,}|^(?:is|has)-|(?:^|-)(?:active|hover|focus|selected|visible|hidden|open|closed|loaded|loading)(?:$|-)|^js-|^css-|^sc-|^jsx-", re.I)
_NAME_WORDS = (
    "title", "name", "brand", "price", "rating", "reviews", "review", "badge", "label", "tag", "delivery", "shipping", "seller", "vendor", "store",
    "availability", "stock", "discount", "sale", "saving", "offer", "coupon", "color", "colour", "size", "variant", "category", "location", "address",
    "date", "time", "author", "description", "summary", "subtitle", "count", "sold", "points", "unit", "weight", "sku", "model", "status",
    "condition", "origin", "deal", "promo", "tax", "fee", "eta", "distance", "duration", "level", "score", "votes", "views", "likes", "comments",
    "year", "mileage", "beds", "baths", "area", "company", "employer", "salary", "city", "country", "phone", "email", "website", "specs", "feature",
)
_KIND_NAMES = {"price": "price", "rating": "rating", "percent": "discount", "date": "date", "number": "count", "link": "link", "image": "image",
               "availability": "availability"}
# Utility classes ("a-size-base", "a-color-price", "text-bold") describe looks, not meaning, so they never name a field.
_PRESENTATION_WORDS = {"size", "color", "colour", "text", "font", "bg", "background", "border", "row", "col", "grid", "flex", "spacing", "margin",
                       "padding", "align", "weight", "width", "height", "display", "icon"}
# Phrases in the values themselves name a field better than any class ("FREE delivery Thu", "50+ bought in past month").
_VALUE_NAMES = (("delivery", "delivery"), ("shipping", "shipping"), ("coupon", "coupon"), ("bought", "sales"), ("sold", "sales"), ("ratings", "ratings"),
                ("reviews", "reviews"), ("review", "reviews"), ("in stock", "availability"), ("left in stock", "availability"), ("out of stock", "availability"),
                ("save", "savings"), ("% off", "discount"), ("sponsored", "sponsored"), ("best seller", "badge"), ("deal", "deal"), ("new arrival", "badge"),
                ("prime", "prime"), ("pickup", "pickup"), ("returns", "returns"), ("warranty", "warranty"), ("verified", "verified"), ("miles", "distance"),
                ("km", "distance"), ("bed", "beds"), ("bath", "baths"), ("sq ft", "area"), ("sqft", "area"))


def _stable_class(node) -> str | None:
    return next((c for c in (node.get("class") or "").split() if _STABLE_CLASS.match(c) and not _UNSTABLE_CLASS.search(c)), None)


def _step(node) -> str:
    cls = _stable_class(node)
    if cls:
        return f"{node.tag}.{cls}"
    parent = node.getparent()
    same = [c for c in parent if isinstance(c.tag, str) and c.tag == node.tag] if parent is not None else [node]
    return node.tag if len(same) <= 1 else f"{node.tag}:{same.index(node) + 1}"


def _presentational(token: str) -> bool:
    parts = [p for p in re.split(r"[-_]", token.lower()) if p]
    return any(part in _PRESENTATION_WORDS and index < len(parts) - 1 for index, part in enumerate(parts))


def _name_hint(node) -> str:
    for attribute in ("itemprop", "data-testid", "data-test", "data-cy", "data-qa", "name"):
        if node.get(attribute):
            return str(node.get(attribute))
    return " ".join(c for c in (node.get("class") or "").split() if not _UNSTABLE_CLASS.search(c) and not _presentational(c)) or node.tag


def _card_values(root, base_url: str) -> dict[str, tuple[str, str, bool]]:
    """signature -> (value, name hint, inside a heading) for every text, link, image, and descriptive attribute of
    one card. A signature is the element's structural path (tag and stable class per step), so the same field has
    the same signature in every card of a grid."""
    values: dict[str, tuple[str, str, bool]] = {}

    def walk(node, path: str, heading: bool) -> None:
        for child in node:
            if not isinstance(child.tag, str) or child.get("aria-hidden") == "true":
                continue  # aria-hidden parts repeat, in pieces, what an accessible copy already says (split prices)
            signature = f"{path}/{_step(child)}" if path else _step(child)
            in_heading = heading or child.tag in ("h1", "h2", "h3", "h4", "h5", "h6")
            direct = (child.text or "").strip() or any((c.tail or "").strip() for c in child)
            if direct or len(child) == 0:
                text = _text(child)
                if text and len(text) <= 300:
                    values.setdefault(signature, (text, _name_hint(child), in_heading))
            for attribute in ("title", "aria-label", "datetime", "content", "alt", "value"):
                value = child.get(attribute)
                if value and value.strip() and len(value) <= 300 and not (attribute == "value" and child.tag not in ("data", "meter", "progress")):
                    values.setdefault(f"{signature}@{attribute}", (_collapse(value), f"{_name_hint(child)} {attribute}", in_heading))
            href = (child.get("href") or "").strip() if child.tag == "a" else ""
            if href and not _SKIP_HREF.match(href) and not href.lower().startswith(("mailto:", "tel:")):
                values.setdefault(f"{signature}@href", (urljoin(base_url, href), f"{_name_hint(child)} link", in_heading))
            if child.tag == "img":
                source = _image_src(child, base_url)
                if source:
                    values.setdefault(f"{signature}@src", (source, "image", in_heading))
            walk(child, signature, in_heading)

    walk(root, "", False)
    return values


def _value_kind(signature: str, values: list[str]) -> str:
    if signature.endswith("@href"):
        return "link"
    if signature.endswith("@src"):
        return "image"
    votes: dict[str, int] = {}
    for value in values[:20]:
        if _CURRENCY_MARK.search(value) and re.search(r"\d", value) and len(value) <= 40:
            kind = "price"
        elif re.search(r"out of \d|\bstars?\b", value, re.I) and re.search(r"\d", value):
            kind = "rating"
        elif re.fullmatch(r"-?\d+(?:[.,]\d+)?\s?%(?:\s?off)?", value.strip(), re.I):
            kind = "percent"
        elif _DATE_LIKE.search(value) and len(value) <= 40:
            kind = "date"
        elif re.fullmatch(r"[(\[]?\s*[\d.,]+\s*[kKmM+]?\s*[)\]]?", value.strip()):
            kind = "number"
        elif len(value) <= 80 and (_OUT_OF_STOCK.search(value) or re.search(r"\bin stock\b|\bleft in stock\b", value, re.I)):
            kind = "availability"
        else:
            kind = "text"
        votes[kind] = votes.get(kind, 0) + 1
    return max(votes, key=votes.get) if votes else "text"


def _value_name(values: list[str]) -> str | None:
    votes: dict[str, int] = {}
    for value in values[:20]:
        lowered = value.lower()
        for phrase, name in _VALUE_NAMES:
            if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered):
                votes[name] = votes.get(name, 0) + 1
                break
    best = max(votes, key=votes.get) if votes else None
    return best if best and votes[best] * 2 >= min(len(values), 20) else None


def _auto_name(signature: str, hint: str, kind: str, in_heading: bool, values: list[str] | None = None) -> str:
    words = re.findall(r"[a-z]+", hint.lower().replace("-", " ").replace("_", " "))
    attribute = signature.rsplit("@", 1)[1] if "@" in signature.rsplit("/", 1)[-1] else None
    phrase = _value_name(values or []) if kind not in ("link", "image") else None
    if phrase and kind not in ("price",):
        return phrase
    word = next((w for w in _NAME_WORDS if w in words), None) or next((w for w in _NAME_WORDS for token in words if token.startswith(w) and len(w) >= 4), None)
    if kind in ("link", "image"):
        return f"{word}_{kind}" if word and word not in (kind, "title", "name") else kind
    if kind in ("price", "rating", "availability") and word not in ("price", "rating", "discount", "saving", "deal", "offer", "sale", "availability", "stock"):
        word = None
    if word:
        base = word
    elif in_heading:
        base = "title"
    elif kind in _KIND_NAMES:
        base = _KIND_NAMES[kind]
    elif attribute in ("title", "aria-label", "alt"):
        base = {"alt": "image_alt", "title": "title_text", "aria-label": "label"}[attribute]
    elif attribute == "datetime":
        base = "date"
    else:
        base = "text"
    if attribute in ("title", "aria-label") and base not in ("title", "rating", "label", "title_text"):
        base = f"{base}_label"
    return base


def _norm(value: object) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value).lower())


def _same_value(a: str, b: str) -> bool:
    """Equal text, one inside the other, or the same number written two ways ("(1,000)" and "1,000 ratings")."""
    if _norm(a) == _norm(b) or (len(a) < len(b) and a in b):
        return True
    digits_a, digits_b = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    return bool(digits_a) and digits_a == digits_b and len(_norm(a)) <= len(digits_a) + 2


def _grid_schema(cards: list[dict[str, tuple[str, str, bool]]], known: list[dict[str, object]], sample: int = 40) -> list[tuple[str, str]]:
    """Which card signatures become fields, and their keys. A field must appear in enough cards, vary between
    them (constant labels such as "Add to cart" carry nothing), and add something the element details lack."""
    sampled = cards[:sample]
    count = len(sampled)
    if not count:
        return []
    appearances: dict[str, int] = {}
    order: list[str] = []
    for card in sampled:
        for signature in card:
            if signature not in appearances:
                order.append(signature)
            appearances[signature] = appearances.get(signature, 0) + 1
    minimum = 1 if count <= 2 else max(2, -(-count * 15 // 100))
    kept: list[tuple[str, str]] = []
    used: set[str] = set()
    seen_values: list[list[str | None]] = []
    for signature in order:
        present = appearances[signature]
        if present < minimum:
            continue
        values = [card[signature][0] if signature in card else None for card in sampled]
        real = [v for v in values if v is not None]
        # The same text on (nearly) every card is boilerplate such as "Add to cart"; on only some cards it is a flag
        # such as "Best Seller" or "Sponsored", which is worth keeping.
        if count >= 3 and len(set(real)) <= 1 and present * 10 >= count * 9:
            continue
        # skip what the element details already hold, and what another kept field repeats or contains
        enough = max(1, present * 4 // 5)
        known_hits = sum(1 for v, extra in zip(values, known[:sample]) if v is not None and _norm(v) in {_norm(x) for x in extra.values()})
        if known_hits >= enough:
            continue
        if any(sum(1 for a, b in zip(values, other) if a is not None and b is not None and (_same_value(a, b) or _same_value(b, a))) >= enough
               for other in seen_values):
            continue
        hint, in_heading = next(card[signature][1:] for card in sampled if signature in card)
        base = _auto_name(signature, hint, _value_kind(signature, real), in_heading, real)
        key, suffix = f"item.{base}", 2
        while key in used or any(key in extra for extra in known[:sample]):
            key, suffix = f"item.{base}_{suffix}", suffix + 1
        used.add(key)
        kept.append((signature, key))
        seen_values.append(values)
        if len(kept) >= MAX_GRID_FIELDS:
            break
    return kept


def grid_details(htmls: list[str], base_url: str, region: str = "US") -> list[dict[str, object]]:
    """Element details for every card of one page, plus the fields the cards share, detected from the grid itself:
    one consistent set of `item.<name>` keys (prices, ratings, review counts, badges, delivery notes, sellers,
    links, images, labels) for every record, as many as the cards carry."""
    roots = []
    for html in htmls:
        try:
            roots.append(_card_root(html))
        except Exception:  # noqa: BLE001 - an unparseable card gets no details
            roots.append(None)
    known = [_element_details(root, base_url, region) if root is not None else {} for root in roots]
    cards = [_card_values(root, base_url) if root is not None else {} for root in roots]
    schema = _grid_schema(cards, known)
    if len(known) >= 3:  # a data attribute with one value on every card (a component name, a tracking id) is not a field
        for key in {k for extra in known for k in extra if k.startswith("item.data.")}:
            if len({str(extra.get(key)) for extra in known}) == 1:
                for extra in known:
                    extra.pop(key, None)
    out = []
    for extra, values in zip(known, cards):
        record = dict(extra)
        for signature, key in schema:
            if signature in values and key not in record:
                record[key] = values[signature][0][:MAX_VALUE]
        out.append(record)
    return out


def describe_grid(htmls: list[str], base_url: str, region: str = "US") -> list[dict[str, object]]:
    """What DataForge reads from each card of a grid (for Scrape Studio): key, coverage, and example values."""
    records = grid_details(htmls, base_url, region)
    keys = list(dict.fromkeys(key for record in records for key in record))
    total = len(records) or 1
    return [{"key": key, "coverage": round(sum(1 for r in records if r.get(key) not in (None, "")) / total, 3),
             "examples": list(dict.fromkeys(str(r[key])[:80] for r in records if r.get(key) not in (None, "")))[:3]} for key in keys]


# --- page details (the document a record came from) -----------------------------------------------

def _meta(doc) -> dict[str, str]:
    metas: dict[str, str] = {}
    for meta in doc.iter("meta"):
        name = (meta.get("property") or meta.get("name") or meta.get("itemprop") or meta.get("http-equiv") or "").strip().lower()
        content = (meta.get("content") or "").strip()
        if name and content and name not in metas:
            metas[name] = _collapse(content)
    return metas


def _jsonld_nodes(doc) -> list[dict]:
    nodes: list[dict] = []

    def walk(value: object, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, dict):
            nodes.append(value)
            if "@graph" in value:
                walk(value["@graph"], depth + 1)

    for script in doc.xpath("//script[@type='application/ld+json']"):
        try:
            walk(json.loads(script.text_content() or "null", strict=False))
        except ValueError:
            continue
    return nodes


def _types(node: dict) -> list[str]:
    value = node.get("@type")
    return [str(t).rsplit("/", 1)[-1] for t in (value if isinstance(value, list) else [value]) if t]


def _breadcrumbs(doc, nodes: list[dict]) -> str | None:
    for node in nodes:
        if "BreadcrumbList" in _types(node):
            items = node.get("itemListElement") or []
            items = items if isinstance(items, list) else [items]
            names = []
            for item in sorted((i for i in items if isinstance(i, dict)), key=lambda i: _number(i.get("position")) or 0):
                name = item.get("name") or (item.get("item") or {}).get("name") if isinstance(item.get("item"), dict) else item.get("name")
                if name:
                    names.append(_collapse(name))
            if names:
                return " > ".join(names)
    trail = doc.xpath("//*[contains(translate(@aria-label,'BREADCRUMB','breadcrumb'),'breadcrumb') or contains(translate(@class,'BREADCRUMB','breadcrumb'),'breadcrumb')]")
    for container in trail:
        parts = [_text(n) for n in container.xpath(".//li") or container.xpath(".//a")]
        parts = [p for p in parts if p and len(p) <= 80]
        if len(parts) >= 2:
            return " > ".join(dict.fromkeys(parts))
    return None


def page_details(html: str, url: str, prefix: str = "page.", doc=None) -> dict[str, object]:
    """Document-level metadata: title, description, canonical URL, language, dates, Open Graph, Twitter cards,
    breadcrumbs, feeds, and the structured-data types the page declares."""
    if doc is None:
        try:
            doc = _document(html)
        except Exception:  # noqa: BLE001
            return {}
    metas = _meta(doc)
    out: dict[str, object] = {}

    def put(name: str, value: object) -> None:
        if value not in (None, "") and prefix + name not in out:
            out[prefix + name] = value if isinstance(value, (int, float)) else _collapse(value)[:MAX_VALUE]

    put("title", doc.findtext(".//title"))
    put("description", metas.get("description") or metas.get("og:description") or metas.get("twitter:description"))
    put("keywords", metas.get("keywords") or metas.get("news_keywords"))
    canonical = next((link.get("href") for link in doc.iter("link") if "canonical" in (link.get("rel") or "").lower().split() and link.get("href")), None)
    put("canonical", urljoin(url, canonical) if canonical else metas.get("og:url"))
    put("language", doc.get("lang") or doc.get("xml:lang") or metas.get("content-language") or metas.get("og:locale"))
    h1 = next((_text(h) for h in doc.iter("h1") if _text(h)), None)
    put("h1", h1)
    put("author", metas.get("author") or metas.get("article:author") or metas.get("dc.creator") or metas.get("twitter:creator"))
    put("published", metas.get("article:published_time") or metas.get("datepublished") or metas.get("date") or metas.get("pubdate")
        or metas.get("publish-date") or metas.get("dc.date") or metas.get("dcterms.created") or metas.get("og:published_time"))
    put("modified", metas.get("article:modified_time") or metas.get("og:updated_time") or metas.get("datemodified") or metas.get("dcterms.modified")
        or metas.get("last-modified"))
    put("site_name", metas.get("og:site_name") or metas.get("application-name"))
    put("type", metas.get("og:type"))
    image = metas.get("og:image") or metas.get("og:image:url") or metas.get("twitter:image")
    put("image", urljoin(url, image) if image else None)
    put("robots", metas.get("robots"))
    og = [(k, v) for k, v in metas.items() if k.startswith(("og:", "product:", "article:", "book:", "profile:", "music:", "video:"))
          and k not in ("og:description", "og:image", "og:url", "og:site_name", "og:type")]
    for name, value in og[:MAX_GROUP_ENTRIES]:
        put("og." + _key(name.split(":", 1)[1] if name.startswith("og:") else name), value)
    twitter = [(k, v) for k, v in metas.items() if k.startswith("twitter:") and k not in ("twitter:description", "twitter:image")]
    labels = {k: v for k, v in twitter if re.fullmatch(r"twitter:label\d", k)}
    for name, value in twitter[:15]:
        if re.fullmatch(r"twitter:(?:label|data)\d", name):
            continue
        put("twitter." + _key(name.split(":", 1)[1]), value)
    for name, label in labels.items():  # "Reading time: 4 min", "Price: $20" pairs used by link previews
        value = metas.get(name.replace("label", "data"))
        if value:
            put("twitter." + _key(label), value)
    nodes = _jsonld_nodes(doc)
    put("breadcrumbs", _breadcrumbs(doc, nodes))
    types = list(dict.fromkeys(t for node in nodes for t in _types(node)))
    put("structured_types", ", ".join(types[:20]))
    feed = next((link.get("href") for link in doc.iter("link") if "alternate" in (link.get("rel") or "").lower()
                 and re.search(r"rss|atom|feed\+json", link.get("type") or "", re.I) and link.get("href")), None)
    put("feed", urljoin(url, feed) if feed else None)
    return out


# --- detail pages ------------------------------------------------------------------------------------

def _specs(doc) -> dict[str, str]:
    """Key-value pairs from definition lists, two-column tables, and "Label: value" list items."""
    specs: dict[str, str] = {}

    def add(label: str, value: str) -> None:
        label, value = _collapse(label).rstrip(":").strip(), _collapse(value)
        key = _key(label)
        if key and value and len(label) <= 60 and key not in specs and len(specs) < MAX_SPECS:
            specs[key] = value[:MAX_VALUE]

    for dl in doc.iter("dl"):
        for dt in dl.iter("dt"):
            dd = dt.getnext()
            while dd is not None and dd.tag != "dd":
                dd = dd.getnext() if dd.tag not in ("dt",) else None
            if dd is not None:
                add(_text(dt), _text(dd))
    for table in doc.iter("table"):
        rows = table.xpath(".//tr")
        if not rows or len(rows) > 200:
            continue
        pairs = []
        for row in rows:
            cells = [c for c in row if isinstance(c.tag, str) and c.tag in ("th", "td")]
            if len(cells) == 2 and 0 < len(_text(cells[0])) <= 60:
                pairs.append((_text(cells[0]), _text(cells[1])))
        if len(pairs) >= 2 and len(pairs) >= len(rows) // 2:
            for label, value in pairs:
                add(label, value)
    for listing in doc.xpath("//ul|//ol"):
        items = [_text(li) for li in listing if isinstance(li.tag, str) and li.tag == "li"]
        labelled = [i.split(":", 1) for i in items if ":" in i and 0 < len(i.split(":", 1)[0]) <= 40 and re.search(r"[A-Za-z]", i.split(":", 1)[0])
                    and not i.lower().startswith(("http:", "https:"))]
        if len(labelled) >= 2 and len(labelled) >= len(items) // 2:
            for label, value in labelled:
                add(label, value)
    return specs


def _social(doc, base_url: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for href in doc.xpath("//a/@href"):
        if not any(domain in href for domain in _SOCIAL):  # cheap text test before parsing every link
            continue
        absolute = urljoin(base_url, href.strip())
        host = (urlparse(absolute).hostname or "").lower().removeprefix("www.").removeprefix("m.")
        network = _SOCIAL.get(host) or next((name for domain, name in _SOCIAL.items() if host.endswith("." + domain)), None)
        path = urlparse(absolute).path.strip("/")
        if network and network not in found and path and not re.match(r"^(?:share|sharer|intent|home)\b", path, re.I):
            found[network] = absolute
    return found


def _main_images(doc, base_url: str) -> list[str]:
    scope = doc.xpath("//main|//article|//*[@role='main']") or [doc]
    images = []
    for img in scope[0].xpath(".//img"):
        src = _image_src(img, base_url)
        if src and not re.search(r"sprite|icon|logo|pixel|spacer|blank|avatar|badge|placeholder", src, re.I):
            images.append(src)
    return list(dict.fromkeys(images))[:MAX_IMAGES]


def primary_entity(records: list[dict], prefer_type: str | None = None) -> dict | None:
    """The entity a detail page is about: the record's own type first, then the mapping priority, then the richest."""
    from .structured import _resolve_type, mappings

    if not records:
        return None
    priority = mappings().get("priority") or list(mappings()["types"])
    preferred = _resolve_type(prefer_type) if prefer_type else None

    def rank(record: dict) -> tuple:
        mapped = _resolve_type(str(record.get("schema_type", "")))
        return (0 if preferred and mapped == preferred else 1, priority.index(mapped) if mapped in priority else len(priority), -len(record))

    return sorted(records, key=rank)[0]


def detail_fields(html: str, url: str, preset: dict) -> dict[str, object]:
    """The preset's own detail-page fields (`details.follow.fields`): selectors evaluated against the whole detail
    page, with the same transforms and types as listing fields. Returned as `detail.<key>`."""
    fields = follow_fields(preset)
    if not fields or not html:
        return {}
    from parsel import Selector

    from .extraction import _parsel_value, apply_field

    try:
        document = Selector(text=html)
    except Exception:  # noqa: BLE001 - an unparseable page yields no custom fields
        return {}
    body = next(iter(document.xpath("//body")), document)
    out: dict[str, object] = {}
    for field in fields:
        for selector in field.get("selectors", []):
            if not isinstance(selector, dict) or not (selector.get("css") or selector.get("xpath")):
                continue
            value = None
            for attempt in _selector_variants(selector):
                try:
                    # Scrape Studio's structural fallbacks are relative to <body>; other XPath and CSS use the document.
                    value = _parsel_value(body if str(attempt.get("xpath") or "").startswith("./") else document, attempt)
                except Exception:  # noqa: BLE001 - a selector that fails on one page is simply unmatched
                    value = None
                if value is not None:
                    break
            if value is not None:
                converted = apply_field(value[:MAX_DETAIL_TEXT], field, url, preset)
                if converted not in (None, ""):
                    out[f"detail.{field['key']}"] = converted
                    break
    return out


def _selector_variants(selector: dict) -> list[dict]:
    """The selector, then (when it names tbody) the same path without it: browsers insert <tbody> into tables, so a
    path picked in the WebView says "table > tbody > tr" while the page's own HTML may have no tbody at all."""
    variants = [selector]
    css, xpath = str(selector.get("css") or ""), str(selector.get("xpath") or "")
    if "tbody" in css:
        variants.append({**selector, "css": re.sub(r"\s*>\s*tbody(?::nth-of-type\(1\))?(?=\s*>)", "", css)})
    if "tbody" in xpath:
        variants.append({**selector, "xpath": re.sub(r"/tbody(?:\[1\])?(?=/)", "", xpath)})
    return variants


def follow_fields(preset: dict | None) -> list[dict]:
    follow = ((preset or {}).get("details") or {}).get("follow") if isinstance((preset or {}).get("details"), dict) else None
    fields = follow.get("fields") if isinstance(follow, dict) else None
    return [f for f in fields if isinstance(f, dict) and isinstance(f.get("key"), str)] if isinstance(fields, list) else []


def _detail_entities(html: str, url: str) -> tuple[list[dict], list[dict]]:
    """Structured data on a detail page, cheapest syntax first: JSON-LD (about a millisecond) usually names the
    product; Microdata and Microformats come next, and RDFa (the slowest) only when nothing else maps."""
    from .structured import entity_records, extract_entities

    seen: list[dict] = []
    for syntaxes in (("json-ld",), ("microdata", "microformat"), ("rdfa",)):
        try:
            entities = extract_entities(html, url, syntaxes)
            records = entity_records(entities)
        except Exception:  # noqa: BLE001 - structured data is optional on a detail page
            continue
        seen.extend(entities)
        if records:
            return seen, records
    return seen, []


def _main_text(html: str, url: str, precise: bool = False) -> str:
    """The page's main text (boilerplate removed) via trafilatura. Its metadata pass costs ten times the text itself,
    so it runs only for `details.follow.text: precise`; author and dates come from the page's own metadata."""
    try:
        import trafilatura

        article = trafilatura.bare_extraction(html, url=url, with_metadata=precise, include_comments=False, include_tables=False)
        article = (article.as_dict() if hasattr(article, "as_dict") else dict(article)) if article is not None else {}
    except Exception:  # noqa: BLE001 - main-text extraction is best effort
        return ""
    return _collapse(article.get("text") or "")


def _visible_facts(doc, url: str) -> dict[str, object]:
    """What the page shows about its subject even without markup: the main heading, visible current and original
    price, rating, availability, and the feature bullet list."""
    scope = next(iter(doc.xpath("//main|//*[@role='main']|//article")), None)
    if scope is None:
        body = doc.find(".//body")
        scope = body if body is not None else doc
    out: dict[str, object] = {}
    heading = next((_text(h) for h in doc.iter("h1") if _text(h)), None)
    if heading:
        out["detail.heading"] = heading[:MAX_VALUE]
    current, original = _prices(scope)
    if current or original:
        out["detail.price_shown"] = (current or original)[0]
        if current and original:
            out["detail.price_original"] = original[0]
    rating = _rating_text(scope)
    if rating:
        out["detail.rating_shown"] = rating[:MAX_VALUE]
    availability = next((_text(n) for n in scope.xpath(".//*[@id='availability' or contains(translate(@class,'AVILBESTOCK','avilbestock'),'availability') or contains(translate(@class,'AVILBESTOCK','avilbestock'),'stock')]") if _text(n) and len(_text(n)) <= 120), None)
    if availability:
        out["detail.availability_shown"] = availability
    best: list[str] = []
    for listing in scope.xpath(".//ul|.//ol"):
        if any(isinstance(a.tag, str) and a.tag in ("nav", "header", "footer", "aside") for a in listing.iterancestors()):
            continue
        items = [_text(li) for li in listing if isinstance(li.tag, str) and li.tag == "li"]
        items = [i for i in items if 15 <= len(i) <= 400]
        if 3 <= len(items) <= 30 and len(items) > len(best) and not any(":" in i[:40] for i in items[:2]):
            best = items
    if best:
        out["detail.bullets"] = _join(best[:20])
        out["detail.bullet_count"] = len(best)
    return out


def detail_page_details(html: str, url: str, region: str = "US", prefer_type: str | None = None, preset: dict | None = None,
                        custom: dict[str, object] | None = None) -> dict[str, object]:
    """Everything a detail page says about its subject, under `detail.`. The preset's own detail fields come first
    and win over automatic values with the same key; `custom` passes values already extracted (Scrape Studio)."""
    own = dict(custom) if custom is not None else (detail_fields(html, url, preset) if preset else {})
    out: dict[str, object] = dict(own)
    data_keys: list[str] = list(own)
    entities, records = _detail_entities(html, url)
    entity = primary_entity(records, prefer_type)
    if entity:
        for key, value in entity.items():
            name = "detail." + key
            if key in ("structured_syntax", "structured_conflicts") or name in out:
                continue
            out[name] = value
            data_keys.append(name)
    types = list(dict.fromkeys(t for e in entities for t in e["types"]))
    if types:
        out["detail.structured_types"] = ", ".join(types[:20])
    if len(records) > 1:
        out["detail.entity_count"] = len(records)
    try:
        doc = _document(html)
    except Exception:  # noqa: BLE001
        return out
    for key, value in page_details(html, url, prefix="detail.page.", doc=doc).items():
        out.setdefault(key, value)
    for source, target in (("detail.page.author", "detail.author"), ("detail.page.published", "detail.date_published")):
        if source in out and target not in out:
            out[target] = out[source]
    _drop_scripts(doc)
    _space_blocks(doc)
    precise = ((preset or {}).get("details") or {}).get("follow", {}).get("text") == "precise" if isinstance((preset or {}).get("details"), dict) else False
    text = _main_text(html, url, precise)
    if text:
        out["detail.text"] = text[:MAX_DETAIL_TEXT]
        out["detail.word_count"] = len(text.split())
    for key, value in _visible_facts(doc, url).items():
        if key not in out:
            out[key] = value
            data_keys.append(key)
    for key, value in _specs(doc).items():
        name = f"detail.spec.{key}"
        if name not in out:
            out[name] = value
            data_keys.append(name)
    page_text = text or _text(doc.find(".//body") if doc.find(".//body") is not None else doc)
    emails, phones = _contacts(doc, page_text, region)
    if emails:
        out["detail.emails"] = _join(emails)
    if phones:
        out["detail.phones"] = _join(phones)
    for network, link in _social(doc, url).items():
        out[f"detail.social.{network}"] = link
    images = _main_images(doc, url)
    if images:  # the structured-data gallery, when present, stays first
        out.setdefault("detail.image", images[0])
        if len(images) > 1:
            out.setdefault("detail.images", _join(images))
    out.update({k: v for k, v in value_details(out, url, region, data_keys).items() if k not in out})
    return out


def rendered_detail(detail: dict, preset: dict, record: dict) -> dict[str, object]:
    """Details for a detail page Scrape Studio opened in the embedded WebView: the preset's detail fields as the
    page showed them, plus everything read from the page's sanitized copy."""
    from .extraction import apply_field

    url = str(detail.get("url") or "")
    custom: dict[str, object] = {}
    values = detail.get("fields") if isinstance(detail.get("fields"), dict) else {}
    for field in follow_fields(preset):
        value = values.get(field["key"])
        if value not in (None, ""):
            converted = apply_field(" ".join(str(value).split())[:MAX_DETAIL_TEXT], field, url, preset)
            if converted not in (None, ""):
                custom[f"detail.{field['key']}"] = converted
    found: dict[str, object] = {"detail.url": url}
    html = str(detail.get("html") or "")[:5_000_000]
    found.update(detail_page_details(html, url, region_of(preset), str(record.get("schema_type") or "") or None, custom=custom) if html else
                 {**custom, **value_details(custom, url, region_of(preset))})
    found["detail.retrieved_at"] = str(detail.get("retrieved_at") or datetime.now(timezone.utc).isoformat())
    return found


def detail_link(record: dict, preset: dict, field: str | None = None) -> str | None:
    """The record's own detail-page URL: the configured field, a declared URL field, a common link key, or the
    first link in the record's element. Images, files, and the page the record came from are never followed."""
    from .extraction import canonicalize_url

    base = str(record.get("source_url") or "")
    declared = [f["key"] for f in (preset.get("extraction") or {}).get("fields", []) or []
                if isinstance(f, dict) and f.get("type") == "url" and not re.search(r"image|img|thumb|logo|photo|icon|avatar|picture", str(f.get("key")), re.I)]
    keys = [field] if field else declared + ["link", "url", "href", "detail_url", "permalink", "item_url", "product_url", "page_url", "item.link"]
    for key in dict.fromkeys(keys):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip() or LIST_SEPARATOR in value:
            continue
        absolute = urljoin(base, value.strip())
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or _NOT_PAGES.search(parsed.path):
            continue
        if base and canonicalize_url(absolute, preset) == canonicalize_url(base, preset):
            continue
        return absolute
    return None


def follow_detail_pages(
    records: list[dict],
    preset: dict,
    get: Callable[[str], object],
    *,
    limit: int | None = None,
    deadline: float | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    on_page: Callable[[dict], None] = lambda event: None,
    warnings: list[str] | None = None,
) -> dict:
    """Fetch each record's detail page with `get` (the caller's policy-checked request function) and merge its
    details into the record. Returns follow statistics. A stop signal (access, challenge, usage reservation)
    ends following but keeps every record and the details gathered so far."""
    import httpx

    from .extraction import canonicalize_url, validate_url

    config = (preset.get("details") or {}).get("follow") if isinstance((preset.get("details") or {}).get("follow"), dict) else {}
    limit = len(records) if limit is None else limit
    limit = min(limit, int(config.get("max_pages", limit)))
    deadline = deadline if deadline is not None else time.monotonic() + int(config.get("max_duration_seconds", 600))
    region = region_of(preset)
    warnings = warnings if warnings is not None else []
    stats = {"candidates": 0, "fetched": 0, "reused": 0, "failed": 0, "skipped_scope": 0, "skipped_robots": 0, "stop_reason": "completed",
             "fetch_ms": 0, "parse_ms": 0}
    cache: dict[str, dict] = {}
    consecutive_errors = 0
    processed = {"n": 0}

    def report(url: str, status: str, **extra) -> None:
        """One event per record link, so the UI can mark that record's card: done, reused, failed, skipped, stopped."""
        processed["n"] += 1
        on_page({"detail_page": processed["n"], "url": url, "status": status, **extra})

    for record in records:
        if any(key.startswith("detail.") for key in record):
            continue
        target = detail_link(record, preset, config.get("field"))
        if not target:
            continue
        stats["candidates"] += 1
        canonical = canonicalize_url(target, preset)
        if canonical in cache:
            record.update({k: v for k, v in cache[canonical].items() if k not in record})
            stats["reused"] += 1
            report(target, "reused" if cache[canonical] else "skipped", fields=len(cache[canonical]))
            continue
        if stats["fetched"] + stats["failed"] >= limit:
            stats["stop_reason"] = "max_detail_pages"
            break
        if should_stop():
            stats["stop_reason"] = "cancelled"
            break
        if time.monotonic() > deadline:
            stats["stop_reason"] = "max_duration"
            break
        try:
            validate_url(target, preset)
        except PolicyViolation:
            stats["skipped_scope"] += 1
            cache[canonical] = {}
            report(target, "skipped", reason="outside the preset scope")
            continue
        started = time.perf_counter()
        try:
            response = get(target)
        except PolicyViolation as error:
            message = str(error)
            if "robots.txt disallows" in message:
                stats["skipped_robots"] += 1
                cache[canonical] = {}
                report(target, "skipped", reason="robots.txt disallows it")
                continue
            if "outside" in message:  # a redirect left the preset's scope
                stats["skipped_scope"] += 1
                cache[canonical] = {}
                report(target, "skipped", reason="redirected outside the preset scope")
                continue
            stats["stop_reason"] = message.removeprefix("Collection stopped: ")
            warnings.append(f"Stopped following detail pages: {stats['stop_reason']}")
            report(target, "stopped", reason=stats["stop_reason"])
            break
        except httpx.HTTPStatusError as error:
            stats["failed"] += 1
            cache[canonical] = {"detail.url": target, "detail.status": error.response.status_code}
            record.update({k: v for k, v in cache[canonical].items() if k not in record})
            report(target, "failed", reason=f"HTTP {error.response.status_code}")
            continue
        except httpx.HTTPError as error:
            stats["failed"] += 1
            consecutive_errors += 1
            report(target, "failed", reason=type(error).__name__)
            if consecutive_errors >= 5:
                stats["stop_reason"] = "network_errors"
                warnings.append("Stopped following detail pages after 5 network errors in a row")
                break
            continue
        fetch_ms = round((time.perf_counter() - started) * 1000)
        consecutive_errors = 0
        content_type = response.headers.get("content-type", "")
        found: dict[str, object] = {"detail.url": target, "detail.status": response.status_code}
        final = str(response.url)
        if final and final != target:
            found["detail.final_url"] = final
        parse_started = time.perf_counter()
        if "html" in content_type or not content_type:
            found.update(detail_page_details(response.text, final or target, region, str(record.get("schema_type") or "") or None, preset=preset))
        else:
            found["detail.content_type"] = content_type.split(";")[0].strip()
        parse_ms = round((time.perf_counter() - parse_started) * 1000)
        found["detail.retrieved_at"] = datetime.now(timezone.utc).isoformat()
        cache[canonical] = found
        record.update({k: v for k, v in found.items() if k not in record})
        stats["fetched"] += 1
        stats["fetch_ms"] += fetch_ms
        stats["parse_ms"] += parse_ms
        report(target, "done", fields=len(found), fetch_ms=fetch_ms, parse_ms=parse_ms)
    if stats["skipped_scope"]:
        warnings.append(f"{stats['skipped_scope']} detail link(s) are outside the preset scope and were not followed")
    if stats["skipped_robots"]:
        warnings.append(f"{stats['skipped_robots']} detail page(s) are disallowed by robots.txt and were not followed")
    if stats["stop_reason"] == "max_detail_pages":
        warnings.append(f"Detail pages were followed for the first {limit} records (the detail page cap)")
    elif stats["stop_reason"] == "max_duration":
        warnings.append(f"Stopped following detail pages at the time budget after {stats['fetched']} page(s); raise details.follow.max_duration_seconds to read more")
    return stats


def follow_with_policy_client(
    records: list[dict],
    preset: dict,
    *,
    contact: dict | None = None,
    cache_dir=None,
    purpose: str | None = None,
    include_local_signals: bool = False,
    limit: int | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    on_page: Callable[[dict], None] = lambda event: None,
    warnings: list[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict, list[dict]]:
    """Detail-page following for callers without an open collection client (the Scrapy engine's parent and
    Scrape Studio staging). Uses the same client, signal checks, politeness scheduler, and fetch rules as the
    httpx engine. Returns (follow statistics, host signal summaries)."""
    from .extraction import validate_url
    from .fetch import fetch, make_client, scheduler_for
    from .signals import SignalChecker

    policy = preset.get("policy") or {}
    with make_client(preset, contact, None, cache_dir) as client:
        scheduler = scheduler_for(preset)
        checker = SignalChecker(client, purpose or policy.get("purpose") or "internal_analysis", policy.get("robots_policy", "respect"), include_local_signals, scheduler)

        def get(target: str):
            validate_url(target, preset)
            checker.check_url(target)
            response = fetch(client, target, preset, validate_url, scheduler, sleep=sleep)
            checker.check_response(target, response, response.text[:100_000] if "html" in response.headers.get("content-type", "") else None)
            return response

        stats = follow_detail_pages(records, preset, get, limit=limit, should_stop=should_stop, on_page=on_page, warnings=warnings)
        return stats, checker.summary()


# --- summaries ---------------------------------------------------------------------------------------

def summarize(records: list[dict], level: str, follow: dict | None = None, top: int = 24) -> dict:
    """What the detail pass added across a result: counts per group and the coverage of the most common keys."""
    groups: dict[str, set[str]] = {"value": set(), "item": set(), "page": set(), "detail": set()}
    counts: dict[str, int] = {}
    enriched = 0
    for record in records:
        found = False
        for key in record:
            group = detail_group(key, record)
            if group and record.get(key) not in (None, ""):
                groups[group].add(key)
                counts[key] = counts.get(key, 0) + 1
                found = True
        enriched += found
    total = len(records) or 1
    common = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top]
    return {
        "level": level,
        "records_enriched": enriched,
        "fields_added": sum(len(keys) for keys in groups.values()),
        "groups": {group: len(keys) for group, keys in groups.items()},
        "coverage": {key: round(count / total, 3) for key, count in common},
        "detail_pages": follow,
    }
