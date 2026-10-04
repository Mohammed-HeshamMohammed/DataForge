"""People-aware matching: nicknames, initials, name order, households, mailbox aliases, and candidate coverage."""

from dataforge_matching import engine
from dataforge_matching.normalize import email, given_relation, person_name, phone, soundex

CONTACTS = {"Name": "name", "Phone": "phone", "Email": "email", "Address": "address", "City": "city", "Zip": "postal_code"}


def run(rows, mapping=CONTACTS, entity_type="person", **settings):
    return engine.run({
        "schema_version": 1, "entity_type": entity_type, "mapping": mapping, "settings": settings, "constraints": [],
        "rows": [{"id": f"r{i}", "row_number": i, "raw": raw} for i, raw in enumerate(rows, start=2)],
    })


def decision(result, left, right):
    return next((d for d in result["decisions"] if {d["left_row_id"], d["right_row_id"]} == {left, right}), None)


def grouped(result, *row_ids):
    return any(set(row_ids) <= set(c["member_row_ids"]) for c in result["clusters"])


def test_gmail_dots_and_plus_tags_reach_the_same_mailbox():
    assert email("Jane.Doe+news@gmail.com") == email("janedoe@googlemail.com") == "janedoe@gmail.com"
    assert email("jane+work@outlook.com") == "jane@outlook.com"
    assert email("jane.doe+x@example.org") == "jane.doe+x@example.org"  # unknown providers keep the address as written


def test_names_are_read_in_either_order_without_titles_or_suffixes():
    assert person_name("Smith, John Jr.") == (("john",), "smith")
    assert person_name("Dr. Jane Doe") == (("jane",), "doe")
    assert person_name("V. Scott") == (("v",), "scott")  # a leading V. is an initial, not "the fifth"
    assert person_name("Maria de la Cruz") == (("maria",), "cruz")
    assert person_name("Cher") is None


def test_given_names_relate_through_nicknames_initials_and_typos():
    assert given_relation(("margaret",), ("peggy",)) == "nickname"
    assert given_relation(("chris",), ("christopher",)) == "nickname"
    assert given_relation(("b",), ("robert",)) == "initial"  # Bob
    assert given_relation(("jennifer",), ("jenifer",)) == "similar"
    assert given_relation(("j", "robert"), ("robert",)) == "same"  # known by a middle name
    assert given_relation(("susan",), ("samantha",)) == "different"
    assert given_relation(("samuel",), ("samantha",)) == "different"  # both are "Sam", but not each other


def test_nickname_with_shared_contact_details_merges_automatically():
    result = run([
        {"Name": "Margaret Perez", "Phone": "(745) 570-3084", "Email": "margaret.perez@example.org", "Address": "3975 Main Blvd", "City": "Seattle", "Zip": "98168"},
        {"Name": "Peggy Perez", "Phone": "745.570.3084", "Email": "margaret.perez@example.org", "Address": "3975 Main Boulevard", "City": "Seattle", "Zip": "98168"},
    ])
    assert decision(result, "r2", "r3")["decision"] == "match"


def test_household_members_sharing_phone_and_address_are_kept_apart():
    result = run([
        {"Name": "Susan Rodriguez", "Phone": "430-365-8070", "Address": "3607 Lincoln Street", "City": "Portland", "Zip": "97295"},
        {"Name": "Samantha Rodriguez", "Phone": "(430) 365-8070", "Address": "3607 Lincoln St", "City": "Portland", "Zip": "97295"},
    ])
    d = decision(result, "r2", "r3")
    assert d["decision"] == "non_match" and "Different first names" in d["reason"]
    assert result["metrics"]["guard_reasons"] == {"Different first names": 1}


def test_different_first_names_with_a_shared_email_go_to_review():
    result = run([
        {"Name": "Daniel Hill", "Email": "hill.family@example.org", "City": "Atlanta", "Zip": "30323"},
        {"Name": "Wendy Hill", "Email": "hill.family@example.org", "City": "Atlanta", "Zip": "30323"},
    ])
    assert decision(result, "r2", "r3")["decision"] == "possible_match"


def test_an_ambiguous_record_never_bridges_two_different_people_into_one_group():
    result = run([
        {"Name": "Susan Rodriguez", "Phone": "4303658070", "Email": "sr@example.org", "Address": "3607 Lincoln St", "City": "Portland"},
        {"Name": "S. Rodriguez", "Phone": "4303658070", "Email": "sr@example.org", "Address": "3607 Lincoln St", "City": "Portland"},
        {"Name": "Samantha Rodriguez", "Phone": "4303658070", "Email": "sr@example.org", "Address": "3607 Lincoln St", "City": "Portland"},
    ], strictness="balanced")
    assert not grouped(result, "r2", "r4")


def test_an_initial_alone_with_a_shared_phone_needs_review_when_safer():
    rows = [
        {"Name": "Grace Hopper", "Phone": "(212) 555-0100", "Address": "1 Navy Way", "Zip": "10001"},
        {"Name": "G. Hopper", "Phone": "(212) 555-0100"},
    ]
    assert decision(run(rows), "r2", "r3")["decision"] == "possible_match"


def test_records_with_only_a_city_or_only_a_zip_are_still_compared():
    result = run([
        {"Name": "Barbara Rodriguez", "Address": "8929 Jackson Ln", "City": "Miami"},
        {"Name": "Barbara Rodriguez", "Phone": "(840) 291-6593", "Address": "8929 Jackson Lane", "City": "Miami", "Zip": "33124"},
    ])
    assert decision(result, "r2", "r3") is not None


def test_sparse_records_are_compared_by_a_surname_that_sounds_alike_and_a_nickname_initial():
    assert soundex("Harris") == soundex("Haris") == "H620"
    result = run([
        {"Name": "Matthew Harris", "Phone": "(293) 356-2553", "Email": "mharris61@gmail.com", "City": "Phoenix", "Zip": "85069"},
        {"Name": "Matt Haris", "City": "Phoenix", "Zip": "85069"},
        {"Name": "B. Jackson", "City": "Phoenix", "Zip": "85032"},
        {"Name": "Robert Jackson", "Phone": "(722) 927-2371", "City": "Phoenix", "Zip": "85032"},
    ])
    assert decision(result, "r2", "r3") is not None
    assert decision(result, "r4", "r5") is not None


def test_companies_are_compared_by_whole_name_without_first_name_rules():
    mapping = {"Company": "name", "Phone": "phone"}
    result = run([{"Company": "Acme Holdings", "Phone": "5125550182"}, {"Company": "Zenith Holdings", "Phone": "5125550182"}], mapping, entity_type="business")
    assert all(e["strength"] != "guard" for e in decision(result, "r2", "r3")["evidence"])


def test_plain_us_phone_numbers_normalize_like_the_full_parser():
    for raw in ("(512) 555-0182", "512.555.0182", "+1 512 555 0182", "1-512-555-0182", "5125550182"):
        assert phone(raw) == "+15125550182"
    assert phone("+44 20 7946 0018") == "+442079460018"
