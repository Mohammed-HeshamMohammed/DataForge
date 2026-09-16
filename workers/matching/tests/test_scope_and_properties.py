from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from dataforge_matching import engine
from dataforge_matching.normalize import address, email, normalize_row, phone, postal_code, text


def cross_request(**settings_overrides):
    return {
        "schema_version": 1,
        "entity_type": "person",
        "mappings": {
            "crm": {"Full Name": "name", "Mobile": "phone", "Street": "address", "ZIP": "postal_code"},
            "leads": {"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code", "Notes": "other"},
        },
        "rows": [
            {"id": "c1", "row_number": 2, "source": "crm", "raw": {"Full Name": "Ada Lovelace", "Mobile": "(512) 555-0182", "Street": "123 Main St", "ZIP": "78701"}},
            {"id": "l1", "row_number": 2, "source": "leads", "raw": {"Name": "Ada Lovelace", "Phone": "512-555-0182", "Address": "123 Main Street", "Zip": "78701", "Notes": "hot lead"}},
            {"id": "l2", "row_number": 3, "source": "leads", "raw": {"Name": "Bob Stone", "Phone": "2125550100", "Address": "9 Oak Ave", "Zip": "10001", "Notes": ""}},
        ],
        "settings": settings_overrides,
        "constraints": [],
    }


def test_cross_dataset_matching_uses_each_sources_mapping_and_trust_order():
    result = engine.run(cross_request(source_trust=["crm", "leads"]))
    assert result["metrics"]["decisions_by_scope"]["across"]["match"] == 1
    record = next(r for r in result["canonical"] if r["survivor_row_id"] in ("c1", "l1"))
    assert record["survivor_row_id"] == "c1"  # trusted CRM wins over the more complete lead row
    assert record["values"]["canonical.phone"] == "(512) 555-0182"
    assert record["field_provenance"]["canonical.name"]["column"] == "Full Name"
    assert record["values"]["Notes"] == "hot lead"  # fields only the lower-trust source has are still filled

    flipped = engine.run(cross_request(source_trust=["leads", "crm"]))
    assert next(r for r in flipped["canonical"] if r["survivor_row_id"] in ("c1", "l1"))["survivor_row_id"] == "l1"
    assert flipped["metrics"]["survivor_sources"] == {"leads": 2}


def test_datasets_without_a_shared_evidence_role_are_rejected():
    request = cross_request()
    request["mappings"]["leads"] = {"Name": "name", "Email": "email"}
    try:
        engine.run(request)
    except engine.MatchRequestError as error:
        assert "share no" in str(error)
    else:
        raise AssertionError("expected MatchRequestError")


def test_reviewer_chosen_values_override_survivor_values_with_provenance():
    request = cross_request(source_trust=["crm", "leads"])
    request["overrides"] = {"l1": {"Full Name", "Name"}}
    result = engine.run(request)
    record = next(r for r in result["canonical"] if r["survivor_row_id"] == "c1")
    assert record["field_provenance"]["Name"] == {"row_id": "l1", "rule": "reviewer_choice"}


def test_metrics_include_stage_timings_and_size_distribution():
    metrics = engine.run(cross_request())["metrics"]
    assert set(metrics["stage_seconds"]) == {"normalizing", "finding_candidates", "evaluating_evidence", "building_groups"}
    assert metrics["cluster_size_distribution"] == {"2": 1, "1": 1}
    assert metrics["rows_per_second"] > 0


# --- property-based tests --------------------------------------------------------------------------

digits = st.text(alphabet="0123456789", min_size=5, max_size=5)


@given(digits, st.sampled_from(["", "-1234", "1234"]))
def test_postal_codes_keep_leading_zeros(zip5, suffix):
    assert postal_code(zip5 + suffix) == zip5


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789._+-", min_size=1, max_size=20), st.text(alphabet="abcdefghij", min_size=1, max_size=10))
def test_valid_emails_keep_their_at_sign_and_are_case_insensitive(local, domain):
    value = f"{local}@{domain}.com"
    normalized = email(value.upper())
    assert normalized is not None and normalized.count("@") >= 1 and normalized == email(value)


@given(st.text(alphabet="0123456789", min_size=10, max_size=10), st.sampled_from(["({a}) {b}-{c}", "{a}.{b}.{c}", "{a}-{b}-{c}", "+1 {a} {b} {c}", "1{a}{b}{c}"]))
def test_phone_formatting_does_not_change_the_normalized_number(number, template):
    formatted = template.format(a=number[:3], b=number[3:6], c=number[6:])
    assert phone(formatted) == "+1" + number


@given(st.text(max_size=60))
def test_normalization_is_idempotent_and_never_raises(value):
    once = text(value)
    assert text(once) == once
    address(value)
    phone(value)
    email(value)


@given(st.lists(st.fixed_dictionaries({"Phone": st.sampled_from(["5125550182", "2125550100", "3105550199", ""]), "Name": st.sampled_from(["Ada", "Bob", "Cy", ""])}), min_size=0, max_size=40), st.integers(min_value=2, max_value=6))
@settings(max_examples=60, deadline=None)
def test_candidate_generation_is_bounded_deduplicated_and_ordered(rows, cap):
    mapping = {"Phone": "phone", "Name": "name"}
    normalized = {f"r{i}": normalize_row(raw, mapping) for i, raw in enumerate(rows)}
    pairs, metrics = engine.generate_candidates(normalized, cap)
    assert all(left < right for left, right in pairs)  # each pair once, canonical order
    phone_groups = Counter(n and next(iter(n["phone"]), None) for n in normalized.values())
    expected = sum(size * (size - 1) // 2 for value, size in phone_groups.items() if value and 2 <= size <= cap)
    assert len(pairs) == expected  # only same-phone pairs, never from blocks above the cap
    assert metrics["oversized_block_count"] == sum(1 for value, size in phone_groups.items() if value and size > cap)


@given(st.lists(st.sampled_from([
    {"Phone": "5125550182", "Address": "123 Main St", "Name": "Ada"},
    {"Phone": "5125550182", "Address": "125 Main St", "Name": "Ada"},
    {"Phone": "2125550100", "Address": "9 Oak Ave", "Name": "Bob"},
    {"Phone": "", "Address": "9 Oak Ave", "Name": "Bob"},
]), min_size=0, max_size=12))
@settings(max_examples=40, deadline=None)
def test_clusters_never_contain_conflicting_house_numbers_and_cover_every_row(raws):
    request = {"schema_version": 1, "mapping": {"Phone": "phone", "Address": "address", "Name": "name"},
               "rows": [{"id": f"r{i:02d}", "row_number": i, "raw": raw} for i, raw in enumerate(raws)], "settings": {}}
    result = engine.run(request)
    seen = [row_id for c in result["clusters"] for row_id in c["member_row_ids"]]
    assert sorted(seen) == sorted(r["id"] for r in request["rows"])
    for c in result["clusters"]:
        houses = {address(raws[int(row_id[1:])]["Address"])["house_number"] for row_id in c["member_row_ids"]}
        assert len(houses) <= 1
