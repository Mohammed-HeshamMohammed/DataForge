"""Sanitized fixtures from captured pages (Track J): turn a WARC-captured response into a health-check fixture
that is safe to keep in a project or ship in a preset package.

Removed: executable scripts (JSON-LD is kept, it is data), comments, iframes, form field values, CSRF and
session tokens in meta tags and URLs, and personal contact details in text (emails, phone numbers, long
digit runs). The result is still reviewed by a person before a preset version uses it.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment

from .suggest import _DIGITS, _EMAIL, _PHONE

_TOKEN_NAME = re.compile(r"(csrf|xsrf|token|nonce|session|sessid|sid|auth|signature|sig|key|apikey|api_key|access_token|password)", re.I)
_URL_ATTRIBUTES = ("href", "src", "action", "data-src", "srcset", "content")


def _clean_url(value: str) -> tuple[str, int]:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value, 0
    if not parts.query:
        return value, 0
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    cleaned = [(k, "REDACTED" if _TOKEN_NAME.search(k) else v) for k, v in pairs]
    changed = sum(1 for (k, v), (_, w) in zip(pairs, cleaned) if v != w)
    return (urlunsplit(parts._replace(query=urlencode(cleaned))) if changed else value), changed


def sanitize_html(html: str) -> tuple[str, dict[str, int]]:
    counts = {"scripts": 0, "comments": 0, "frames": 0, "form_values": 0, "tokens": 0, "contact_details": 0}
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        if (script.get("type") or "").lower() != "application/ld+json":
            script.decompose()
            counts["scripts"] += 1
    for node in soup.find_all(["noscript", "iframe", "object", "embed"]):
        node.decompose()
        counts["frames"] += 1
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
        counts["comments"] += 1
    for field in soup.find_all(["input", "textarea", "select", "option"]):
        if field.get("value"):
            field["value"] = ""
            counts["form_values"] += 1
        if field.name == "textarea" and field.string:
            field.string = ""
            counts["form_values"] += 1
    for meta in soup.find_all("meta"):
        if _TOKEN_NAME.search(str(meta.get("name") or meta.get("property") or "")) and meta.get("content"):
            meta["content"] = "REDACTED"
            counts["tokens"] += 1
    for node in soup.find_all(True):
        for attribute in _URL_ATTRIBUTES:
            value = node.get(attribute)
            if isinstance(value, str) and "?" in value:
                cleaned, changed = _clean_url(value)
                if changed:
                    node[attribute] = cleaned
                    counts["tokens"] += changed
        for attribute, value in list(node.attrs.items()):
            if attribute.startswith("data-") and isinstance(value, str) and _TOKEN_NAME.search(attribute):
                node[attribute] = "REDACTED"
                counts["tokens"] += 1
    for text in soup.find_all(string=True):
        if isinstance(text, Comment) or text.parent is None or text.parent.name == "script":
            continue
        redacted = _DIGITS.sub("[number]", _PHONE.sub("[phone]", _EMAIL.sub("[email]", str(text))))
        if redacted != str(text):
            counts["contact_details"] += 1
            text.replace_with(redacted)
    return str(soup), counts
