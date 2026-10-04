"""Value cleanup: standardizing, flagging, junk rows, variant spellings, and applying a person's choices."""

from dataforge_matching import cleanup
from dataforge_matching.cleanup import company_key, fingerprint, standardize

ROLES = {"Name": "name", "Phone": "phone", "Email": "email", "Zip": "postal_code", "State": "region", "Company": "other", "Address": "address"}
ROWS = [
    {"Name": "SMITH, JOHN", "Phone": "512.555.0182", "Email": "John@GMIAL.com", "Zip": "2152", "State": "massachusetts", "Company": "Acme, Inc.", "Address": "12 MAIN STREET APARTMENT 4"},
    {"Name": "Ana Lopez", "Phone": "000-000-0000", "Email": "noemail@gmail.com", "Zip": "78701", "State": "TX", "Company": "ACME INC", "Address": "9 Oak Ave"},
    {"Name": "Ana Lopez", "Phone": "000-000-0000", "Email": "noemail@gmail.com", "Zip": "78701", "State": "TX", "Company": "ACME INC", "Address": "9 Oak Ave"},
    {"Name": "Test User", "Phone": "", "Email": "test@test.com", "Zip": "", "State": "", "Company": "Acme Incorporated", "Address": ""},
    {"Name": "N/A", "Phone": " ", "Email": "", "Zip": "#N/A", "State": "", "Company": "", "Address": ""},
    {"Name": "Lee Chan", "Phone": "555-01", "Email": "lee@@mail", "Zip": "78702", "State": "Texas", "Company": "Globex", "Address": "1 Elm St"},
]


def column(report, name):
    return next(c for c in report["columns"] if c["column"] == name)


def test_formats_are_standardized_per_column_role():
    assert standardize("SMITH, JOHN JR.", "name") == ("John Smith Jr.", None)
    assert standardize("512.555.0182", "phone") == ("(512) 555-0182", None)
    assert standardize("+44 20 7946 0018", "phone") == ("+44 20 7946 0018", None)
    assert standardize(" John@GMIAL.com ", "email") == ("john@gmail.com", None)
    assert standardize("2152", "postal_code") == ("02152", None)  # the leading zero Excel dropped
    assert standardize("massachusetts", "region") == ("MA", None)
    assert standardize("12 MAIN STREET APARTMENT 4", "address") == ("12 Main St Apt 4", None)
    assert standardize("Acme Holdings", "name", person=False) == ("Acme Holdings", None)


def test_invalid_and_placeholder_values_are_flagged_not_guessed():
    assert standardize("000-000-0000", "phone")[1] == "placeholder"
    assert standardize("555-01", "phone")[1] == "invalid"
    assert standardize("noemail@gmail.com", "email")[1] == "placeholder"
    assert standardize("lee@@mail", "email") == ("lee@@mail", "invalid")
    assert standardize("#N/A", "postal_code")[1] == "placeholder"
    assert standardize("1.23457E+11", "identifier")[1] == "invalid"  # Excel shortened the number; it cannot be recovered


def test_scan_reports_changes_problems_junk_rows_and_variant_spellings():
    report = cleanup.scan(ROWS, ROLES)
    phone = column(report, "Phone")
    assert phone["changes"] == 1 and phone["placeholders"] == 2 and phone["invalid"] == 1
    assert ["512.555.0182", "(512) 555-0182"] in phone["change_examples"]
    assert column(report, "Zip")["change_examples"] == [["2152", "02152"]]
    assert report["junk_rows"]["empty"] == 1 and report["junk_rows"]["exact_duplicates"] == 1 and report["junk_rows"]["test"] == 1
    acme = next(c for c in report["clusters"] if c["column"] == "Company")
    assert {v["value"] for v in acme["values"]} == {"Acme, Inc.", "ACME INC", "Acme Incorporated"}
    assert acme["suggested"] == "ACME INC" and acme["rows"] == 4  # the most common spelling


def test_apply_follows_the_plan_and_never_changes_the_input():
    before = [dict(row) for row in ROWS]
    plan = {
        "standardize": ["Name", "Phone", "Email", "Zip", "State"],
        "clear_invalid": ["Phone"],
        "merge_values": [{"column": "Company", "values": ["ACME INC", "Acme Incorporated", "Acme, Inc."], "to": "Acme Inc."}],
        "drop_rows": ["empty", "exact_duplicates", "test"],
    }
    kept, cleaned, summary = cleanup.apply(ROWS, ROLES, plan)
    assert ROWS == before
    assert kept == [0, 1, 5]
    assert cleaned[0] == {"Name": "John Smith", "Phone": "(512) 555-0182", "Email": "john@gmail.com", "Zip": "02152", "State": "MA", "Company": "Acme Inc.", "Address": "12 MAIN STREET APARTMENT 4"}
    assert cleaned[1]["Phone"] == "" and cleaned[2]["Phone"] == ""  # placeholder and invalid numbers cleared
    assert cleaned[2]["Email"] == "lee@@mail"  # invalid but not chosen for clearing: kept as typed
    assert summary["dropped"] == {"empty": 1, "test": 1, "exact_duplicates": 1}
    assert summary["merged"] == {"Company": 2}


def test_value_keys_ignore_case_order_punctuation_and_legal_form():
    assert fingerprint("Acme, Inc.") == fingerprint("inc ACME") == "acme inc"
    assert company_key("ACME Incorporated") == company_key("The Acme Co.") == "acme"
    assert fingerprint("Café Rio") == fingerprint("cafe rio")
