"""Value cleanup for one dataset, using nothing but the dataset itself.

``scan`` reports what can be fixed: formats to standardize per column, invalid and placeholder values, junk rows
(empty, exact duplicates, test entries), and variant spellings of the same value grouped the way OpenRefine's
key-collision clustering does (fingerprint, character n-gram fingerprint, and company names without legal
suffixes). ``apply`` turns the choices a person made into cleaned rows plus a change summary. Both are pure:
rows in, report or rows out.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
import phonenumbers

CLEANUP_VERSION = "1.0.0"

# Values people type when there is no value. Compared after trimming and casefolding.
PLACEHOLDERS = frozenset({
    "n/a", "na", "n.a.", "none", "null", "nil", "nan", "#n/a", "#value!", "#ref!", "#div/0!", "#name?", "#null!", "#num!",
    "-", "--", "---", "?", "??", "unknown", "not available", "not applicable", "tbd", "undefined", "blank", "empty",
})
_TEST_WORDS = frozenset({"test", "testing", "tester", "asdf", "asdfgh", "qwerty", "dummy", "sample", "lorem", "ipsum", "xxx", "zzz", "foo", "bar", "abc", "aaa"})
_PLACEHOLDER_EMAIL_LOCALS = frozenset({
    "noemail", "no-email", "no.email", "none", "na", "nobody", "noone", "no", "test", "asdf", "fake", "email",
    "donotreply", "do-not-reply", "noreply", "no-reply", "unknown",
})
_EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}$")
# Frequent misspellings of the largest mailbox domains.
_DOMAIN_TYPOS = {
    "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmal.com": "gmail.com", "gmail.co": "gmail.com", "gmail.con": "gmail.com",
    "gmaill.com": "gmail.com", "gnail.com": "gmail.com", "gamil.com": "gmail.com", "yaho.com": "yahoo.com", "yahooo.com": "yahoo.com",
    "yahoo.co": "yahoo.com", "yahoo.con": "yahoo.com", "hotmial.com": "hotmail.com", "hotmal.com": "hotmail.com", "hotmail.co": "hotmail.com",
    "hotmail.con": "hotmail.com", "outlok.com": "outlook.com", "outloo.com": "outlook.com", "outlook.co": "outlook.com", "iclod.com": "icloud.com",
    "icloud.co": "icloud.com", "aol.co": "aol.com", "comcast.ner": "comcast.net",
}
_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR", "guam": "GU",
    "virgin islands": "VI", "american samoa": "AS", "northern mariana islands": "MP",
}
_US_CODES = frozenset(_US_STATES.values())
# USPS street suffixes and unit designators (Publication 28), for the common spellings.
_STREET_SUFFIXES = {
    "street": "St", "st": "St", "str": "St", "avenue": "Ave", "ave": "Ave", "av": "Ave", "road": "Rd", "rd": "Rd", "drive": "Dr",
    "dr": "Dr", "lane": "Ln", "ln": "Ln", "boulevard": "Blvd", "blvd": "Blvd", "court": "Ct", "ct": "Ct", "place": "Pl", "pl": "Pl",
    "circle": "Cir", "cir": "Cir", "highway": "Hwy", "hwy": "Hwy", "parkway": "Pkwy", "pkwy": "Pkwy", "terrace": "Ter", "ter": "Ter",
    "trail": "Trl", "trl": "Trl", "square": "Sq", "sq": "Sq", "way": "Way", "loop": "Loop", "expressway": "Expy", "freeway": "Fwy",
    "crossing": "Xing", "point": "Pt", "plaza": "Plz", "mountain": "Mtn", "heights": "Hts",
}
_UNITS = {"apartment": "Apt", "apt": "Apt", "suite": "Ste", "ste": "Ste", "unit": "Unit", "building": "Bldg", "bldg": "Bldg", "floor": "Fl", "fl": "Fl", "room": "Rm", "rm": "Rm"}
_NAME_PARTICLES = frozenset({"de", "del", "della", "la", "le", "van", "von", "der", "den", "da", "di", "du", "dos", "das", "bin", "ibn"})
_NAME_SUFFIXES = {"jr": "Jr.", "sr": "Sr.", "ii": "II", "iii": "III", "iv": "IV", "v": "V", "phd": "PhD", "md": "MD", "esq": "Esq."}
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "gmbh", "ag", "sa", "sas",
    "srl", "bv", "nv", "pty", "pllc", "pc", "the",
})
_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏  ﻿]")
_CATEGORY_HINTS = re.compile(r"compan|organi[sz]ation|employer|business|firm|vendor|supplier|brand|categor|status|type|source|industry|department|title|city|county|state|country|region|segment|tag|group", re.I)

_ROLE_LABELS = {
    "name": "Names", "first_name": "First names", "last_name": "Last names", "phone": "Phone numbers", "email": "Email addresses",
    "postal_code": "ZIP / postal codes", "region": "States or regions", "city": "Cities", "county": "Counties", "address": "Addresses",
    "mailing_address": "Mailing addresses", "street": "Streets", "url": "Web addresses", "identifier": "IDs",
}


# --- small helpers ----------------------------------------------------------------------------------------------

def tidy(value: object) -> str:
    """Text without control characters, with non-breaking spaces as spaces and runs of whitespace collapsed."""
    if value is None:
        return ""
    text = _CONTROL.sub("", unicodedata.normalize("NFC", str(value)).replace(" ", " "))
    return " ".join(text.split())


def is_placeholder(value: str) -> bool:
    return value.strip().casefold() in PLACEHOLDERS


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def fingerprint(value: str) -> str:
    """OpenRefine fingerprint: case, accents, punctuation, and word order do not matter."""
    words = re.sub(r"[^\w\s]", "", _ascii(value).casefold()).split()
    return " ".join(sorted(set(words)))


def ngram_fingerprint(value: str, n: int = 2) -> str:
    """OpenRefine n-gram fingerprint: also ignores spacing ("Main Street" and "MainStreet")."""
    letters = re.sub(r"[\W_]", "", _ascii(value).casefold())
    return "".join(sorted({letters[i:i + n] for i in range(max(len(letters) - n + 1, 1))}))


def company_key(value: str) -> str:
    """A company name without legal-form words, so "Acme, Inc." and "ACME Incorporated" share a key."""
    words = [word for word in re.sub(r"[^\w\s&]", " ", _ascii(value).casefold()).split() if word not in _LEGAL_SUFFIXES]
    return " ".join(sorted(set(words)))


def _mixed_case(value: str) -> bool:
    letters = [ch for ch in value if ch.isalpha()]
    return any(ch.isupper() for ch in letters) and any(ch.islower() for ch in letters)


def _title_word(word: str, first: bool = True) -> str:
    lower = word.casefold()
    if lower in _NAME_SUFFIXES:
        return _NAME_SUFFIXES[lower]
    if not first and lower in _NAME_PARTICLES:
        return lower
    if "-" in word:
        return "-".join(_title_word(part) for part in word.split("-"))
    if re.fullmatch(r"o'\w+", lower):
        return "O'" + lower[2:].capitalize()
    if re.fullmatch(r"mc\w{2,}", lower):
        return "Mc" + lower[2:].capitalize()
    if re.fullmatch(r"[a-z]\.?", lower):
        return lower.upper()
    return lower.capitalize()


def title_case(value: str) -> str:
    """Title case for text typed all upper or all lower; mixed case ("DeShawn", "McAllister") is the writer's choice."""
    if not value or _mixed_case(value):
        return value
    return " ".join(_title_word(word, index == 0) for index, word in enumerate(value.split()))


