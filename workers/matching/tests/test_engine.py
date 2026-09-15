import pytest

from dataforge_matching import engine
from dataforge_matching.normalize import address, email, phone, postal_code, url


def request(rows, mapping, **settings):
    return {
        "schema_version": 1,
        "entity_type": "person",
        "mapping": mapping,
        "rows": [{"id": f"r{i}", "row_number": i, "raw": raw} for i, raw in enumerate(rows, start=2)],
        "settings": settings,
        "constraints": [],
    }


def decision_for(result, left, right):
    return next(d for d in result["decisions"] if {d["left_row_id"], d["right_row_id"]} == {left, right})


PEOPLE = {"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code"}


def test_normalization_preserves_meaningful_characters():
    assert postal_code("00123") == "00123"
    assert postal_code("00123-4567") == "00123"
    assert email(" Ada@Example.test ") == "ada@example.test"
    assert email("not-an-email") is None
    assert phone("(512) 555-0182") == "+15125550182"
    assert phone("555-0182", default_region=None) == "5550182"
    assert address("123 North Main Street Apt 4")["house_number"] == "123"
    assert address("123 North Main Street Apt 4")["street"] == "n main st"
    assert address("123 North Main Street Apt 4")["unit"] == "4"
    assert url("https://www.Example.com/item/1/?utm_source=x&id=7") == "//example.com/item/1?id=7"


def test_exact_phone_and_address_auto_match():
    result = engine.run(request(
        [
            {"Name": "Ada Lovelace", "Phone": "(512) 555-0182", "Address": "123 Main Street", "Zip": "78701"},
            {"Name": "Ada Lovelace", "Phone": "512.555.0182", "Address": "123 Main St", "Zip": "78701"},
        ],
        PEOPLE,
    ))
    assert decision_for(result, "r2", "r3")["decision"] == "match"
    assert result["metrics"]["multi_member_clusters"] == 1


def test_name_and_region_similarity_alone_never_auto_merges():
    result = engine.run(request(
        [
            {"Name": "Ada Lovelace", "Zip": "78701", "Phone": ""},
            {"Name": "Ada Lovelace", "Zip": "78701", "Phone": ""},
        ],
        {"Name": "name", "Zip": "postal_code", "Phone": "phone"},
    ))
    assert all(d["decision"] != "match" for d in result["decisions"])
    assert result["metrics"]["multi_member_clusters"] == 0


def test_different_house_numbers_block_merge_despite_shared_phone():
    result = engine.run(request(
        [
            {"Name": "Ada Lovelace", "Phone": "5125550182", "Address": "123 Main St", "Zip": "78701"},
            {"Name": "Ada Lovelace", "Phone": "5125550182", "Address": "125 Main St", "Zip": "78701"},
        ],
        PEOPLE,
    ))
    d = decision_for(result, "r2", "r3")
    assert d["decision"] == "non_match"
    assert d["reason"].startswith("Cannot auto-merge")
    assert result["metrics"]["guard_reasons"] == {"Address: different house number": 1}


def test_conflicting_identifiers_are_a_hard_non_match():
    result = engine.run(request(
        [{"APN": "001", "Phone": "5125550182"}, {"APN": "002", "Phone": "5125550182"}],
        {"APN": "identifier", "Phone": "phone"},
    ))
    assert decision_for(result, "r2", "r3")["decision"] == "non_match"


def test_missing_values_are_no_evidence():
    evidence, score, comparable = engine.compare(
        engine.normalize_row({"Phone": "5125550182", "Email": ""}, {"Phone": "phone", "Email": "email"}),
        engine.normalize_row({"Phone": "5125550182", "Email": "a@b.test"}, {"Phone": "phone", "Email": "email"}),
    )
    assert [e["field"] for e in evidence] == ["phone"]
    assert score == 1.0 and comparable == 1


def test_single_strong_field_goes_to_review_not_merge():
    result = engine.run(request(
        [{"Phone": "5125550182", "Name": ""}, {"Phone": "5125550182", "Name": ""}],
        {"Phone": "phone", "Name": "name"},
    ))
    assert decision_for(result, "r2", "r3")["decision"] == "possible_match"


def test_oversized_blocks_are_capped_not_compared():
    rows = [{"Phone": "5125550182", "Name": f"Person {i}"} for i in range(6)]
    result = engine.run(request(rows, {"Phone": "phone", "Name": "name"}, max_block_size=5))
    assert result["metrics"]["candidate_pairs"] == 0
    assert result["metrics"]["oversized_block_count"] == 1


