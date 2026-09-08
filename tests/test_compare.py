"""Record comparison semantics — the rules that keep precision honest."""

from __future__ import annotations

import pandas as pd

from dataforge.core.compare import address_similarity, has_strong_evidence, role_similarity
from dataforge.core.normalize import detect_roles, normalize_address, normalize_frame


def _normalized(records: list[dict[str, str]]):
    frame = pd.DataFrame(records)
    roles = detect_roles(list(frame.columns))
    return normalize_frame(frame, roles), roles


def test_house_numbers_must_match_exactly():
    # One character apart, but two different houses on the same street.
    assert address_similarity("144 e 68th st", "140 e 68th st") == 0.0
    assert (
        address_similarity("4343 martin luther king jr blvd", "4333 martin luther king jr blvd")
        == 0.0
    )


def test_same_house_number_compares_the_street_fuzzily():
    # Suffixes are canonicalised during normalisation, so compare normalised input.
    assert (
        address_similarity(normalize_address("123 Main St"), normalize_address("123 Main Street"))
        == 1.0
    )
    assert address_similarity("123 main st", "123 main str") > 0.8


def test_addresses_without_house_numbers_fall_back_to_fuzzy():
    assert address_similarity("po box 55", "po box 55") == 1.0


def test_phone_columns_compare_as_an_interchangeable_set():
    frame, roles = _normalized(
        [
            {"Phone 1": "(520) 555-0142", "Phone 2": "520-555-9911"},
            {"Phone 1": "520-555-9911", "Phone 2": "(520) 555-0142"},
        ]
    )
    # Primary and alternate are swapped; the pair must still match.
    assert role_similarity(frame, roles, 0, 1, "phone") == 1.0


def test_address_columns_compare_positionally_not_as_a_bag():
    # Two different properties that share a managing agent's mailing address.
    frame, roles = _normalized(
        [
            {"Address": "4423 Wesley Ave", "Owner Mailing Address": "200 N Spring St"},
            {"Address": "872 W Vernon Ave", "Owner Mailing Address": "200 N Spring St"},
        ]
    )
    # Bagging both columns together would score this near 1.0.
    assert role_similarity(frame, roles, 0, 1, "address") < 0.6


def test_blank_role_yields_no_evidence_rather_than_disagreement():
    frame, roles = _normalized(
        [{"Email 1": "", "Address": "1 A St"}, {"Email 1": "", "Address": "1 A St"}]
    )
    assert role_similarity(frame, roles, 0, 1, "email") is None


def test_shared_name_and_city_alone_are_not_strong_evidence():
    frame, roles = _normalized(
        [
            {"Address": "4423 Wesley Ave", "City": "Los Angeles", "Owner Last Name": "Angeles"},
            {"Address": "872 W Vernon Ave", "City": "Los Angeles", "Owner Last Name": "Angeles"},
        ]
    )
    assert not has_strong_evidence(frame, roles, 0, 1)


def test_an_exact_phone_is_strong_evidence():
    frame, roles = _normalized(
        [
            {"Address": "1 A St", "Phone 1": "5205550142"},
            {"Address": "999 Z Rd", "Phone 1": "(520) 555-0142"},
        ]
    )
    assert has_strong_evidence(frame, roles, 0, 1)


def test_an_identical_address_is_strong_evidence():
    frame, roles = _normalized([{"Address": "123 Main St"}, {"Address": "123 Main Street"}])
    assert has_strong_evidence(frame, roles, 0, 1)