# --- per-role standardization ------------------------------------------------------------------------------------
# Each returns (value, problem) where problem is None, "invalid", or "placeholder". The value is the standardized
# text; for a problem it is the tidied original so nothing is lost unless the person chooses to clear it.

def person_name(value: str) -> tuple[str, str | None]:
    text = tidy(value)
    if "," in text:
        before, after = (part.strip() for part in text.split(",", 1))
        after_words = after.split()
        if before and after_words and all(word.strip(".").casefold() in _NAME_SUFFIXES for word in after_words):
            text = f"{before} {after}"  # "John Smith, Jr." -> "John Smith Jr."
        elif before and after and len(before.split()) <= 3:
            suffixes = [word for word in after_words if word.strip(".").casefold() in _NAME_SUFFIXES]
            given = [word for word in after_words if word not in suffixes]
            text = " ".join([*given, before, *suffixes])  # "Smith, John Jr." -> "John Smith Jr."
    if any(word.casefold() in _TEST_WORDS for word in text.split()) and all(word.casefold() in _TEST_WORDS | {"user", "name", "person"} for word in text.split()):
        return text, "placeholder"
    return title_case(text), None


def plain_name(value: str) -> tuple[str, str | None]:
    """Company, product, or place names: spacing only; their capitalization (IBM, eBay) is kept."""
    return tidy(value), None


