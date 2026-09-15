# DataForge Website Preset Specification

## Purpose

This specification defines the portable configuration unit used by the DataForge Website Preset Library. A preset identifies one **website family + page type + version**, not merely a domain. It tells the scraping engine what URLs it may accept, which compliant strategies it may use, how to extract/validate fields, how to paginate, and how to measure health.

Built-in presets are reviewed packages. User modifications create derived custom presets that preserve their parent and do not overwrite built-in behavior.

## Identifier and Versioning

Use this identifier form:

```text
<provider>.<page_type>@<semver>
```

Examples:

```text
amazon.search_results@1.0.0
amazon.product_details@1.0.0
zillow.property_details@1.0.0
generic.html_list@1.0.0
```

- A **patch** changes selectors or transforms without altering output meaning.
- A **minor** version adds optional fields or compatible capabilities.
- A **major** version changes required fields, field meaning, URL semantics, or strategy/policy behavior.
- Existing jobs resolve and store an exact immutable version. A later package update cannot alter a completed or queued job.
- Presets can be `active`, `deprecated`, `degraded`, or `disabled`. `disabled` versions cannot start new jobs.

## Conceptual Schema

The canonical implementation can be JSON Schema, Pydantic, or TypeScript/Zod, but must preserve these fields.

```yaml
id: amazon.search_results
version: 1.0.0
display_name: Amazon — Search Results
category: ecommerce
provider:
  name: Amazon
  homepage: https://www.amazon.com/
page_type: search_results
description: Extracts a result card per eligible product from a permitted public search page.

url_scope:
  allowed_hosts: [amazon.com, www.amazon.com]
  allowed_path_patterns: ["^/s(?:/|$)"]
  canonicalization:
    remove_query_parameters: [ref, tag, linkCode]
    preserve_query_parameters: [k, page, s]

policy:
  collection_basis: public_html
  requires_user_authorization_acknowledgement: true
  robots_policy: respect
  authentication: forbidden
  captcha_or_access_challenge: stop
  paywall_or_rate_limit: stop
  personal_data_classification: limited
  notes: "Use only for data the user is authorized to collect and process."

strategy:
  preferred: http
  allowed: [http, webview]
  api_integration: null
  webview_only_if:
    - initial_response_lacks_required_result_cards
  prohibited_escalations:
    - access_denied
    - captcha
    - login_required
    - robots_disallowed

request_limits:
  max_concurrency: 1
  min_delay_ms: 2000
  max_pages_default: 10
  max_records_default: 500
  max_duration_seconds: 900

extraction:
  interactive_builder:
    allow_element_picker: true
    allowed_attributes: [href, src, alt, title, data-testid]
    allow_selector_editing: true
    require_successful_test_run_before_full_run: true
  record_root:
    css: "[data-component-type='s-search-result']"
  fields:
    - key: title
      type: string
      required: true
      selectors:
        - css: "h2 a span::text"
      transforms: [trim, collapse_whitespace]
      validate:
        min_length: 2
    - key: product_url
      type: url
      required: true
      selectors:
        - css: "h2 a::attr(href)"
      transforms: [to_absolute_url, canonicalize_url]
      validate:
        allowed_hosts: [amazon.com, www.amazon.com]
    - key: price_amount
      type: decimal
      required: false
      selectors:
        - css: ".a-price .a-offscreen::text"
      transforms: [trim, parse_currency_amount]
    - key: rating
      type: decimal
      required: false
      selectors:
        - css: ".a-icon-alt::text"
      transforms: [parse_rating]
      validate: { minimum: 0, maximum: 5 }
    - key: review_count
      type: integer
      required: false
      selectors:
        - css: "[aria-label$='ratings']::text"
      transforms: [parse_integer]

pagination:
  type: next_link
  next:
    css: ".s-pagination-next:not(.s-pagination-disabled)::attr(href)"
  stop_conditions: [max_pages, max_records, repeated_canonical_url, no_new_records]

validation:
  minimum_record_coverage: 0.70
  required_field_coverage:
    title: 0.90
    product_url: 0.95
  unique_by: [product_url]
  reject_record_when: [missing_required_field, invalid_url]

field_mappings:
  canonical_entity: product
  output:
    name: title
    source_url: product_url
    price: price_amount
    rating: rating
    review_count: review_count

health:
  fixture_tests:
    - fixtures/amazon/search-results-basic.html
  expected:
    minimum_records: 3
    required_field_coverage: 0.90
  live_check:
    enabled: false
    only_when_permitted: true
  degrade_when:
    - fixture_test_fails
    - required_field_coverage_below_threshold
  disable_when:
    - policy_violation_detected
    - three_consecutive_health_failures
```

The example describes configuration shape only. It is not a claim that the illustrated selectors remain correct or that a run is permitted on any specific site.

## Required Fields

| Section | Required behavior |
| --- | --- |
| Identity | Stable ID, semantic version, display name, category, provider, and page type. |
| URL scope | Exact allowed hosts/path patterns plus canonicalization. Do not accept arbitrary URLs. |
| Policy | Access basis, authentication/robots/challenge handling, personal-data classification, and explicit stop behavior. |
| Strategy | Preferred/allowed methods and conditions under which embedded-WebView rendering is permitted. |
| Limits | Safe defaults for concurrency, delay, pages, records, and duration. |
| Extraction | Record root or API item path, field selectors/mappings, transforms, types, and requiredness. |
| Pagination | Mode, location of next token/link, canonicalization, and stop conditions. |
| Validation | Field coverage, record rejection, uniqueness, and schema validation. |
| Mappings | Canonical DataForge entity and target field names. |
| Health | Fixture checks, optional permitted live checks, and degrade/disable thresholds. |

