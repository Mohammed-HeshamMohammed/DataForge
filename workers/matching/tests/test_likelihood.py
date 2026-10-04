"""Review likelihood: estimated from the dataset, rarer agreements count more, contradictions count against."""

from dataforge_matching import engine

MAPPING = {"Name": "name", "Phone": "phone", "City": "city"}


def run(rows):
    return engine.run({
        "schema_version": 1, "entity_type": "person", "mapping": MAPPING, "settings": {}, "constraints": [],
        "rows": [{"id": f"r{i}", "row_number": i, "raw": raw} for i, raw in enumerate(rows, start=2)],
    })


def pair(result, a, b):
    return next(d for d in result["decisions"] if {d["left_row_id"], d["right_row_id"]} == {a, b})


def test_a_rare_name_counts_more_than_a_common_one():
    rows = [
        {"Name": "Ada Lovelace", "City": "Austin"}, {"Name": "Ada Lovelace", "City": "Austin"},  # r2, r3: rare name
        {"Name": "John Smith", "City": "Austin"}, {"Name": "John Smith", "City": "Austin"},  # r4, r5: common name
        *({"Name": f"{first} Smith", "City": "Dallas", "Phone": f"21455501{i:02d}"} for i, first in enumerate(["Jim", "Mary", "Paul", "Anna", "Lee", "Ruth", "Carl", "Nina"])),
    ]
    result = run(rows)
    rare, common = pair(result, "r2", "r3"), pair(result, "r4", "r5")
    assert rare["decision"] == common["decision"] == "possible_match"  # the rules see the same evidence
    assert rare["likelihood"] > common["likelihood"]


FIRST = ["Ada", "Alan", "Barbara", "Claude", "Dorothy", "Edsger", "Frances", "Grace", "Hedy", "Ivan", "Joan", "Ken", "Lynn", "Margaret", "Niklaus", "Radia"]
LAST = ["Babbage", "Church", "Dijkstra", "Engelbart", "Floyd", "Gosling", "Hamming", "Iverson", "Kay", "Lamport", "Minsky", "Naur", "Perlis", "Ritchie", "Shannon", "Thompson"]
# Distinct people for the estimate to learn from, as any real list has.
BACKGROUND = [{"Name": f"{FIRST[i]} {LAST[(i * 7) % 16]}", "Phone": f"30355501{i:02d}", "City": ["Austin", "Denver", "Boston", "Miami"][i % 4]} for i in range(16)]


def test_contradictions_make_a_pair_unlikely_and_results_repeat():
    rows = [
        {"Name": "Susan Rodriguez", "Phone": "4303658070", "City": "Portland"},
        {"Name": "Samantha Rodriguez", "Phone": "4303658070", "City": "Portland"},
        {"Name": "Grace Hopper", "Phone": "2125550100", "City": "New York"},
        {"Name": "Grace Hopper", "Phone": "2125550100", "City": "New York"},
        *BACKGROUND,
    ]
    first = run(rows)
    assert pair(first, "r2", "r3")["likelihood"] < 0.01
    assert pair(first, "r4", "r5")["likelihood"] > 0.5  # more likely than not; certainty needs a larger file
    assert [d["likelihood"] for d in run(rows)["decisions"]] == [d["likelihood"] for d in first["decisions"]]
    assert first["metrics"]["likelihood"]["estimated"] is True


def test_only_pairs_on_which_nothing_disagrees_can_be_decided_together():
    from dataforge_matching.likelihood import agrees_throughout

    same_home = [
        {"field": "phone", "result": "exact", "similarity": 1.0, "strength": "strong", "explanation": "Exact phone match"},
        {"field": "name", "result": "different", "similarity": 0.75, "strength": "supporting", "explanation": "Name: first initial agrees, surname similarity 1.00"},
    ]
    assert agrees_throughout(same_home)  # an initial fits the full name
    # Alexander and A. (Andrew) Jones share a home; their own email addresses tell them apart.
    other_email = {"field": "email", "result": "different", "similarity": 0.0, "strength": "none", "explanation": "No shared email"}
    assert not agrees_throughout([*same_home, other_email])
    assert not agrees_throughout([{"field": "name", "result": "different", "similarity": 0.6, "strength": "supporting", "explanation": "Name similarity 0.60"}])