_PLAIN_US_PHONE = re.compile(r"\+?[\d\s().-]+")


def phone_number(value: str, region: str = "US") -> tuple[str, str | None]:
    text = tidy(value)
    digits = re.sub(r"\D", "", text)
    if not digits:
        return text, "invalid"
    if len(set(digits[-10:])) == 1 or digits[-10:] in ("1234567890", "0123456789", "0987654321") or digits[-7:] == "0000000":
        return text, "placeholder"
    if region == "US" and _PLAIN_US_PHONE.fullmatch(text) and (len(digits) == 10 or (len(digits) == 11 and digits[0] == "1")):
        national = digits[-10:]  # the common case, formatted exactly as the full parser would
        return f"({national[:3]}) {national[3:6]}-{national[6:]}", None
    numbers = [match.number for match in phonenumbers.PhoneNumberMatcher(text, region, leniency=phonenumbers.Leniency.POSSIBLE)]
    if not numbers:
        try:
            numbers = [phonenumbers.parse(text, region)]
        except phonenumbers.NumberParseException:
            return text, "invalid"
    formatted = []
    for number in numbers:
        if not phonenumbers.is_possible_number(number):
            return text, "invalid"
        same_country = phonenumbers.region_code_for_number(number) == region or number.country_code == phonenumbers.country_code_for_region(region)
        formatted.append(phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.NATIONAL if same_country else phonenumbers.PhoneNumberFormat.INTERNATIONAL))
    return "; ".join(dict.fromkeys(formatted)), None


def email_address(value: str) -> tuple[str, str | None]:
    text = tidy(value).strip("<>").removeprefix("mailto:").removeprefix("MAILTO:").strip().casefold()
    text = text.replace(" ", "").rstrip(".,;")
    if "@" in text:
        local, _, domain = text.rpartition("@")
        domain = _DOMAIN_TYPOS.get(domain, domain)
        text = f"{local}@{domain}"
        if local in _PLACEHOLDER_EMAIL_LOCALS or text in ("test@test.com", "email@email.com", "a@a.com", "x@x.com"):
            return text, "placeholder"
    if not _EMAIL_RE.match(text) or ".." in text:
        return tidy(value), "invalid"
    return text, None


def postal_code(value: str, region: str = "US") -> tuple[str, str | None]:
    text = tidy(value).upper()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]  # 2152.0: a ZIP code stored as a number
    if region == "US" and re.fullmatch(r"[\d\s-]+", text):
        digits = re.sub(r"\D", "", text)
        if len(digits) in (3, 4):
            return digits.zfill(5), None  # Excel drops leading zeros: 2152 -> 02152
        if len(digits) in (7, 8):
            digits = digits.zfill(9)
        if len(digits) == 5:
            return digits, None
        if len(digits) == 9:
            return f"{digits[:5]}-{digits[5:]}", None
        return text, "invalid"
    if re.fullmatch(r"[A-Z]\d[A-Z]\s?\d[A-Z]\d", text):  # Canada
        compact = text.replace(" ", "")
        return f"{compact[:3]} {compact[3:]}", None
    uk = re.fullmatch(r"([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})", text)
    if uk:
        return f"{uk.group(1)} {uk.group(2)}", None
    return text, None