## Page-Type Taxonomy

Use this controlled vocabulary when possible:

| Page type | Typical record unit |
| --- | --- |
| `search_results` | A result card from a query. |
| `category_listing` | A result card from a browse/category view. |
| `detail` | One complete item, listing, job, business, article, package, or media entity. |
| `profile` | One seller, agent, company, author, organization, or creator. |
| `reviews` | One review/comment, with parent entity provenance. |
| `ranking` | One ranked item from a bestseller/chart/trending page. |
| `location_listing` | A result card scoped to a geographic area. |
| `article` | One story, document, or post. |
| `table` | One row in a structured public table. |
| `api_collection` | One JSON object from an authorized API collection. |

If a site needs a meaningful variation (for example `for_rent_listing`), declare it explicitly rather than overloading an unrelated type.

## Canonical Entity Mappings

| Entity | Core canonical fields |
| --- | --- |
| `product` | `name`, `source_url`, `sku_or_id`, `price`, `currency`, `rating`, `review_count`, `image_url`, `seller_name`. |
| `property` | `address`, `source_url`, `listing_id`, `price`, `beds`, `baths`, `area`, `property_type`, `listing_status`, `agent_name`. |
| `job` | `title`, `source_url`, `company_name`, `location`, `employment_type`, `salary`, `posted_at`, `description`. |
| `business` | `name`, `source_url`, `address`, `phone`, `website`, `category`, `rating`, `review_count`. |
| `article` | `title`, `source_url`, `author`, `published_at`, `summary`, `topic`. |
| `media` | `title`, `source_url`, `creator`, `published_at`, `duration`, `rating`, `review_count`. |
| `software` | `name`, `source_url`, `owner`, `version`, `license`, `description`, `download_count`, `rating`. |

Every output row must additionally contain `source_url`, `source_retrieved_at`, `preset_id`, `preset_version`, and `strategy_used`.

## Interactive Element Selection

The custom-preset editor must support a visible child-WebView preview with its controls beside it. The picker captures user-selected DOM elements through the native WebView bridge and creates schema-valid extraction configuration; it does not execute arbitrary page JavaScript or attempt to bypass a page's controls.

| Picker action | Stored preset output |
| --- | --- |
| Pick element | One field selector relative to the current `record_root`, extraction attribute, transform, and type. |
| Pick repeated item | A `record_root` selector and a preview of repeated candidate records. |
| Pick next page | A `next_link` selector or a validated page/cursor mapping. |
| Pick detail link | A field selector plus bounded host/path scope for `detail_links` pagination. |

The bridge must capture only safe selector metadata: element tag, safe attributes, sample visible text, frame context, and candidate locators. It must redact form values, cookies, tokens, session identifiers, and hidden sensitive fields. Selection is limited to the rendered document and permitted frames; inaccessible cross-origin frames must be reported as unavailable rather than bypassed.

After every picker action, DataForge must show the generated selector, sample values, field type, transform, relative record-root context, and validation result. Saving creates a draft custom preset. Starting a full run requires a successful bounded test run.

## Validation Rules

- Validate type and requiredness after all transforms.
- Apply selector fallbacks in order and retain which selector won only in diagnostic metadata.
- Reject a record that lacks a required identity/source field; do not emit partial rows that cannot be traced.
- Deduplicate within a run using `unique_by`, retaining the first valid canonical record and counting suppressed duplicates.
- Surface nonfatal field failures as record warnings; surface health thresholds as preset warnings.
- Do not silently invent values through generative extraction. Any AI-assisted mapping must be opt-in, labeled, reviewable, and evaluated before saving.

## Health Checks and Operations

### Fixture Checks

Each built-in preset must include sanitized, legally retainable fixtures covering normal, sparse, and changed-layout cases. The test asserts record count, required field coverage, type validity, pagination parsing, and canonical mapping.

### Optional Live Checks

Live checks run only against endpoints that the operator is permitted to access and only at configured low frequency. They must respect every policy and request limit. A live check may report health; it must never attempt an escalation after access is denied.

### Status Rules

| Status | Meaning | New jobs |
| --- | --- | --- |
| `active` | Fixture and health thresholds pass. | Allowed. |
| `degraded` | A recoverable extraction or coverage issue exists. | Allowed only after a clear user warning. |
| `deprecated` | A successor version is available. | Allowed with migration notice. |
| `disabled` | Policy, security, or repeated health failure. | Blocked. |

## Custom Presets

Custom presets must:

- Be namespaced as `custom.<owner>.<name>@<semver>`.
- Store `parent_preset_id` and `parent_preset_version` when derived from a bundled preset.
- Be validated against the same schema, policy gate, URL scope, rate caps, and test-run limits as built-ins.
- Require a successful test scrape before a full run can start.
- Be exportable/importable without credentials, cookies, raw responses, or personally identifying run data.
- Never broaden a built-in preset’s URL scope, access policy, or strategy permissions without independent policy review.

## Preset Authoring Checklist

- [ ] State the site family and exact page type.
- [ ] Define strict URL scope and canonicalization.
- [ ] Declare policy, access assumptions, and stop conditions.
- [ ] Prefer authorized API, then HTTP, then bounded rendering in DataForge's embedded WebView. Never launch a separate browser window or automation process.
- [ ] Define record root/API path, typed fields, transforms, and fallbacks.
- [ ] Define whether the preset permits child-WebView element selection and which attributes may be extracted.
- [ ] Define pagination and every termination condition.
- [ ] Map source fields to a canonical DataForge entity.
- [ ] Add sanitized fixtures and tests for normal and failure cases.
- [ ] Set field-coverage thresholds and degradation rules.
- [ ] Assign a semantic version, owner, and maintenance review date.
