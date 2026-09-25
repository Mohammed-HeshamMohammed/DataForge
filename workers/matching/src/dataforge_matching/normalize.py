"""Role-specific, versioned normalization. Raw values are never modified; these return derived values."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import phonenumbers

NORMALIZATION_VERSION = "1.1.0"

# Roles whose values are interchangeable across columns (compared as sets).
SET_ROLES = frozenset({"phone", "email"})
# Roles compared like-for-like; at most one column per role.
POSITIONAL_ROLES = frozenset(
    {
        "address", "mailing_address", "building_name", "house_number", "street", "unit", "po_box", "neighborhood",
        "district", "city", "county", "region", "country", "country_code", "postal_code", "latitude", "longitude",
        "name", "first_name", "last_name", "url",
    }
)
# Identifiers are namespaced by source column, so several identifier columns are allowed.
ALL_ROLES = SET_ROLES | POSITIONAL_ROLES | {"identifier", "other", "ignore"}
SENSITIVE_ROLES = frozenset({
    "phone", "email", "address", "mailing_address", "building_name", "house_number", "street", "unit", "po_box",
    "neighborhood", "district", "city", "county", "region", "country", "country_code", "postal_code", "latitude",
    "longitude", "name", "first_name", "last_name",
})

ADDRESS_TEXT_ROLES = frozenset({
    "building_name", "house_number", "street", "unit", "po_box", "neighborhood", "district", "city", "county",
    "region", "country", "country_code",
})

_SUFFIXES = {
    "street": "st", "str": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr", "lane": "ln",
    "boulevard": "blvd", "court": "ct", "place": "pl", "circle": "cir", "highway": "hwy", "parkway": "pkwy",
    "terrace": "ter", "trail": "trl", "square": "sq", "way": "way", "loop": "loop",
}
_DIRECTIONS = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}
_UNIT_RE = re.compile(r"\b(?:apt|apartment|unit|ste|suite|#)\s*([a-z0-9-]+)\b")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TRACKING_PARAMS = re.compile(r"^(utm_.*|ref|fbclid|gclid|mc_eid|mc_cid)$")


def text(value: object) -> str:
    if value is None:
        return ""
    folded = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(re.sub(r"[^\w\s#-]", " ", folded).split())


def phone(value: object, default_region: str | None = "US") -> str | None:
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return None
    try:
        parsed = phonenumbers.parse(raw, default_region)
        if phonenumbers.is_possible_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    if raw.startswith("+"):
        return "+" + digits
    if default_region == "US":
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
    # Preserve the conservative fallback for incomplete or ambiguous values.
    return digits


def email(value: object) -> str | None:
    candidate = str(value or "").strip().casefold()
    return candidate if _EMAIL_RE.match(candidate) else None


def postal_code(value: object) -> str | None:
    candidate = re.sub(r"\s+", "", str(value or "")).casefold()
    if not candidate:
        return None
    us = re.fullmatch(r"(\d{5})(?:-?\d{4})?", candidate)
    return us.group(1) if us else candidate


def address(value: object) -> dict[str, str] | None:
    normalized = text(value)
    if not normalized:
        return None
    unit_match = _UNIT_RE.search(normalized)
    unit = unit_match.group(1) if unit_match else ""
    if unit_match:
        normalized = (normalized[: unit_match.start()] + normalized[unit_match.end():]).strip()
    tokens = [_DIRECTIONS.get(token, _SUFFIXES.get(token, token)) for token in normalized.replace("#", " ").split()]
    house = tokens[0] if tokens and re.fullmatch(r"\d+[a-z]?", tokens[0]) else ""
    street_tokens = tokens[1:] if house else tokens
    return {"house_number": house, "street": " ".join(street_tokens), "unit": unit, "full": " ".join(tokens)}


def coordinate(value: object, minimum: float, maximum: float) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return round(result, 7) if minimum <= result <= maximum else None


def url(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    if not parsed.hostname:
        return None
    host = parsed.hostname.casefold().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query) if not _TRACKING_PARAMS.match(k)))
    return urlunparse(("", host, parsed.path.rstrip("/") or "/", "", query, ""))


def normalize_row(raw: dict[str, object], mapping: dict[str, str], default_region: str | None = "US") -> dict[str, object]:
    """Return role-keyed comparison values for one row. Missing values are simply absent (no evidence)."""
    out: dict[str, object] = {"phone": set(), "email": set(), "identifier": {}}
    first = last = ""
    for column, role in mapping.items():
        value = raw.get(column)
        if value in (None, ""):
            continue
        if role == "phone":
            if (normalized := phone(value, default_region)) is not None:
                out["phone"].add(normalized)
        elif role == "email":
            if (normalized := email(value)) is not None:
                out["email"].add(normalized)
        elif role == "identifier":
            if (normalized := str(value).strip().casefold()):
                out["identifier"][column] = normalized
        elif role in ("address", "mailing_address"):
            if (parsed := address(value)) is not None:
                out[role] = parsed
        elif role == "postal_code":
            if (normalized := postal_code(value)) is not None:
                out[role] = normalized
        elif role == "url":
            if (normalized := url(value)) is not None:
                out[role] = normalized
        elif role == "latitude":
            if (normalized := coordinate(value, -90, 90)) is not None:
                out[role] = normalized
        elif role == "longitude":
            if (normalized := coordinate(value, -180, 180)) is not None:
                out[role] = normalized
        elif role == "first_name":
            first = text(value)
        elif role == "last_name":
            last = text(value)
        elif role == "name" or role in ADDRESS_TEXT_ROLES:
            if (normalized := text(value)):
                out[role] = normalized
    # Separate address columns become the same parsed structure used by a complete address.
    # Explicit components also improve a parsed full address without altering the raw values.
    if out.get("address") or out.get("street"):
        parsed = out.get("address") or address(" ".join(part for part in (str(out.get("house_number", "")), str(out["street"]), str(out.get("unit", ""))) if part))
        if parsed:
            if out.get("house_number"):
                parsed["house_number"] = str(out["house_number"])
            if out.get("street"):
                parsed["street"] = str(out["street"])
            if out.get("unit"):
                parsed["unit"] = str(out["unit"])
            parsed["full"] = " ".join(part for part in (parsed["house_number"], parsed["street"], parsed["unit"]) if part)
            out["address"] = parsed
    if "name" not in out and (first or last):
        out["name"] = f"{first} {last}".strip()
    return out