def state_or_region(value: str, region: str = "US") -> tuple[str, str | None]:
    text = tidy(value)
    if region == "US":
        key = text.casefold().replace(".", "").strip()
        if key in _US_STATES:
            return _US_STATES[key], None
        if key.upper() in _US_CODES:
            return key.upper(), None
    return title_case(text), None


def street_address(value: str) -> tuple[str, str | None]:
    text = title_case(tidy(value))
    words = text.split()
    out = []
    for index, word in enumerate(words):
        bare = word.casefold().rstrip(".,")
        tail = word[len(word.rstrip(".,")):].replace(".", "")
        following = words[index + 1].casefold().rstrip(".,") if index + 1 < len(words) else ""
        if bare in _UNITS and (following[:1].isdigit() or len(following) <= 4):
            out.append(_UNITS[bare] + tail)
        elif bare in _STREET_SUFFIXES and index > 0 and (index == len(words) - 1 or following in _UNITS or following.startswith("#") or tail):
            out.append(_STREET_SUFFIXES[bare] + tail)
        else:
            out.append(word)
    return " ".join(out), None


def web_address(value: str) -> tuple[str, str | None]:
    text = tidy(value)
    if not text or " " in text:
        return text, "invalid" if text else None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        if not re.match(r"^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(/.*)?$", text, re.I):
            return text, "invalid"
        text = "https://" + text
    scheme, _, rest = text.partition("://")
    host, slash, path = rest.partition("/")
    return f"{scheme.casefold()}://{host.casefold()}{slash}{path}", None


def identifier(value: str) -> tuple[str, str | None]:
    text = tidy(value)
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".")[0], None  # 12345.0: an ID stored as a number
    if re.fullmatch(r"\d(\.\d+)?E\+\d+", text, re.I):
        return text, "invalid"  # 1.23457E+11: Excel shortened a long number; the digits are gone
    return text, None


def standardize(value: object, role: str, region: str = "US", person: bool = True) -> tuple[str, str | None]:
    """The standardized form of one value for a column role, and whether it is invalid or a placeholder."""
    text = tidy(value)
    if not text:
        return "", None
    if is_placeholder(text):
        return text, "placeholder"
    if role in ("name", "first_name", "last_name"):
        return person_name(text) if person else plain_name(text)
    if role == "phone":
        return phone_number(text, region)
    if role == "email":
        return email_address(text)
    if role == "postal_code":
        return postal_code(text, region)
    if role == "region":
        return state_or_region(text, region)
    if role in ("city", "county", "neighborhood", "district", "country"):
        return title_case(text), None
    if role in ("address", "mailing_address", "street"):
        return street_address(text)
    if role == "url":
        return web_address(text)
    if role == "identifier":
        return identifier(text)
    return text, None


# --- scanning ----------------------------------------------------------------------------------------------------

def _row_signature(row: dict[str, object], columns: list[str]) -> tuple:
    return tuple(tidy(row.get(column)).casefold() for column in columns)


def _is_empty_row(row: dict[str, object], columns: list[str]) -> bool:
    return all(not tidy(row.get(column)) or is_placeholder(tidy(row.get(column))) for column in columns)


def _is_test_row(row: dict[str, object], roles: dict[str, str], person: bool) -> bool:
    for column, role in roles.items():
        value = tidy(row.get(column))
        if not value:
            continue
        if role in ("name", "first_name", "last_name") and person and person_name(value)[1] == "placeholder":
            return True
        if role == "email" and email_address(value)[1] == "placeholder" and email_address(value)[0].split("@")[0] in ("test", "asdf", "fake"):
            return True
    return False


