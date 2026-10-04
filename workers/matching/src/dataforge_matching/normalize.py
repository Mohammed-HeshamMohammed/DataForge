"""Role-specific, versioned normalization. Raw values are never modified; these return derived values."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import phonenumbers
from rapidfuzz.distance import JaroWinkler, Levenshtein

NORMALIZATION_VERSION = "1.2.0"

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
# Mailbox providers that deliver user+tag@ to user@; Gmail also ignores dots in the local part.
_PLUS_TAG_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com", "mac.com",
    "fastmail.com", "protonmail.com", "proton.me",
})
_NAME_TITLES = frozenset({"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "madam"})
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v", "phd", "md", "esq"})
_NAME_PARTICLES = frozenset({"de", "del", "della", "la", "le", "van", "von", "der", "den", "da", "di", "du", "dos", "das", "bin", "ibn", "al", "el", "st"})
# Common English given names and their nicknames. A name may belong to several groups (sam: samuel and samantha).
_GIVEN_NAME_GROUPS = (
    "abigail abby abbie gail", "albert al bert bertie", "alexander alex al xander sandy sasha", "alexandra alex lexi sandra sandy sasha",
    "alfred al alf fred freddie", "andrew andy drew", "anthony tony", "barbara barb barbie babs", "benjamin ben benny benji",
    "catherine katherine kathryn katharine cathy kathy kate katie kat kay kitty", "charles charlie chuck chas chaz", "christina chris tina chrissy",
    "christine chris tina chrissy", "christopher chris kit topher", "cynthia cindy", "daniel dan danny", "danielle dani", "david dave davey",
    "deborah debra deb debbie", "dennis denny", "donald don donnie", "dorothy dot dottie dolly", "douglas doug", "edward ed eddie ted ned",
    "elizabeth liz lizzie lizzy beth betty betsy eliza libby lisa", "eugene gene", "frances fran frannie", "francis frank fran",
    "franklin frank", "frederick fred freddie", "gabriel gabe", "gerald jerry gerry", "gregory greg", "harold hal harry",
    "henry hank harry hal", "isabella isabel bella izzy", "jacob jake jakey", "jacqueline jackie jacquie", "james jim jimmy jamie",
    "jeffrey geoffrey jeff geoff", "jennifer jen jenny jenn", "jessica jess jessie", "john jack johnny jon", "jonathan jon jonny nathan",
    "joseph joe joey", "joshua josh", "kenneth ken kenny", "kimberly kim kimmy", "lawrence laurence larry",
    "leonard leo len lenny", "margaret maggie meg peggy marge margie greta", "matthew matt matty", "michael mike mikey mick mickey",
    "mitchell mitch", "nathaniel nathan nate nat", "nicholas nick nicky", "pamela pam", "patricia pat patty trish tricia",
    "patrick pat paddy", "peter pete", "philip phillip phil", "raymond ray", "rebecca becky becca", "richard rick ricky rich dick richie",
    "robert bob bobby rob robbie bert", "ronald ron ronnie", "samantha sam sammy", "samuel sam sammy", "sandra sandy",
    "stephen steven steve stevie", "susan sue susie suzy", "theodore ted teddy theo", "thomas tom tommy", "timothy tim timmy",
    "victoria vicky vickie tori", "vincent vince vinny", "walter walt wally", "william bill billy will willy liam", "zachary zach zack",
)
_NICKNAMES: dict[str, set[int]] = {}
for _group_index, _group in enumerate(_GIVEN_NAME_GROUPS):
    for _name in _group.split():
        _NICKNAMES.setdefault(_name, set()).add(_group_index)


def text(value: object) -> str:
    if value is None:
        return ""
    return _text(str(value))


@lru_cache(maxsize=65536)
def _text(value: str) -> str:
    # Cities, states, and names repeat across rows, so normalized text is cached.
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w\s#-]", " ", folded).split())


_PLAIN_PHONE = re.compile(r"[\d\s().+-]+")


def phone(value: object, default_region: str | None = "US") -> str | None:
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return None
    # Plain US numbers need no full parse: 10 digits, or 11 starting with the country code 1 (with or without "+").
    if default_region == "US" and _PLAIN_PHONE.fullmatch(raw) and raw.count("+") <= int(raw.startswith("+")):
        if len(digits) == 10 and not raw.startswith("+"):
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
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
    """The mailbox an address delivers to: case-insensitive, without +tags at providers that ignore them,
    and without dots in Gmail local parts (jane.doe@gmail.com and janedoe@gmail.com are one inbox)."""
    candidate = str(value or "").strip().casefold()
    if not _EMAIL_RE.match(candidate):
        return None
    local, _, domain = candidate.rpartition("@")
    if domain in _PLUS_TAG_DOMAINS and "+" in local:
        local = local.split("+", 1)[0] or local
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", "") or local, "gmail.com"
    return f"{local}@{domain}"


def person_name(value: object) -> tuple[tuple[str, ...], str] | None:
    """(given names, surname) of a person's name, accepting "Last, First"; None for a single word."""
    raw = unicodedata.normalize("NFKC", str(value or ""))
    tokens = text(raw).split()
    if "," in raw:
        before, after = raw.split(",", 1)
        after_tokens = [token for token in text(after).split() if token not in _NAME_SUFFIXES]
        if text(before) and after_tokens:
            tokens = after_tokens + text(before).split()
    # Titles lead and suffixes trail ("V." at the start is an initial, at the end it is "the fifth").
    while tokens and tokens[0] in _NAME_TITLES:
        tokens = tokens[1:]
    while len(tokens) > 2 and tokens[-1] in _NAME_SUFFIXES:
        tokens = tokens[:-1]
    if len(tokens) < 2:
        return None
    given = tuple(token for token in tokens[:-1] if token not in _NAME_PARTICLES)
    return (given, tokens[-1]) if given else None


