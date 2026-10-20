"""Validation behaviour against the hand-written expected results, plus targeted rule tests.

Expected results come from tests/fixtures/expected/*.json (written before this code).
Only the validation-stage fields are checked here; indicator values belong to metrics.
"""

import json
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from conftest import FIXTURES, SAMPLE, write_raw_workbook
from financing.loader import load_workbook_file
from financing.schema import RECORDED_AMOUNT_COLUMN, RecordStatus, Severity
from financing.validation import validate

EXPECTED = FIXTURES / "expected"
CASES = json.loads((EXPECTED / "cases_expected.json").read_text(encoding="utf-8"))


def halalas(text: str) -> int:
    return int(Decimal(text) * 100)


def run_file(path):
    return validate(load_workbook_file(path))


def parse_record_ref(text: str) -> tuple[str, str | None]:
    """'payments:PAY-T1 row 2' -> ('payments', 'PAY-T1'); '<missing id>' -> None."""
    sheet, rest = text.split(":", 1)
    return sheet, None if rest.startswith("<missing id>") else rest.split(" ")[0]


def records_with(result, status):
    return [r for sheet in result.records for r in result.with_status(sheet, status)]


def quarantined(result) -> Counter:
    return Counter((r.record.sheet, r.record.record_id, rule)
                   for r in records_with(result, RecordStatus.QUARANTINED) for rule in r.rules)


def out_of_period(result) -> Counter:
    found = Counter()
    for r in records_with(result, RecordStatus.OUT_OF_PERIOD):
        amount = r.record.values.get(RECORDED_AMOUNT_COLUMN[r.record.sheet])
        found[(r.record.sheet, r.record.record_id, r.rules[0], amount)] += 1
    return found


def warnings(result, rule):
    return [i for i in result.issues if i.rule == rule and i.severity is Severity.WARN]


def assert_series(reliability, expected_series):
    monthly = expected_series["monthly"]
    if monthly == "ALL_NA":
        assert reliability.all_months
    else:
        assert not reliability.all_months
        assert reliability.affected_months == {m for m, v in monthly.items() if v is None}
    assert reliability.reliable == (expected_series["total"] is not None)


# ---------------------------------------------------------------- Dataset B

@pytest.mark.parametrize("case_id", sorted(k for k in CASES if k.startswith("TC-")))
def test_case_validation_matches_expected(case_id):
    exp = CASES[case_id]
    result = run_file(FIXTURES / exp["file"])

    if exp["block"]:
        assert result.blocked
        assert exp["block"] in {i.rule for i in result.issues if i.severity is Severity.BLOCK}
        if "block_detail" in exp:
            sheet, column = exp["block_detail"].split(".")
            assert any(i.rule == "S-02" and i.sheet == sheet and i.column == column for i in result.issues)
        for rule in exp["rules"]:
            assert result.issues_for(rule), rule
        return

    assert not result.blocked
    assert quarantined(result) == Counter(
        (*parse_record_ref(q["record"]), q["rule"]) for q in exp["quarantined"])
    assert out_of_period(result) == Counter(
        (*parse_record_ref(o["record"]), o["rule"], halalas(o["amount"])) for o in exp["out_of_period"])
    assert result.receivables_quality.value == exp["receivables_quality_status"]
    assert sorted(result.unreliable_rules()) == sorted(exp["receivable_unreliable_invoices"])
    assert_series(result.collections_reliability, exp["collections"])
    assert_series(result.credit_note_reliability, exp["credit_notes"])
    assert_series(result.credit_note_reliability, exp["net_sales"])  # net sales share the credit-note months

    for sheet, summary in result.excluded_amounts.items():
        expected = exp["excluded_amounts"].get(sheet)
        if expected is None:
            assert summary.records == 0, sheet
            continue
        assert summary.known_recorded_halalas == halalas(expected["known_recorded_amount_excluded"])
        assert summary.unreadable_or_not_positive == expected["unreadable_or_not_positive"]
        assert summary.duplicate_id == expected["duplicate_id"]
        assert summary.non_sar == expected["non_sar"]
        assert summary.unknown_currency == expected["unknown_currency"]

    assert len(warnings(result, "R-19")) == 10  # background: 2025-10 .. 2026-07
    assert len(warnings(result, "R-21")) == 1
    assert result.source_completeness_verified is False