def _cluster_columns(columns: list[str], roles: dict[str, str], rows: list[dict[str, object]], person: bool) -> list[str]:
    chosen = []
    for column in columns:
        role = roles.get(column, "other")
        if role in ("phone", "email", "identifier", "url", "postal_code", "first_name", "last_name", "address", "mailing_address", "street", "latitude", "longitude", "house_number", "unit", "ignore"):
            continue
        if role == "name" and person:
            continue  # people's names are not categories; spelling differences there are matching's job
        values = [tidy(row.get(column)) for row in rows]
        filled = [value for value in values if value]
        if not filled or all(re.fullmatch(r"[\d.,\s$-]+", value) for value in filled[:50]):
            continue
        if role != "other" or _CATEGORY_HINTS.search(column) or len(set(filled)) <= 0.7 * len(filled):
            chosen.append(column)
    return chosen


def _preferred(variants: list[tuple[str, int]]) -> str:
    """The spelling to keep: the most common, preferring properly capitalized text over ALL CAPS or all lower."""
    return max(variants, key=lambda item: (item[1], _mixed_case(item[0]), item[0] == item[0].strip(), -len(item[0])))[0]


def value_clusters(rows: list[dict[str, object]], column: str, company: bool = False, limit: int = 100) -> list[dict]:
    """Groups of different spellings that share a fingerprint key, most rows first."""
    counts = Counter(tidy(row.get(column)) for row in rows)
    counts.pop("", None)
    groups: dict[str, set[str]] = defaultdict(set)
    methods: dict[str, str] = {}
    for method, key_of in (("company", company_key) if company else ("fingerprint", fingerprint), ("ngram", ngram_fingerprint)):
        for value in counts:
            if is_placeholder(value):
                continue
            key = key_of(value)
            if key:
                groups[f"{method}:{key}"].add(value)
                methods.setdefault(f"{method}:{key}", method)
    seen: list[frozenset[str]] = []
    clusters = []
    for key, values in groups.items():
        if len(values) < 2 or any(frozenset(values) <= earlier for earlier in seen):
            continue
        seen.append(frozenset(values))
        variants = sorted(((value, counts[value]) for value in values), key=lambda item: -item[1])
        clusters.append({
            "column": column, "method": methods[key], "rows": sum(count for _, count in variants),
            "values": [{"value": value, "rows": count} for value, count in variants], "suggested": _preferred(variants),
        })
    clusters.sort(key=lambda cluster: (-cluster["rows"], cluster["suggested"]))
    return clusters[:limit]


def scan(rows: list[dict[str, object]], roles: dict[str, str], region: str = "US", person: bool = True, examples: int = 4) -> dict:
    """What cleanup would change in these rows. ``roles`` maps column -> mapping role ("other" for plain text)."""
    columns = list(dict.fromkeys(column for row in rows for column in row))
    report_columns = []
    for column in columns:
        role = roles.get(column, "other")
        if role == "ignore":
            continue
        filled = changed = 0
        problems: Counter[str] = Counter()
        change_examples: list[list[str]] = []
        problem_examples: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            raw = row.get(column)
            original = "" if raw is None else str(raw)
            if not tidy(original):
                continue
            filled += 1
            value, problem = standardize(original, role, region, person)
            if problem:
                problems[problem] += 1
                if len(problem_examples[problem]) < examples and value not in problem_examples[problem]:
                    problem_examples[problem].append(value)
            elif value != original:
                changed += 1
                if len(change_examples) < examples and [original, value] not in change_examples:
                    change_examples.append([original, value])
        if filled and (changed or problems):
            report_columns.append({
                "column": column, "role": role, "label": _ROLE_LABELS.get(role, column), "filled": filled, "changes": changed,
                "change_examples": change_examples, "invalid": problems["invalid"], "placeholders": problems["placeholder"],
                "problem_examples": {kind: values for kind, values in problem_examples.items()},
            })
    empty, test, duplicates = [], [], []
    seen: set[tuple] = set()
    for index, row in enumerate(rows):
        if _is_empty_row(row, columns):
            empty.append(index)
            continue
        if _is_test_row(row, roles, person):
            test.append(index)
        signature = _row_signature(row, columns)
        if signature in seen:
            duplicates.append(index)
        seen.add(signature)
    clusters = []
    for column in _cluster_columns(columns, roles, rows, person):
        company = roles.get(column) == "name" or bool(re.search(r"compan|organi[sz]ation|employer|business|firm|vendor|supplier|brand", column, re.I))
        for cluster in value_clusters(rows, column, company):
            # Spellings that standardizing the column already unifies (AUSTIN, Austin) need no separate choice.
            if len({standardize(item["value"], roles.get(column, "other"), region, person)[0] for item in cluster["values"]}) > 1:
                clusters.append(cluster)
    clusters.sort(key=lambda cluster: -cluster["rows"])
    return {
        "cleanup_version": CLEANUP_VERSION, "rows": len(rows), "columns": report_columns,
        "junk_rows": {
            "empty": len(empty), "exact_duplicates": len(duplicates), "test": len(test),
            "examples": {"test": [{column: tidy(rows[i].get(column)) for column in columns[:4]} for i in test[:examples]]},
        },
        "clusters": clusters[:200],
    }


