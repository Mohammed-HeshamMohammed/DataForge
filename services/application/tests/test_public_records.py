from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from dataforge_application import public_records, sources
from dataforge_application.api import Service
from test_workflows import call, ok, service, wait  # noqa: F401 - shared fixture and helpers

ACCOUNT_HEADER = public_records.ACCOUNT_FIELDS


def write_zip(path: Path, files: dict[str, list[list[str]]]) -> None:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, rows in files.items():
            bundle.writestr(name, "\r\n".join("\t".join(row) for row in rows) + "\r\n")


def account(acct: str, site: str, zip_code: str, mail: str, mail_state: str, owner: str, value: str, since: str) -> list[str]:
    row = dict.fromkeys(ACCOUNT_HEADER, "")
    row.update(acct=acct, mailto=owner, mail_addr_1=mail, mail_city="HOUSTON", mail_state=mail_state, mail_zip=zip_code, site_addr_1=site,
               site_addr_2="HOUSTON", site_addr_3=zip_code, state_class="A1", Neighborhood_Code="8001", tot_mkt_val=value,
               prior_tot_mkt_val="200000", new_own_dt=since, land_ar="5000")
    return [row[name] for name in ACCOUNT_HEADER]


@pytest.fixture()
def hcad_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "hcad"
    folder.mkdir()
    write_zip(folder / "Real_acct_owner.zip", {
        "real_acct.txt": [ACCOUNT_HEADER,
                          account("0001", "100 MAIN ST", "77009", "100 MAIN ST", "TX", "JANE SMITH", "250000", "05/01/2001"),
                          account("0002", "200 OAK LN", "77009", "PO BOX 5", "TX", "OAK HOLDINGS LLC", "300000", "01/15/2020"),
                          account("0003", "300 ELM ST", "77009", "9 BAY RD", "FL", "SMITH FAMILY TRUST", "400000", "03/10/1995"),
                          account("0004", "400 PINE ST", "77018", "77 OTHER ST", "TX", "JOHN DOE", "500000", "07/04/2010")],
        "real_neighborhood_code.txt": [["cd", "grp_cd", "dscr"], ["8001", "1E", "NORTHSIDE"]],
    })
    write_zip(folder / "Real_building_land.zip", {
        "building_res.txt": [["acct", "bld_num", "date_erected", "heat_ar", "dscr"], ["0001", "1", "1950", "1400", "Average"], ["0003", "1", "1975", "2100", "Good"]],
        "fixtures.txt": [["acct", "bld_num", "type", "units"], ["0001", "1", "RMB", "3.00"], ["0001", "1", "RMF", "2.00"]],
    })
    write_zip(folder / "Real_jur_exempt.zip", {"jur_exempt_cd.txt": [["acct", "exempt_cat"], ["0001", "RES OVR"], ["0004", "RES"]]})
    write_zip(folder / "Code_description_real.zip", {"desc_r_01_state_class.txt": [["Code", "Dept", "Description"], ["A1", "A1", "Real, Residential, Single-Family"]]})
    return folder


def test_hcad_job_filters_the_roll_into_a_dataset_without_sensitive_exemptions(service: Service, hcad_folder: Path) -> None:
    base = {"policy_acknowledgement": True, "purpose": "internal_analysis", "data_dir": str(hcad_folder)}
    assert "at least one filter" in call(service, "records.hcad", **base)["error"]["message"]
    assert "authorized" in call(service, "records.hcad", **{**base, "policy_acknowledgement": False, "zip": ["77009"]})["error"]["message"]
    job = wait(service, ok(service, "records.hcad", **base, zip=["77009"], absentee=True)["job_id"])
    assert job["state"] == "completed", job
    assert job["result"]["rows"] == 1  # 0002 mails to a PO box, 0001 lives there, 0004 is in another ZIP
    rows = [row["raw"] for row in ok(service, "dataset.rows", dataset_id=job["result"]["dataset_id"])]
    assert rows[0]["Address"] == "300 Elm St" and rows[0]["Owner type"] == "Trust" and rows[0]["Out of state owner"] == "Yes"
    assert rows[0]["Year built"] == "1975" and rows[0]["Homestead"] == "No" and rows[0]["Neighborhood"] == "Northside"
    everyone = wait(service, ok(service, "records.hcad", **base, zip=["77009"])["job_id"])
    raw = [row["raw"] for row in ok(service, "dataset.rows", dataset_id=everyone["result"]["dataset_id"])]
    first = next(row for row in raw if row["HCAD account"] == "0001")
    assert first["Homestead"] == "Yes" and first["Bedrooms"] == "3" and first["Absentee owner"] == "No"
    assert not any("Over" in str(value) or "OVR" in str(value) for row in raw for value in row.values())


def test_tax_sale_job_checks_the_site_and_stages_the_list(service: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    listing = {"Sale date": "2026-10-06", "Address": "4114 E Toliver St", "City": "Houston", "Zip": "77016", "Minimum bid": "18088.24",
               "Adjudged value": "64263.00", "HCAD account": "0720160010046"}
    monkeypatch.setattr(public_records, "tax_sales", lambda fetcher: [dict(listing)])
    monkeypatch.setattr(sources, "check_navigation", lambda store, url, purpose: {"allowed": True, "reason": None, "skippable": False})
    job = wait(service, ok(service, "records.tax_sales", policy_acknowledgement=True, purpose="internal_analysis")["job_id"])
    assert job["state"] == "completed", job
    assert job["result"]["rows"] == 1
    dataset = next(d for d in ok(service, "dataset.list") if d["id"] == job["result"]["dataset_id"])
    assert dataset["name"] == "Harris County tax sale 2026-10-06 (1 properties)"

    monkeypatch.setattr(sources, "check_navigation", lambda store, url, purpose: {"allowed": False, "reason": "robots.txt disallows this URL", "skippable": True})
    refused = wait(service, ok(service, "records.tax_sales", policy_acknowledgement=True, purpose="internal_analysis")["job_id"])
    assert refused["state"] == "failed" and "robots.txt" in str(refused["error"])


def test_foreclosure_job_validates_the_sale_months(service: Service) -> None:
    base = {"policy_acknowledgement": True, "purpose": "internal_analysis"}
    assert "2026-11" in call(service, "records.foreclosures", **base, **{"from": "November"})["error"]["message"]
    assert "between 1 and 12" in call(service, "records.foreclosures", **base, **{"from": "2026-01", "to": "2027-06"})["error"]["message"]
