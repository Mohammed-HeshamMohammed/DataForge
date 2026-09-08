"""Normalisation and column role detection."""

from __future__ import annotations

from dataforge.core.normalize import (
    detect_roles,
    normalize_address,
    normalize_email,
    normalize_frame,
    normalize_phone,
    normalize_text,
)


def test_normalize_text_strips_punctuation_and_case():
    assert normalize_text("  123 Main St.  ") == "123 main st"
    assert normalize_text(None) == ""


def test_normalize_phone_keeps_digits_and_drops_country_code():
    assert normalize_phone("(520) 555-0142") == "5205550142"
    assert normalize_phone("1-520-555-0142") == "5205550142"
    assert normalize_phone("") == ""


def test_normalize_email_lowercases_and_rejects_malformed():
    assert normalize_email("  SAM@Example.com ") == "sam@example.com"
    assert normalize_email("not-an-email") == ""


def test_detect_roles_assigns_each_column_once(leads_frame):
    roles = detect_roles(list(leads_frame.columns))

    assert "Phone 1" in roles.phone and "Phone 2" in roles.phone
    assert "Email 1" in roles.email
    assert "Owner 1 First Name" in roles.name
    assert "Address" in roles.address

    # No column may be claimed by two roles.
    all_columns = roles.all_columns()
    assert len(all_columns) == len(set(all_columns)) == len(leads_frame.columns)


def test_normalize_frame_applies_the_role_specific_normalizer(leads_frame):
    roles = detect_roles(list(leads_frame.columns))
    normalized = normalize_frame(leads_frame, roles)

    # Phones keep digits rather than being stripped to letters-and-spaces.
    assert normalized.at[0, "Phone 1"] == "5205550142"
    # Emails keep their '@', which the original blanket normaliser destroyed.
    assert normalized.at[4, "Email 1"] == "sam@example.com"


def test_normalize_address_canonicalises_suffixes_and_directionals():
    assert normalize_address("831 W 60TH ST") == "831 w 60th st"
    assert normalize_address("831 West 60th Street") == "831 w 60th st"
    assert normalize_address("77 Oak Avenue") == normalize_address("77 Oak Ave.")


def test_region_and_address_are_separate_roles():
    roles = detect_roles(["Address", "City", "State", "Zip", "Owner Mailing Address"])
    assert roles.address == ["Address", "Owner Mailing Address"]
    assert roles.region == ["City", "State", "Zip"]


def test_lookalike_columns_are_not_misclassified():
    roles = detect_roles(["Units Count", "Subdivision", "Ownership Length (Months)", "Bedrooms"])
    # "Units Count" must not read as "County", nor "Subdivision" as an id.
    assert roles.other == ["Units Count", "Subdivision", "Ownership Length (Months)", "Bedrooms"]
