"""Preset schema validation (Website Preset Specification) and scope resolution for generic presets."""

from __future__ import annotations

import copy
import re
from urllib.parse import urlparse

from .extraction import EXTRACTION_MODES, TRANSFORMS
from .signals import PURPOSES

STATUSES = ("active", "degraded", "deprecated", "disabled")
STRATEGIES = ("api", "http", "webview")
FIELD_TYPES = ("string", "url", "decimal", "integer")
PAGINATION_TYPES = ("none", "next_link", "page_parameter", "cursor", "infinite_scroll", "api_cursor", "detail_links")
DISCOVERY_MODES = ("none", "sitemap", "feed", "crawl", "llms_txt")
ENGINES = ("auto", "httpx", "scrapy")
VARIABLE_TYPES = ("string", "integer", "number", "enum", "sparql", "path")
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
        if "purpose" in policy and policy["purpose"] not in PURPOSES:
            errors.append(f"policy.purpose must be one of {PURPOSES}")

    strategy = preset.get("strategy") if isinstance(preset.get("strategy"), dict) else {}
    allowed = strategy.get("allowed", [strategy.get("preferred")])
    if strategy.get("preferred") not in STRATEGIES or not set(allowed) <= set(STRATEGIES) or strategy.get("preferred") not in allowed:
        errors.append("strategy.preferred must be one of the allowed strategies (api, http, webview)")

    integration = strategy.get("api_integration")
    if integration is not None:
        if not isinstance(integration, dict) or integration.get("auth") not in ("bearer", "header", "query_param", None):
            errors.append("strategy.api_integration.auth must be bearer, header, or query_param")
        elif integration.get("auth") == "query_param" and not re.fullmatch(r"[A-Za-z_][\w.-]{0,40}", str(integration.get("parameter", ""))):
            errors.append("strategy.api_integration.parameter must name the query parameter for query_param auth")
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
    rps = limits.get("max_requests_per_second")
    if rps is not None and (not isinstance(rps, (int, float)) or not 0 < rps <= 10):
        errors.append("request_limits.max_requests_per_second must be between 0 and 10")
    if "respect_retry_after" in limits and not isinstance(limits["respect_retry_after"], bool):
        errors.append("request_limits.respect_retry_after must be true or false")
    if preset.get("engine", "auto") not in ENGINES:
        errors.append(f"engine must be one of {ENGINES}")

    extraction = preset.get("extraction") if isinstance(preset.get("extraction"), dict) else {}
    mode = "api" if strategy.get("preferred") == "api" else extraction.get("mode", "selectors")
    selector_free = mode in ("structured_data", "article", "document_tables") or (preset.get("discovery") or {}).get("mode") == "feed"
    if mode not in EXTRACTION_MODES:
        errors.append(f"extraction.mode must be one of {EXTRACTION_MODES}")
    if strategy.get("preferred") == "api":
        if not isinstance(extraction.get("item_path"), str):
            errors.append("extraction.item_path is required for API presets")
    elif mode == "selectors" and not selector_free and not isinstance((extraction.get("record_root") or {}).get("css"), str) and not isinstance((extraction.get("record_root") or {}).get("xpath"), str):
        errors.append("extraction.record_root.css is required")
    if extraction.get("parser", "bs4") not in ("bs4", "parsel", "selectolax"):
        errors.append("extraction.parser must be bs4, parsel, or selectolax")
    fields = extraction.get("fields")
    if not isinstance(fields, list) or (not fields and not selector_free and not extraction.get("capture_all")):
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
        if strategy.get("preferred") != "api" and mode == "selectors" and not selector_free and not field.get("selectors"):
            errors.append(f"field {field.get('key')}: at least one selector is required")
        for selector in field.get("selectors", []):
            if isinstance(selector, dict) and selector.get("xpath"):
                try:
                    from lxml import etree

                    etree.XPath(str(selector["xpath"]))
                except Exception:  # noqa: BLE001
                    errors.append(f"field {field.get('key')}: invalid XPath {selector['xpath']!r}")

    pagination = preset.get("pagination") if isinstance(preset.get("pagination"), dict) else {"type": "none"}
    kind = pagination.get("type", "none")
    if kind not in PAGINATION_TYPES:
        errors.append(f"pagination.type must be one of {PAGINATION_TYPES}")
    required_config = {"next_link": "next", "cursor": "cursor", "detail_links": "links"}
    if kind in required_config and not isinstance((pagination.get(required_config[kind]) or {}).get("css"), str) and strategy.get("preferred") != "api":
        errors.append(f"pagination.{required_config[kind]}.css is required for {kind} pagination")
    if kind == "infinite_scroll" and "webview" not in allowed:
        errors.append("infinite_scroll pagination requires the webview strategy")
    if kind == "infinite_scroll" and int(pagination.get("max_scrolls", 20)) > 100:
        errors.append("pagination.max_scrolls cannot exceed 100")

    discovery = preset.get("discovery") if isinstance(preset.get("discovery"), dict) else {"mode": "none"}
    if discovery.get("mode", "none") not in DISCOVERY_MODES:
        errors.append(f"discovery.mode must be one of {DISCOVERY_MODES}")
    crawl = discovery.get("crawl") or {}
    if discovery.get("mode") == "crawl":
        if not isinstance(crawl.get("max_depth", 2), int) or not 0 <= crawl.get("max_depth", 2) <= 5:
            errors.append("discovery.crawl.max_depth must be between 0 and 5")
        if crawl.get("same_host_only", True) is not True:
            errors.append("discovery.crawl.same_host_only must be true")
    for section, key in (("crawl", "link_pattern"), ("crawl", "extract_pattern"), ("crawl", "exclude_pattern"), ("sitemap", "url_pattern")):
        pattern = (discovery.get(section) or {}).get(key)
        if pattern:
            try:
                re.compile(pattern)
            except re.error:
                errors.append(f"discovery.{section}.{key} is not a valid regular expression")
    if discovery.get("mode", "none") != "none" and kind not in ("none", None):
        errors.append("discovery and pagination cannot be combined; discovery supplies the pages")

    request = preset.get("request")
    if request is not None:
        if not isinstance(request, dict):
            errors.append("request must be an object")
        else:
            if request.get("method", "GET") not in ("GET", "POST"):
                errors.append("request.method must be GET or POST")
            template = str(request.get("url_template", ""))
            if template and not template.startswith("https://"):
                errors.append("request.url_template must be an https URL")
            used = set(re.findall(r"\{\{\s*([a-zA-Z_]\w*)\s*\}\}", template + str(request.get("body_template", ""))))
            declared = request.get("variables") or {}
            if not isinstance(declared, dict):
                errors.append("request.variables must be an object")
                declared = {}
            for name in sorted(used - set(declared)):
                errors.append(f"request template uses undeclared variable {name!r}")
            for name, spec in declared.items():
                if not isinstance(spec, dict) or spec.get("type", "string") not in VARIABLE_TYPES:
                    errors.append(f"request.variables.{name}.type must be one of {VARIABLE_TYPES}")
                elif spec.get("type") == "enum" and not spec.get("choices"):
                    errors.append(f"request.variables.{name} needs choices")
                elif spec.get("pattern"):
                    try:
                        re.compile(spec["pattern"])
                    except re.error:
                        errors.append(f"request.variables.{name}.pattern is not a valid regular expression")
            if template and strategy.get("preferred") == "api" and not (scope or {}).get("user_supplied_host"):
                host_part = template.split("//", 1)[-1].split("/", 1)[0]
                host_variable = re.fullmatch(r"\{\{\s*([a-zA-Z_]\w*)\s*\}\}", host_part)
                allowed_hosts = scope.get("allowed_hosts", []) if isinstance(scope, dict) else []
                if host_variable:
                    spec = declared.get(host_variable.group(1)) or {}
                    if spec.get("type") != "enum" or not set(spec.get("choices", [])) <= set(allowed_hosts):
                        errors.append("a host variable in request.url_template must be an enum of url_scope.allowed_hosts")
                elif urlparse(_example_url(template)).hostname not in allowed_hosts:
                    errors.append("request.url_template host must be in url_scope.allowed_hosts")
    region = (preset.get("normalization") or {}).get("default_region")
    if region is not None and not re.fullmatch(r"[A-Z]{2}", str(region)):
        errors.append("normalization.default_region must be a two-letter region code")
    return errors


def _example_url(template: str) -> str:
    return re.sub(r"\{\{\s*[a-zA-Z_]\w*\s*\}\}", "x", template)


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
