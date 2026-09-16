"""Field normalizers used as preset transforms. They make scraped values comparable before matching.

All run offline: the public-suffix list and phone metadata ship inside their packages.
"""

from __future__ import annotations

from functools import lru_cache

_CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "C$": "CAD", "A$": "AUD", "CHF": "CHF"}


def normalize_phone(value: object, region: str = "US") -> str | None:
    import phonenumbers

    try:
        number = phonenumbers.parse(str(value), region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def parse_price(value: object) -> tuple[str | None, str | None]:
    from price_parser import Price

    price = Price.fromstring(str(value))
    currency = price.currency
    return (str(price.amount) if price.amount is not None else None, _CURRENCY_SYMBOLS.get(currency or "", currency))


def parse_date(value: object, languages: list[str] | None = None) -> str | None:
    import dateparser

    parsed = dateparser.parse(str(value), languages=languages, settings={"RETURN_AS_TIMEZONE_AWARE": False, "PREFER_DAY_OF_MONTH": "first"})
    if parsed is None:
        return None
    return parsed.date().isoformat() if (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0) else parsed.isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def _tld_extractor():
    import tldextract

    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)  # bundled snapshot; never fetches


def registrable_domain(value: object) -> str | None:
    result = _tld_extractor()(str(value))
    domain = getattr(result, "top_domain_under_public_suffix", None) or result.registered_domain
    return domain.lower() or None


_ADDRESS_PARTS = {
    "street": ("AddressNumberPrefix", "AddressNumber", "AddressNumberSuffix", "StreetNamePreModifier", "StreetNamePreDirectional", "StreetNamePreType", "StreetName", "StreetNamePostType", "StreetNamePostDirectional"),
    "unit": ("OccupancyType", "OccupancyIdentifier", "SubaddressType", "SubaddressIdentifier"),
    "city": ("PlaceName",),
    "state": ("StateName",),
    "postal_code": ("ZipCode",),
}


def parse_us_address(value: object) -> dict[str, str]:
    import usaddress

    try:
        tagged, _ = usaddress.tag(str(value))
    except usaddress.RepeatedLabelError:
        return {}
    return {part: " ".join(tagged[label] for label in labels if label in tagged).strip(", ") for part, labels in _ADDRESS_PARTS.items() if any(label in tagged for label in labels)}


def detect_language(value: object) -> str | None:
    """Optional: lingua is an add-on (large models). Returns an ISO 639-1 code or None when unavailable."""
    try:
        from lingua import LanguageDetectorBuilder
    except ImportError:
        return None
    detector = _lingua(LanguageDetectorBuilder)
    language = detector.detect_language_of(str(value))
    return language.iso_code_639_1.name.lower() if language else None


@lru_cache(maxsize=1)
def _lingua(builder):
    return builder.from_all_languages().with_low_accuracy_mode().build()


def transforms() -> dict:
    """Preset transform registry entries. Each receives the value plus runtime context keywords."""

    def region(preset: dict) -> str:
        return str((preset.get("normalization") or {}).get("default_region", "US"))

    return {
        "normalize_phone": lambda v, preset, **_: normalize_phone(v, region(preset)),
        "parse_price": lambda v, **_: parse_price(v)[0],
        "price_currency": lambda v, **_: parse_price(v)[1],
        "parse_date": lambda v, **_: parse_date(v),
        "registrable_domain": lambda v, **_: registrable_domain(v),
        "address_street": lambda v, **_: parse_us_address(v).get("street"),
        "address_unit": lambda v, **_: parse_us_address(v).get("unit"),
        "address_city": lambda v, **_: parse_us_address(v).get("city"),
        "address_state": lambda v, **_: parse_us_address(v).get("state"),
        "address_postal_code": lambda v, **_: parse_us_address(v).get("postal_code"),
        "detect_language": lambda v, **_: detect_language(v),
    }