# --- applying ----------------------------------------------------------------------------------------------------

def apply(rows: list[dict[str, object]], roles: dict[str, str], plan: dict, region: str = "US", person: bool = True) -> tuple[list[int], list[dict[str, object]], dict]:
    """Cleaned rows for a plan: {"standardize": [columns], "clear_invalid": [columns],
    "merge_values": [{"column", "values", "to"}], "drop_rows": ["empty", "exact_duplicates", "test"]}.

    Returns (indexes of kept input rows, cleaned rows, summary). Input rows are never modified."""
    columns = list(dict.fromkeys(column for row in rows for column in row))
    standardize_columns = set(plan.get("standardize") or [])
    clear_columns = set(plan.get("clear_invalid") or [])
    drops = set(plan.get("drop_rows") or [])
    merges: dict[str, dict[str, str]] = defaultdict(dict)
    for merge in plan.get("merge_values") or []:
        target = tidy(merge.get("to"))
        for value in merge.get("values") or []:
            merges[merge["column"]][tidy(value)] = target
    summary = {"changed": Counter(), "cleared": Counter(), "merged": Counter(), "dropped": Counter()}
    kept: list[int] = []
    cleaned: list[dict[str, object]] = []
    seen: set[tuple] = set()
    for index, row in enumerate(rows):
        if "empty" in drops and _is_empty_row(row, columns):
            summary["dropped"]["empty"] += 1
            continue
        if "test" in drops and _is_test_row(row, roles, person):
            summary["dropped"]["test"] += 1
            continue
        out: dict[str, object] = {}
        row_counts = {"changed": Counter(), "cleared": Counter(), "merged": Counter()}  # counted only if the row is kept
        for column in columns:
            raw = row.get(column)
            original = "" if raw is None else str(raw)
            value = original
            role = roles.get(column, "other")
            if tidy(original) and role != "ignore":
                standardized, problem = standardize(original, role, region, person)
                if problem and column in clear_columns:
                    value = ""
                    row_counts["cleared"][column] += 1
                elif column in standardize_columns and not problem:
                    value = standardized
                elif column in standardize_columns:
                    value = tidy(original)
                target = merges.get(column, {}).get(tidy(original), merges.get(column, {}).get(standardized))
                if target is not None and value != "":
                    value = target
                    row_counts["merged"][column] += 1
                elif value != original and value != "":
                    row_counts["changed"][column] += 1
            out[column] = value if raw is not None or value != "" else raw
        if "exact_duplicates" in drops:
            # As typed (ignoring case and spacing), the same rule the scan counts; rows that only become
            # identical after standardizing are left for duplicate matching to merge.
            signature = _row_signature(row, columns)
            if signature in seen:
                summary["dropped"]["exact_duplicates"] += 1
                continue
            seen.add(signature)
        for key, counter in row_counts.items():
            summary[key].update(counter)
        kept.append(index)
        cleaned.append(out)
    return kept, cleaned, {key: dict(counter) for key, counter in summary.items()}