def name_initials(given: str) -> set[str]:
    """Initials a person with this given name may use: its own, and those of its nicknames (Robert: R or B for Bob)."""
    initials = {given[0]} if given else set()
    for group in _NICKNAMES.get(given, ()):
        initials.update(name[0] for name in _GIVEN_NAME_GROUPS[group].split())
    return initials


def soundex(word: str) -> str:
    """American Soundex, so surnames that sound alike (Harris, Haris) share a candidate group."""
    letters = [ch for ch in word.casefold() if "a" <= ch <= "z"]
    if not letters:
        return ""
    codes = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"), "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}
    out, previous = [letters[0].upper()], codes.get(letters[0], "")
    for ch in letters[1:]:
        code = codes.get(ch, "")
        if code and code != previous:
            out.append(code)
        if ch not in "hw":
            previous = code
    return ("".join(out) + "000")[:4]


def _given_token_relation(a: str, b: str) -> str:
    if a == b:
        return "same"
    if len(a) == 1 and len(b) == 1:
        return "initial" if a == b else "different"
    if len(a) == 1 or len(b) == 1:
        initial, full = (a, b) if len(a) == 1 else (b, a)
        return "initial" if initial in name_initials(full) else "different"
    if _NICKNAMES.get(a, set()) & _NICKNAMES.get(b, set()):
        return "nickname"
    short, long = sorted((a, b), key=len)
    if len(short) >= 3 and long.startswith(short):
        return "nickname"
    if min(len(a), len(b)) >= 4 and (JaroWinkler.similarity(a, b) >= 0.92 or (min(len(a), len(b)) >= 5 and Levenshtein.distance(a, b) <= 1)):
        return "similar"
    return "different"


_GIVEN_RANK = {"same": 4, "nickname": 3, "similar": 2, "initial": 1, "different": 0}


def given_relation(a: tuple[str, ...], b: tuple[str, ...]) -> str:
    """How two people's given names relate: same, nickname, similar (typo), initial, or different.
    Any pair of given names counts, so someone known by a middle name still matches."""
    best = "different"
    for left in a:
        for right in b:
            relation = _given_token_relation(left, right)
            if _GIVEN_RANK[relation] > _GIVEN_RANK[best]:
                best = relation
    return best


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
    # Given names and surname, used when the records are people.
    if first and last:
        given = tuple(token for token in first.split() if token not in _NAME_PARTICLES)
        if given:
            out["person"] = (given, last.split()[-1])
    elif out.get("name") and (parsed := person_name(next((raw[c] for c, r in mapping.items() if r == "name" and raw.get(c)), out["name"]))):
        out["person"] = parsed
    return out