def test_tc03_invoices_are_out_of_period_before_s03():
    result = run_file(FIXTURES / "cases" / "TC-03.xlsx")
    assert out_of_period(result) == Counter({("invoices", "INV-T1", "R-14", 115000): 1,
                                             ("invoices", "INV-T2", "R-14", 230000): 1})


def test_tc18_credit_note_after_vd_does_not_touch_valuation_or_sales():
    result = run_file(FIXTURES / "cases" / "TC-18.xlsx")
    assert result.accepted("credit_notes") == []           # not applied to the VD balance or RP sales
    assert result.with_status("credit_notes", RecordStatus.OUT_OF_PERIOD)[0].rules == ["R-15"]
    assert result.receivable_unreliable == {}
    assert result.credit_note_reliability.reliable
    assert quarantined(result) == Counter()


def test_quarantine_issue_names_the_affected_invoice():
    result = run_file(FIXTURES / "cases" / "TC-04.xlsx")
    issue = next(i for i in result.issues if i.rule == "R-10")
    assert (issue.sheet, issue.row, issue.affected_invoice) == ("payments", 2, "INV-T1")


# ---------------------------------------------------------------- worked example

def test_worked_example_validation():
    exp = json.loads((EXPECTED / "worked_example_expected.json").read_text(encoding="utf-8"))
    result = run_file(FIXTURES / exp["file"])
    assert not result.blocked
    assert quarantined(result) == Counter()
    assert out_of_period(result) == Counter(
        (*parse_record_ref(o["record"]), o["rule"], halalas(o["amount"])) for o in exp["out_of_period"])
    assert result.receivables_quality.value == exp["receivables_quality_status"]
    assert result.collections_reliability.reliable and result.credit_note_reliability.reliable
    assert sorted(i.message.split()[4].rstrip(";") for i in warnings(result, "R-19")) == \
        exp["warnings"]["R-19_empty_months"]


# ---------------------------------------------------------------- Dataset A

@pytest.fixture(scope="module")
def clean_result():
    return run_file(SAMPLE / "sample_clean.xlsx")


@pytest.fixture(scope="module")
def defect_result():
    return run_file(SAMPLE / "sample_defects.xlsx")


def test_clean_sample_has_no_defects(clean_result):
    report = json.loads((SAMPLE / "generation_report.json").read_text(encoding="utf-8"))
    assert not clean_result.blocked
    assert quarantined(clean_result) == Counter()
    assert records_with(clean_result, RecordStatus.DEPENDENT) == []
    assert clean_result.receivable_unreliable == {}
    assert clean_result.receivables_quality.value == "no_known_issues"
    oop = records_with(clean_result, RecordStatus.OUT_OF_PERIOD)
    assert {(r.record.sheet, r.rules[0]) for r in oop} == {("payments", "R-15")}
    assert len(oop) == report["after_vd_records"]["payments"]
    assert not any(warnings(clean_result, rule) for rule in ("R-17", "R-18", "R-19"))
    assert clean_result.collections_reliability.reliable and clean_result.credit_note_reliability.reliable