def test_ambiguous_bridge_is_sent_to_review():
    normalized = {f"r{i}": engine.normalize_row({}, {}) for i in range(4)}
    edge = lambda a, b, s: {"id": f"{a}{b}", "left_row_id": a, "right_row_id": b, "decision": "match", "score": s}
    # r0-r1 and r2-r3 are strong pairs; a single weaker edge r1-r2 would chain them together.
    clusters, bridges, metrics = engine.cluster(
        list(normalized), normalized, [edge("r0", "r1", 1.0), edge("r2", "r3", 1.0), edge("r1", "r2", 0.96)], []
    )
    assert bridges == ["r1r2"]
    assert sorted(len(c["member_row_ids"]) for c in clusters) == [2, 2]


def test_must_not_link_constraint_prevents_merge_and_is_transitive_safe():
    normalized = {f"r{i}": engine.normalize_row({}, {}) for i in range(3)}
    edge = lambda a, b: {"id": f"{a}{b}", "left_row_id": a, "right_row_id": b, "decision": "match", "score": 1.0}
    clusters, _, _ = engine.cluster(
        list(normalized), normalized, [edge("r0", "r1"), edge("r1", "r2")],
        [{"left_row_id": "r0", "right_row_id": "r2", "kind": "must_not_link"}],
    )
    assert all(not {"r0", "r2"} <= set(c["member_row_ids"]) for c in clusters)


def test_locked_group_neither_gains_nor_loses_members():
    normalized = {f"r{i}": engine.normalize_row({}, {}) for i in range(3)}
    edge = lambda a, b: {"id": f"{a}{b}", "left_row_id": a, "right_row_id": b, "decision": "match", "score": 1.0}
    # r0-r1 is locked with no edge between them; r1-r2 would normally attach r2.
    clusters, _, _ = engine.cluster(list(normalized), normalized, [edge("r1", "r2")], [], [["r0", "r1"]])
    groups = sorted(sorted(c["member_row_ids"]) for c in clusters)
    assert groups == [["r0", "r1"], ["r2"]]
    assert next(c for c in clusters if "r0" in c["member_row_ids"])["status"] == "locked"


def test_survivor_prefers_complete_rows_and_records_provenance():
    result = engine.run(request(
        [
            {"Name": "Ada Lovelace", "Phone": "5125550182", "Address": "123 Main St", "Zip": ""},
            {"Name": "Ada Lovelace", "Phone": "5125550182", "Address": "123 Main St", "Zip": "78701"},
        ],
        PEOPLE,
    ))
    record = next(c for c in result["canonical"] if len(c["field_provenance"]) == 4 and c["values"]["Zip"])
    assert record["survivor_row_id"] == "r3"
    assert record["field_provenance"]["Zip"]["row_id"] == "r3"


def test_same_inputs_reproduce_same_output():
    rows = [{"Name": "Ada", "Phone": "5125550182", "Address": "1 A St", "Zip": "1"}] * 3
    first = engine.run(request(rows, PEOPLE))
    second = engine.run(request(rows, PEOPLE))
    assert first["decisions"] == second["decisions"] and first["clusters"] == second["clusters"]


def test_ranking_learns_from_review_labels_and_refuses_too_few():
    from dataforge_matching import ranking

    phone = {"field": "phone", "similarity": 1.0, "result": "exact", "strength": "strong", "explanation": ""}
    name = lambda s: {"field": "name", "similarity": s, "result": "similar", "strength": "supporting", "explanation": ""}
    labeled = [([phone, name(0.9 + i / 1000)], 0.94, 1) for i in range(15)] + [([phone, name(0.3 + i / 1000)], 0.6, 0) for i in range(15)]
    with pytest.raises(ValueError, match="at least"):
        ranking.train(labeled[:5])
    model = ranking.train(labeled)
    likely = ranking.predict(model["weights"], ranking.features([phone, name(0.95)], 0.95))
    unlikely = ranking.predict(model["weights"], ranking.features([phone, name(0.2)], 0.55))
    assert likely > unlikely
    assert model["evaluation"]["holdout_size"] == 7


def test_invalid_requests_fail_before_candidate_generation():
    with pytest.raises(engine.MatchRequestError, match="schema_version"):
        engine.run({**request([], PEOPLE), "schema_version": 99})
    with pytest.raises(engine.MatchRequestError, match="At least one"):
        engine.run(request([{"Name": "x"}], {"Name": "name"}))
    with pytest.raises(engine.MatchRequestError, match="positional"):
        engine.run(request([], {"A": "address", "B": "address", "P": "phone"}))
