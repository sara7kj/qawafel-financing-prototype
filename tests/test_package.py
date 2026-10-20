"""JSON package and issues CSV: same values as the computed results, N/A with reasons, BLOCK, R-13."""

import csv
import io
import json
import re

import pytest

from conftest import FIXTURES, SAMPLE
from financing.financing import estimate_financing
from financing.metrics import format_sar
from financing.package import ISSUE_COLUMNS, analyse, build_package, issue_rows, issues_csv, package_json

EXPECTED = FIXTURES / "expected"
WORKED = json.loads((EXPECTED / "worked_example_expected.json").read_text(encoding="utf-8"))
CASES = json.loads((EXPECTED / "cases_expected.json").read_text(encoding="utf-8"))
AMOUNT_KEYS = {"total", "value", "amount", "open_balance", "eligible_receivables",
               "known_recorded_amount_excluded"}
AMOUNT = re.compile(r"-?\d+\.\d{2}")


def package_for(path, rate=70):
    validation, metrics = analyse(path)
    package = build_package(validation, metrics, estimate_financing(metrics, rate), source_name=path.name)
    return validation, metrics, json.loads(package_json(package))  # through real JSON text


def walk_amounts(node, path=""):
    """Yield every amount-like string so its format can be checked."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("na_reasons", "reason"):
                continue  # reason texts, not amounts
            if key == "monthly" and isinstance(value, dict):
                yield from ((f"{path}.{m}", v) for m, v in value.items())
            elif key in AMOUNT_KEYS and (value is None or isinstance(value, str)) and "ratio" not in path:
                yield f"{path}.{key}", value
            else:
                yield from walk_amounts(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from walk_amounts(item, f"{path}[{i}]")


# ---------------------------------------------------------------- worked example

@pytest.fixture(scope="module")
def worked():
    return package_for(FIXTURES / WORKED["file"])


def test_worked_example_package_matches_expected(worked):
    _, _, pkg = worked
    ind = pkg["indicators"]
    for name, key in (("gross_sales", "gross_sales"), ("credit_notes", "credit_notes"), ("net_sales", "net_sales"),
                      ("collections", "collections_from_accepted_records_only")):
        exp = WORKED[key]
        assert ind[name]["total"] == exp["total"]
        assert {m: v for m, v in ind[name]["monthly"].items() if v != "0.00"} == exp["monthly"]
        assert len(ind[name]["monthly"]) == 12
    assert ind["credit_note_ratio"]["value"] == WORKED["credit_note_ratio"]["display"]
    aging = ind["receivables"]["aging"]
    assert aging == {k: v for k, v in WORKED["aging"].items() if k != "total"}
    assert ind["receivables"]["total"] == WORKED["aging"]["total"]
    assert {i["invoice_id"]: (i["open_balance"], i["days_overdue"], i["bucket"])
            for i in ind["receivables"]["open_invoices"]} == \
        {k: (v["open"], v["days_overdue"], v["bucket"]) for k, v in WORKED["open_balances"].items()}
    fin = pkg["financing_estimate"]
    assert (fin["value"], fin["eligible_receivables"], fin["advance_rate"]) == ("8988.27", "12840.38", "0.7000")
    for key in ("sales_concentration", "receivables_concentration"):
        assert {e["customer_id"]: (e["amount"], e["share"]) for e in ind[key]["top"]} == \
            {c: (v["amount"], v["display"]) for c, v in WORKED[key].items()}
    r20 = sorted(f"{'sales' if 'net sales' in w['message'] else 'receivables'}:{w['record_id']}"
                 for w in pkg["warnings"] if w["rule"] == "R-20")
    assert r20 == sorted(WORKED["warnings"]["R-20"])
    assert pkg["data_quality"]["receivables_quality_status"]["value"] == "no_known_issues"
    assert pkg["data_quality"]["source_completeness_verified"] is False
    assert pkg["valuation_date"] == "2026-09-30"
    assert list(ind["receivables"]["aging"]) == ["current", "1-30", "31-60", "61-90", ">90"]


def test_known_sample_is_marked_synthetic_with_its_applicant_id():
    validation, metrics = analyse(SAMPLE / "sample_clean.xlsx")
    pkg = build_package(validation, metrics, estimate_financing(metrics), applicant_id="APP-DEMO-001",
                        source_name="sample_clean.xlsx", known_synthetic_sample=True)
    assert (pkg["synthetic_data"], pkg["applicant_id"], pkg["source_file"]) == (True, "APP-DEMO-001",
                                                                                 "sample_clean.xlsx")
    assert pkg["disclaimers"][0].startswith("All data is synthetic")


def test_uploaded_file_is_not_claimed_synthetic_and_has_no_applicant_id():
    validation, metrics = analyse(SAMPLE / "sample_clean.xlsx")
    pkg = build_package(validation, metrics, estimate_financing(metrics), source_name="my_upload.xlsx")
    assert pkg["synthetic_data"] is None and "cannot be proven synthetic" in pkg["synthetic_data_note"]
    assert pkg["applicant_id"] is None and pkg["applicant_id_note"] == "not provided"
    assert pkg["source_file"] == "my_upload.xlsx"
    assert "All data is synthetic" not in package_json(pkg)
    assert "cannot be proven synthetic" in pkg["disclaimers"][0]


def test_package_values_come_from_the_computed_results(worked):
    _, metrics, pkg = worked
    for name in ("gross_sales", "credit_notes", "net_sales", "collections"):
        series = getattr(metrics, name)
        assert pkg["indicators"][name]["monthly"] == {m: format_sar(v) for m, v in series.monthly.items()}
        assert pkg["indicators"][name]["scope"]["record_counts"] == series.scope.record_counts


def test_amounts_are_two_decimal_strings_or_null(worked):
    for path in [FIXTURES / WORKED["file"], SAMPLE / "sample_defects.xlsx", FIXTURES / "cases" / "TC-09.xlsx"]:
        _, _, pkg = package_for(path)
        for where, value in walk_amounts(pkg["indicators"]) :
            assert value is None or AMOUNT.fullmatch(value), (path.name, where, value)


@pytest.mark.parametrize("rate, value", [(0, "0.00"), (100, "12840.38")])
def test_package_follows_the_selected_rate(rate, value):
    _, _, pkg = package_for(FIXTURES / WORKED["file"], rate)
    assert pkg["financing_estimate"]["value"] == value
    assert pkg["financing_estimate"]["advance_rate_percent"] == rate


# ---------------------------------------------------------------- BLOCK and N/A

@pytest.mark.parametrize("case_id", ["TC-01", "TC-02", "TC-03"])
def test_blocked_package_has_reasons_and_no_financial_indicators(case_id):
    _, _, pkg = package_for(FIXTURES / CASES[case_id]["file"])
    assert pkg["status"]["blocked"] is True
    assert any(r.startswith(CASES[case_id]["block"]) for r in pkg["status"]["block_reasons"])
    assert pkg["indicators"] is None and pkg["indicators_reason"]
    assert pkg["financing_estimate"]["value"] is None
    assert pkg["financing_estimate"]["eligible_receivables"] is None
    assert pkg["financing_estimate"]["reason"] == "analysis is blocked"


def test_na_values_are_null_with_reasons():
    _, _, pkg = package_for(FIXTURES / "cases" / "TC-09.xlsx")
    ind = pkg["indicators"]
    assert ind["gross_sales"]["total"] == "3000.00"
    assert ind["net_sales"]["monthly"]["2026-08"] is None and ind["net_sales"]["na_reasons"]["2026-08"]
    assert ind["net_sales"]["monthly"]["2026-09"] == "2000.00"
    assert ind["net_sales"]["total"] is None and ind["net_sales"]["na_reasons"]["total"]
    assert ind["credit_note_ratio"]["value"] is None and ind["credit_note_ratio"]["reason"]
    assert ind["sales_concentration"]["top"] is None and ind["sales_concentration"]["reason"]


def test_empty_receivables_scope_and_unresolved_links():
    _, _, tc07 = package_for(FIXTURES / "cases" / "TC-07.xlsx")
    assert tc07["indicators"]["receivables"]["aging"] is None
    assert tc07["indicators"]["receivables"]["reason"] == "receivables scope is empty"
    assert tc07["financing_estimate"]["value"] is None and tc07["financing_estimate"]["reason"]

    _, _, tc08 = package_for(FIXTURES / "cases" / "TC-08.xlsx")
    assert tc08["indicators"]["receivables"]["total"] == "3450.00"
    assert tc08["financing_estimate"]["value"] is None
    assert tc08["financing_estimate"]["eligible_receivables"] == "3450.00"
    assert "unresolved_links" in tc08["financing_estimate"]["reason"]
    assert tc08["data_quality"]["receivables_quality_status"]["value"] == "unresolved_links"


def test_true_zero_is_exported_as_zero_not_null():
    _, _, pkg = package_for(FIXTURES / "cases" / "TC-15.xlsx")
    assert pkg["indicators"]["receivables"]["total"] == "0.00"
    assert pkg["financing_estimate"]["value"] == "0.00"
    assert pkg["indicators"]["receivables_concentration"]["top"] is None


# ---------------------------------------------------------------- R-13, dependents and the CSV

@pytest.fixture(scope="module")
def defects():
    return package_for(SAMPLE / "sample_defects.xlsx")


def test_r13_is_exported_with_its_cause(defects):
    validation, metrics, pkg = defects
    [entry] = pkg["data_quality"]["receivable_unreliable_invoices"]
    assert entry["invoice_id"] == "INV-D11"
    assert [(r["rule"], r["source_sheet"], r["source_record_id"]) for r in entry["reasons"]] == \
        [("R-13", "invoices", "INV-D11")]
    rows = [r for r in pkg["data_quality"]["issues"] if r["class"] == "RECEIVABLE UNRELIABLE"]
    assert [(r["rule"], r["affected_invoice"]) for r in rows] == [("R-13", "INV-D11")]


def test_dependents_and_excluded_amounts_are_exported(defects):
    _, _, pkg = defects
    dq = pkg["data_quality"]
    dependent = [r for r in dq["issues"] if r["class"] == "DEPENDENT"]
    assert [(r["record_id"], r["affected_invoice"], r["rule"]) for r in dependent] == [("PAY-D03", "INV-D03", "R-03")]
    assert dq["dependent_payments_d13"]["records"] == 1 and dq["dependent_payments_d13"]["unknown_currency"] == 1
    assert dq["excluded_records"]["invoices"]["known_recorded_amount_excluded"] == "6900.02"
    assert dq["record_counts_by_status"]["invoices"]["quarantined"] == 10


def test_issues_csv_matches_the_issue_table(defects):
    validation, metrics, _ = defects
    rows = list(csv.DictReader(io.StringIO(issues_csv(validation, metrics))))
    assert list(rows[0].keys()) == ISSUE_COLUMNS
    assert len(rows) == len(issue_rows(validation, metrics))
    assert {"QUARANTINE", "OUT OF PERIOD", "WARN", "DEPENDENT", "RECEIVABLE UNRELIABLE"} <= {r["class"] for r in rows}
    assert any(r["rule"] == "R-13" and r["affected_invoice"] == "INV-D11" for r in rows)


def test_blocked_issues_csv_lists_the_block_reason():
    validation, metrics, _ = package_for(FIXTURES / "cases" / "TC-02.xlsx")
    rows = list(csv.DictReader(io.StringIO(issues_csv(validation, metrics))))
    assert [(r["class"], r["rule"], r["column"]) for r in rows] == [("BLOCK", "S-02", "due_date")]