def test_defect_sample_matches_expected_additions(clean_result, defect_result):
    exp = json.loads((EXPECTED / "sample_defects_expected.json").read_text(encoding="utf-8"))
    expected_q = Counter()
    for sheet, entries in exp["quarantined_added"].items():
        for entry in entries:
            record_id = None if entry["record"].startswith("<missing id>") else entry["record"].split(" ")[0]
            expected_q[(sheet, record_id, entry["rule"])] += 1
    assert quarantined(defect_result) - quarantined(clean_result) == expected_q

    invoices = defect_result.excluded_amounts["invoices"]
    expected_inv = exp["excluded_amounts_added"]["invoices"]
    assert invoices.known_recorded_halalas == halalas(expected_inv["known_recorded_amount_excluded"])
    assert (invoices.unreadable_or_not_positive, invoices.duplicate_id, invoices.non_sar,
            invoices.unknown_currency) == (expected_inv["unreadable_or_not_positive"], expected_inv["duplicate_id"],
                                           expected_inv["non_sar"], expected_inv["unknown_currency"])

    d13 = exp["d13_excluded_payments_added"]
    dependents = defect_result.with_status("payments", RecordStatus.DEPENDENT)
    assert [r.record.record_id for r in dependents] == d13["records"]
    assert [(r.dependent_on, r.dependency_rules, r.rules) for r in dependents] == \
        [(d13["dependent_on"], d13["dependency_rules"], [])]
    assert defect_result.dependent_payments.records == d13["count"]
    assert defect_result.dependent_payments.known_recorded_halalas == halalas(d13["known_recorded_amount"])
    assert defect_result.dependent_payments.unknown_currency == d13["unknown_currency"]

    added_oop = out_of_period(defect_result) - out_of_period(clean_result)
    assert added_oop == Counter({("invoices", "INV-D10", "R-14", 115000): 1,
                                 ("payments", "PAY-D10", "R-14", 115000): 1})

    assert defect_result.unreliable_rules() == {"INV-D11": ["R-13"]}
    assert sorted(defect_result.receivable_unreliable) == exp["receivable_unreliable_invoices_added"]
    assert defect_result.receivables_quality.value == exp["receivables_quality_status"]
    assert defect_result.receivables_quality_reasons == exp["receivables_quality_reasons"]
    assert defect_result.collections_reliability.reliable and defect_result.credit_note_reliability.reliable

    assert [f"{i.sheet}:{i.record_id}" for i in warnings(defect_result, "R-17")] == exp["warnings_added"]["R-17"]
    assert sorted(f"{i.sheet}:{i.record_id}" for i in warnings(defect_result, "R-18")) == \
        exp["warnings_added"]["R-18"]
    accepted = {r.record_id for r in defect_result.accepted("invoices")}
    assert {"INV-D09", "INV-D11", "INV-D12"} <= accepted


# ---------------------------------------------------------------- precision and rule edges

CUSTOMER = {"customer_id": "C1", "customer_name": "One", "payment_terms_days": 30}


def invoice(invoice_id, net=1000.0, vat=150.0, total=1150.0, issue=date(2026, 8, 1), due=date(2026, 8, 31)):
    return {"invoice_id": invoice_id, "customer_id": "C1", "issue_date": issue, "due_date": due,
            "currency": "SAR", "net_amount": net, "vat_amount": vat, "total_amount": total}


def payment(payment_id, invoice_id, d=date(2026, 8, 20), amount=100.0):
    return {"payment_id": payment_id, "invoice_id": invoice_id, "payment_date": d, "amount": amount}


def credit(credit_id, invoice_id, d=date(2026, 8, 10), net=100.0, vat=15.0, total=115.0):
    return {"credit_note_id": credit_id, "invoice_id": invoice_id, "credit_date": d,
            "net_amount": net, "vat_amount": vat, "total_amount": total}


def run_rows(tmp_path, invoices, payments=(), credit_notes=()):
    path = write_raw_workbook(tmp_path / "book.xlsx", {
        "customers": [CUSTOMER], "invoices": list(invoices),
        "payments": list(payments), "credit_notes": list(credit_notes)})
    return run_file(path)


def status_of(result, sheet, record_id):
    return next(r.status for r in result.records[sheet] if r.record.record_id == record_id)


def rules_of(result, sheet, record_id):
    return next(r.rules for r in result.records[sheet] if r.record.record_id == record_id)


def test_vat_tolerance_one_halala_accepted_two_quarantined(tmp_path):
    result = run_rows(tmp_path, [invoice("I1", total=1150.01), invoice("I2", total=1150.02),
                                 invoice("I3", total=1149.99), invoice("I4")],
                      credit_notes=[credit("N1", "I4", total=115.01), credit("N2", "I4", total=115.02)])
    assert status_of(result, "invoices", "I1") is RecordStatus.ACCEPTED
    assert rules_of(result, "invoices", "I2") == ["R-16"]
    assert status_of(result, "invoices", "I3") is RecordStatus.ACCEPTED
    assert status_of(result, "credit_notes", "N1") is RecordStatus.ACCEPTED
    assert rules_of(result, "credit_notes", "N2") == ["R-16"]


