"""Preset schema validation (Website Preset Specification) and scope resolution for generic presets."""

from __future__ import annotations

import copy
import re
from urllib.parse import urlparse

from .extraction import TRANSFORMS

STATUSES = ("active", "degraded", "deprecated", "disabled")
STRATEGIES = ("api", "http", "webview")
FIELD_TYPES = ("string", "url", "decimal", "integer")
PAGINATION_TYPES = ("none", "next_link", "page_parameter", "cursor", "infinite_scroll", "api_cursor", "detail_links")
_ID = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def validate_preset(preset: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(preset, dict):
        return ["Preset must be an object"]
    if not _ID.match(str(preset.get("id", ""))):
        errors.append("id must look like <provider>.<page_type>")
    if str(preset.get("id", "")).startswith("custom.") and str(preset["id"]).count(".") < 2:
        errors.append("custom presets must be namespaced custom.<owner>.<name>")
    if not _SEMVER.match(str(preset.get("version", ""))):
        errors.append("version must be semantic (MAJOR.MINOR.PATCH)")
    for key in ("display_name", "category", "page_type"):
        if not isinstance(preset.get(key), str) or not preset[key]:
            errors.append(f"{key} is required")
    if preset.get("status", "active") not in STATUSES:
        errors.append(f"status must be one of {STATUSES}")

    scope = preset.get("url_scope")
    if not isinstance(scope, dict):
        errors.append("url_scope is required")
    else:
        hosts = scope.get("allowed_hosts")
        if not isinstance(hosts, list) or (not hosts and not scope.get("user_supplied_host")):
            errors.append("url_scope.allowed_hosts must list hosts (or set user_supplied_host for generic presets)")
        for pattern in scope.get("allowed_path_patterns", []):
            try:
                re.compile(pattern)
            except re.error:
                errors.append(f"invalid path pattern {pattern!r}")

    policy = preset.get("policy")
    if not isinstance(policy, dict):
        errors.append("policy is required")
    else:
        if policy.get("requires_user_authorization_acknowledgement") is not True:
            errors.append("policy.requires_user_authorization_acknowledgement must be true")
        if policy.get("authentication", "forbidden") != "forbidden":
            errors.append("policy.authentication must be forbidden")
        for key in ("captcha_or_access_challenge", "paywall_or_rate_limit"):
            if policy.get(key, "stop") != "stop":
                errors.append(f"policy.{key} must be stop")

    strategy = preset.get("strategy") if isinstance(preset.get("strategy"), dict) else {}
    allowed = strategy.get("allowed", [strategy.get("preferred")])
    if strategy.get("preferred") not in STRATEGIES or not set(allowed) <= set(STRATEGIES) or strategy.get("preferred") not in allowed:
        errors.append("strategy.preferred must be one of the allowed strategies (api, http, webview)")

    integration = strategy.get("api_integration")
    if integration is not None:
        if not isinstance(integration, dict) or integration.get("auth") not in ("bearer", "header", None):
            errors.append("strategy.api_integration.auth must be bearer or header")
        elif integration.get("auth") == "header" and str(integration.get("header_name", "")).lower() in ("", "cookie", "host", "set-cookie"):
            errors.append("strategy.api_integration.header_name must be a named API key header (not Cookie or Host)")
        elif integration.get("auth") and strategy.get("preferred") != "api":
            errors.append("credentials are only allowed for API strategy presets")
    if preset.get("status") == "deprecated" and not preset.get("successor"):
        errors.append("deprecated presets must name a successor (id@version)")

    limits = preset.get("request_limits") if isinstance(preset.get("request_limits"), dict) else {}
    for key in ("max_concurrency", "min_delay_ms", "max_pages_default", "max_records_default", "max_duration_seconds"):
        if not isinstance(limits.get(key), int) or limits[key] < 0:
            errors.append(f"request_limits.{key} must be a non-negative integer")

    extraction = preset.get("extraction") if isinstance(preset.get("extraction"), dict) else {}
    if strategy.get("preferred") == "api":
        if not isinstance(extraction.get("item_path"), str):
            errors.append("extraction.item_path is required for API presets")
    elif not isinstance((extraction.get("record_root") or {}).get("css"), str):
        errors.append("extraction.record_root.css is required")
    fields = extraction.get("fields")
    if not isinstance(fields, list) or not fields:
        errors.append("extraction.fields must be a non-empty list")
        fields = []
    keys = [f.get("key") for f in fields if isinstance(f, dict)]
    if len(keys) != len(set(keys)) or any(not isinstance(k, str) or not k for k in keys):
        errors.append("extraction field keys must be unique non-empty strings")
    for field in fields:
        if not isinstance(field, dict):
            continue
        if field.get("type", "string") not in FIELD_TYPES:
            errors.append(f"field {field.get('key')}: type must be one of {FIELD_TYPES}")
        unknown = [t for t in field.get("transforms", []) if t not in TRANSFORMS]
        if unknown:
            errors.append(f"field {field.get('key')}: unknown transforms {unknown}")
        if strategy.get("preferred") != "api" and not field.get("selectors"):
            errors.append(f"field {field.get('key')}: at least one selector is required")

    pagination = preset.get("pagination") if isinstance(preset.get("pagination"), dict) else {"type": "none"}
    if pagination.get("type", "none") not in PAGINATION_TYPES:
        errors.append(f"pagination.type must be one of {PAGINATION_TYPES}")
    return errors


def resolve_for_url(preset: dict, start_url: str) -> dict:
    """Pin a generic preset's scope to the host the user supplied. Built-in scopes are never broadened."""
    resolved = copy.deepcopy(preset)
    scope = resolved["url_scope"]
    if scope.get("user_supplied_host"):
        host = urlparse(start_url).hostname
        if not host:
            raise ValueError("Start URL must include a host")
        scope["allowed_hosts"] = [host]
    return resolved