@pytest.mark.parametrize("net", [1000.005, "1000.005", 0, 0.0, -1000.0, "-1000", True, "1,000.00"])
def test_invalid_invoice_amounts_are_quarantined_r05(tmp_path, net):
    result = run_rows(tmp_path, [invoice("I1"), invoice("I2", net=net)])
    # A readable but non-positive net also breaks net + VAT = total, so R-16 may be reported too.
    assert status_of(result, "invoices", "I2") is RecordStatus.QUARANTINED
    assert "R-05" in rules_of(result, "invoices", "I2")


@pytest.mark.parametrize("amount", [0, -5.0, 10.005, True, "abc", None])
def test_invalid_payment_amounts_are_quarantined_r10(tmp_path, amount):
    result = run_rows(tmp_path, [invoice("I1")], payments=[payment("P1", "I1", amount=amount)])
    assert rules_of(result, "payments", "P1") == ["R-10"]
    assert result.unreliable_rules() == {"I1": ["R-10"]}


@pytest.mark.parametrize("column, value", [("net_amount", 0), ("vat_amount", -1.0), ("total_amount", 115.001)])
def test_credit_note_checks_cover_net_vat_and_total(tmp_path, column, value):
    result = run_rows(tmp_path, [invoice("I1")], credit_notes=[{**credit("N1", "I1"), column: value}])
    assert "R-10" in rules_of(result, "credit_notes", "N1")
    assert result.credit_note_reliability.affected_months == {"2026-08"}


def test_defective_link_after_vd_has_no_effect(tmp_path):
    result = run_rows(tmp_path, [invoice("I1")], payments=[payment("P1", "I1", d=date(2026, 10, 5), amount=0)])
    assert status_of(result, "payments", "P1") is RecordStatus.QUARANTINED
    assert result.receivable_unreliable == {}
    assert result.collections_reliability.reliable


def test_defective_link_before_rp_affects_receivables_but_no_month(tmp_path):
    result = run_rows(tmp_path, [invoice("I1")], payments=[payment("P1", "I1", d=date(2025, 9, 1), amount=0)])
    assert result.unreliable_rules() == {"I1": ["R-10"]}
    assert result.collections_reliability.reliable


def test_defective_link_to_quarantined_invoice_does_not_propagate(tmp_path):
    result = run_rows(tmp_path, [invoice("I1"), invoice("I2", net="abc")],
                      payments=[payment("P1", "I2", amount=0)])
    assert status_of(result, "payments", "P1") is RecordStatus.QUARANTINED
    assert result.collections_reliability.reliable
    assert result.receivable_unreliable == {}


def test_over_settlement_counts_only_links_up_to_vd(tmp_path):
    result = run_rows(tmp_path, [invoice("I1"), invoice("I2")], payments=[
        payment("P1", "I1", amount=1150.0), payment("P2", "I1", d=date(2026, 10, 2), amount=100.0),
        payment("P3", "I2", amount=1150.0)], credit_notes=[credit("N1", "I2", total=115.0)])
    assert result.unreliable_rules() == {"I2": ["R-13"]}
    assert status_of(result, "invoices", "I2") is RecordStatus.ACCEPTED  # stays in sales
    assert not result.issues_for("R-13")  # an invoice state, not a problem class
    reason = result.receivable_unreliable["I2"][0]
    assert (reason.sheet, reason.record_id) == ("invoices", "I2")


def test_r17_payment_before_invoice_is_a_warning_only(tmp_path):
    result = run_rows(tmp_path, [invoice("I1")], payments=[payment("P1", "I1", d=date(2026, 7, 30))])
    assert status_of(result, "payments", "P1") is RecordStatus.ACCEPTED
    assert [i.record_id for i in warnings(result, "R-17")] == ["P1"]


# ---------------------------------------------------------------- currencies in excluded summaries

def usd(invoice_row):
    return {**invoice_row, "currency": "USD"}


def test_dependent_payment_on_usd_invoice_is_non_sar_not_halalas(tmp_path):
    result = run_rows(tmp_path, [invoice("I1"), usd(invoice("I2"))], payments=[payment("P1", "I2", amount=500.0)])
    summary = result.dependent_payments
    assert (summary.records, summary.known_recorded_halalas, summary.non_sar) == (1, 0, 1)
    dependent = result.with_status("payments", RecordStatus.DEPENDENT)[0]
    assert (dependent.dependent_on, dependent.dependency_rules) == ("I2", ["R-07"])


def test_quarantined_links_on_usd_invoice_are_non_sar(tmp_path):
    result = run_rows(tmp_path, [invoice("I1"), usd(invoice("I2"))],
                      payments=[payment("P1", "I2", d="bad-date", amount=500.0)],
                      credit_notes=[credit("N1", "I2", total=115.05)])
    for sheet in ("payments", "credit_notes"):
        summary = result.excluded_amounts[sheet]
        assert (summary.records, summary.known_recorded_halalas, summary.non_sar) == (1, 0, 1), sheet


def test_unknown_or_ambiguous_invoice_currency_is_never_assumed_sar(tmp_path):
    no_currency = {**invoice("I2"), "currency": None}
    result = run_rows(tmp_path, [invoice("I1"), no_currency, invoice("I3"), invoice("I3", net=2000.0, vat=300.0,
                                                                                    total=2300.0)],
                      payments=[payment("P1", "I2", amount=500.0),           # dependent, invoice currency missing
                                payment("P2", "I3", amount=500.0),           # dependent, duplicated invoice ID
                                payment("P3", "I-X", amount=500.0)])         # quarantined, unknown invoice
    assert result.excluded_amounts["invoices"].unknown_currency == 1          # I2 itself
    assert (result.dependent_payments.unknown_currency, result.dependent_payments.known_recorded_halalas) == (2, 0)
    assert (result.excluded_amounts["payments"].unknown_currency,
            result.excluded_amounts["payments"].known_recorded_halalas) == (1, 0)


def test_sar_links_still_count_in_known_amount(tmp_path):
    result = run_rows(tmp_path, [invoice("I1"), invoice("I2", net="abc")], payments=[payment("P1", "I2", amount=500.0)])
    assert (result.dependent_payments.known_records, result.dependent_payments.known_recorded_halalas) == (1, 50000)


# ---------------------------------------------------------------- counting and classification

def test_record_with_several_issues_is_counted_once(tmp_path):
    bad = {**invoice("I2"), "net_amount": 0, "due_date": date(2026, 7, 1), "currency": "USD"}
    result = run_rows(tmp_path, [invoice("I1"), bad])
    assert len([i for i in result.issues if i.row == 3 and i.sheet == "invoices"]) >= 3
    assert result.status_counts()["invoices"] == {"accepted": 1, "quarantined": 1, "out_of_period": 0,
                                                  "dependent": 0}
    summary = result.excluded_amounts["invoices"]
    assert summary.records == 1
    assert summary.known_records + summary.unreadable_or_not_positive + summary.duplicate_id + \
        summary.non_sar + summary.unknown_currency == summary.records


def test_excluded_summaries_partition_records_in_dataset_a(defect_result):
    for summary in [*defect_result.excluded_amounts.values(), defect_result.dependent_payments]:
        assert summary.known_records + summary.unreadable_or_not_positive + summary.duplicate_id + \
            summary.non_sar + summary.unknown_currency == summary.records
    counts = defect_result.status_counts()
    assert all(sum(by_status.values()) == len(defect_result.records[sheet]) for sheet, by_status in counts.items())


def test_dependent_and_receivable_unreliable_are_not_problem_classes():
    assert {s.name for s in Severity} == {"BLOCK", "QUARANTINE", "OUT_OF_PERIOD", "WARN"}
    result = run_file(SAMPLE / "sample_defects.xlsx")
    dependent = result.with_status("payments", RecordStatus.DEPENDENT)[0].record
    assert not [i for i in result.issues if (i.sheet, i.row) == (dependent.sheet, dependent.row)]
