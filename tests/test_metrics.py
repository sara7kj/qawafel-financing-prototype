"""Indicators and the illustrative financing estimate against the hand-written expected results."""

import copy
import json
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from conftest import FIXTURES, SAMPLE, write_raw_workbook
from financing.financing import apply_rate, estimate_financing, validate_rate
from financing.loader import load_workbook_file
from financing.metrics import bucket_for, compute_metrics, display_ratio, round_half_up_div
from financing.schema import VALUATION_DATE
from financing.validation import reporting_months, validate

EXPECTED = FIXTURES / "expected"
CASES = json.loads((EXPECTED / "cases_expected.json").read_text(encoding="utf-8"))
WORKED = json.loads((EXPECTED / "worked_example_expected.json").read_text(encoding="utf-8"))


def h(text):
    return None if text is None else int(Decimal(text) * 100)


def analyse(path):
    validation = validate(load_workbook_file(path))
    return validation, compute_metrics(validation)


def assert_series(series, expected):
    monthly = expected["monthly"]
    for month in reporting_months():
        if monthly == "ALL_NA":
            want = None
        else:
            want = h(monthly[month]) if month in monthly else 0
        assert series.monthly[month] == want, (series.name, month)
        if want is None:
            assert series.na_reasons[month]
    assert series.total == h(expected["total"]), series.name
    if series.total is None:
        assert series.na_reasons["total"]


def r20(metrics):
    return sorted([f"sales:{c}" for c in metrics.sales_concentration.warnings] +
                  [f"receivables:{c}" for c in metrics.receivables_concentration.warnings])


# ---------------------------------------------------------------- worked example (oracle)

@pytest.fixture(scope="module")
def worked():
    return analyse(FIXTURES / WORKED["file"])[1]


def test_worked_example_series(worked):
    assert_series(worked.gross_sales, WORKED["gross_sales"])
    assert_series(worked.credit_notes, WORKED["credit_notes"])
    assert_series(worked.net_sales, WORKED["net_sales"])
    assert_series(worked.collections, WORKED["collections_from_accepted_records_only"])
    assert "Collections from accepted records only" in worked.collections.label


def test_worked_example_credit_note_ratio(worked):
    num, den = WORKED["credit_note_ratio"]["fraction"].split("/")
    assert worked.credit_note_ratio.value == Fraction(h(num), h(den))
    assert worked.credit_note_ratio.display == WORKED["credit_note_ratio"]["display"]


def test_worked_example_receivables(worked):
    open_by_invoice = {i.invoice_id: i for i in worked.receivables.invoices}
    assert set(open_by_invoice) == set(WORKED["open_balances"])
    for invoice_id, exp in WORKED["open_balances"].items():
        actual = open_by_invoice[invoice_id]
        assert (actual.open_halalas, actual.days_overdue, actual.bucket.replace("current", "current")) == \
            (h(exp["open"]), exp["days_overdue"], exp["bucket"])
    assert worked.receivables.buckets == {k: h(v) for k, v in WORKED["aging"].items() if k != "total"}
    assert worked.receivables.total == h(WORKED["aging"]["total"])


def test_worked_example_financing(worked):
    estimate = estimate_financing(worked)
    assert estimate.eligible_halalas == h(WORKED["eligible_receivables"])
    assert estimate.amount_halalas == h(WORKED["financing_estimate_at_70pct"]) == 898827  # SAR 8,988.27
    assert estimate.reason is None and "Illustrative" in estimate.label


def test_worked_example_concentration(worked):
    for conc, key in ((worked.sales_concentration, "sales_concentration"),
                      (worked.receivables_concentration, "receivables_concentration")):
        exp = WORKED[key]
        assert [e.customer_id for e in conc.top] == sorted(exp, key=lambda c: -h(exp[c]["amount"]))
        for entry in conc.top:
            num, den = exp[entry.customer_id]["fraction"].split("/")
            assert entry.amount == h(exp[entry.customer_id]["amount"])
            assert entry.share == Fraction(h(num), h(den))
            assert entry.share_display == exp[entry.customer_id]["display"]
    assert r20(worked) == sorted(WORKED["warnings"]["R-20"])
    assert worked.receivables_quality == WORKED["receivables_quality_status"]
    assert worked.source_completeness_verified is False


# ---------------------------------------------------------------- Dataset B

@pytest.mark.parametrize("case_id", sorted(k for k in CASES if k.startswith("TC-")))
def test_case_metrics_match_expected(case_id):
    exp = CASES[case_id]
    _, metrics = analyse(FIXTURES / exp["file"])
    if exp["block"]:
        assert metrics.blocked and metrics.gross_sales is None
        estimate = estimate_financing(metrics)
        assert estimate.amount_halalas is None and estimate.reason == "analysis is blocked"
        return

    assert not metrics.blocked
    for name in ("gross_sales", "credit_notes", "net_sales", "collections"):
        assert_series(getattr(metrics, name), exp[name])
    assert metrics.credit_note_ratio.display == exp["credit_note_ratio"]
    if exp["credit_note_ratio"] is None:
        assert metrics.credit_note_ratio.reason

    if exp["aging"] is None:
        assert metrics.receivables.buckets is None and metrics.receivables.reason
    else:
        assert metrics.receivables.buckets == {k: h(v) for k, v in exp["aging"].items() if k != "total"}
        assert metrics.receivables.total == h(exp["aging"]["total"])
    for invoice_id, days in exp.get("days_overdue", {}).items():
        assert (VALUATION_DATE - next(i for i in metrics.receivables.invoices
                                      if i.invoice_id == invoice_id).due_date).days == days

    estimate = estimate_financing(metrics)
    assert estimate.eligible_halalas == h(exp["eligible_receivables"])
    assert estimate.amount_halalas == h(exp["financing_estimate_at_70pct"])
    if estimate.amount_halalas is None:
        assert estimate.reason

    for key, conc in (("sales_concentration", metrics.sales_concentration),
                      ("receivables_concentration", metrics.receivables_concentration)):
        if exp[key] is None:
            assert conc.top is None and conc.reason, key
        else:
            assert {e.customer_id: e.share_display for e in conc.top} == exp[key], key
    assert r20(metrics) == sorted(exp["R-20"])
    assert metrics.receivables_quality == exp["receivables_quality_status"]
    assert sorted(metrics.receivable_unreliable) == sorted(exp["receivable_unreliable_invoices"])


# ---------------------------------------------------------------- Dataset A and the clean sample

@pytest.fixture(scope="module")
def clean_pair():
    return analyse(SAMPLE / "sample_clean.xlsx")


@pytest.fixture(scope="module")
def defect_pair():
    return analyse(SAMPLE / "sample_defects.xlsx")


def test_defect_sample_differences_match_expected(clean_pair, defect_pair):
    exp = json.loads((EXPECTED / "sample_defects_expected.json").read_text(encoding="utf-8"))["differences_vs_clean"]
    clean, defect = clean_pair[1], defect_pair[1]
    for name in ("gross_sales", "net_sales"):
        assert defect.__dict__[name].monthly["2026-06"] - clean.__dict__[name].monthly["2026-06"] == h(exp[name]["2026-06"])
        assert defect.__dict__[name].total - clean.__dict__[name].total == h(exp[name]["total"])
        assert all(defect.__dict__[name].monthly[m] == clean.__dict__[name].monthly[m]
                   for m in reporting_months() if m != "2026-06")
    assert defect.credit_notes.total - clean.credit_notes.total == h(exp["credit_notes"]["total"])
    col = exp["collections_from_accepted_records_only"]
    assert defect.collections.monthly["2026-06"] - clean.collections.monthly["2026-06"] == h(col["2026-06"])
    assert defect.collections.total - clean.collections.total == h(col["total"])
    assert defect.receivables.buckets["61-90"] - clean.receivables.buckets["61-90"] == h(exp["open_balance"]["61-90"])
    assert defect.receivables.total - clean.receivables.total == h(exp["open_balance"]["total"])
    assert estimate_financing(defect).eligible_halalas - estimate_financing(clean).eligible_halalas == \
        h(exp["eligible_receivables"])
    assert defect.receivable_unreliable == {"INV-D11": ["R-13"]}
    assert defect.dependent_payments.records == 1


def test_clean_sample_agrees_with_generator_self_check(clean_pair):
    """The generator's report uses its own in-memory data; the two implementations must agree."""
    report = json.loads((SAMPLE / "generation_report.json").read_text(encoding="utf-8"))
    targets = report["profile_vs_targets"]
    metrics = clean_pair[1]
    assert metrics.gross_sales.total == h(f"{targets['gross_sales_sar']['actual']:.2f}")
    assert metrics.receivables.total == h(f"{report['open_balance_at_vd_sar']:.2f}")
    top = metrics.sales_concentration.top
    assert abs(float(top[0].share) - targets["top1_net_sales_share"]["actual"]) < 0.00005
    assert abs(float(sum(e.share for e in top)) - targets["top5_net_sales_share"]["actual"]) < 0.00005
    assert abs(float(metrics.credit_note_ratio.value) - targets["credit_note_ratio"]["actual"]) < 0.00005
    over_90 = Fraction(metrics.receivables.buckets[">90"], metrics.receivables.total)
    assert abs(float(over_90) - targets["over_90_share_of_open_balance"]["actual"]) < 0.00005
    assert metrics.receivables_quality == "no_known_issues"
    assert all(v is not None for s in (metrics.gross_sales, metrics.credit_notes, metrics.net_sales,
                                       metrics.collections) for v in s.monthly.values())


def test_clean_sample_r20_on_receivables_is_a_warning_only(clean_pair):
    """CUST-003 holds > 30% of reliable open balances (data-design §3). The WARN must not
    quarantine anything, change any calculation or block the analysis."""
    validation, metrics = clean_pair
    conc = metrics.receivables_concentration
    entry = next(e for e in conc.top if e.customer_id == "CUST-003")
    assert (entry.amount, conc.total) == (50989565, 157790074)
    assert entry.share == Fraction(50989565, 157790074) and entry.share > Fraction(3, 10)
    assert conc.warnings == ["CUST-003"] and metrics.sales_concentration.warnings == []
    assert [(w.rule, w.severity.value, w.record_id) for w in metrics.warnings] == [("R-20", "WARN", "CUST-003")]

    assert not metrics.blocked and not validation.blocked
    assert all(n == 0 for counts in validation.status_counts().values() for s, n in counts.items()
               if s in ("quarantined", "dependent"))
    cust_003_open = sum(i.open_halalas for i in metrics.receivables.invoices if i.customer_id == "CUST-003")
    assert cust_003_open == entry.amount                        # still counted in receivables
    assert metrics.receivables.total == sum(metrics.receivables.buckets.values()) == conc.total
    estimate = estimate_financing(metrics)
    assert estimate.amount_halalas is not None
    assert estimate.eligible_halalas == sum(i.open_halalas for i in metrics.receivables.invoices
                                            if i.days_overdue <= 90)  # CUST-003 balances are included


def test_metrics_do_not_modify_the_validation_result(defect_pair):
    validation = defect_pair[0]
    before = copy.deepcopy(validation)
    compute_metrics(validation)
    assert validation == before


# ---------------------------------------------------------------- aging and rate boundaries

@pytest.mark.parametrize("days, bucket", [(-1, "current"), (0, "current"), (1, "1-30"), (30, "1-30"),
                                          (31, "31-60"), (60, "31-60"), (61, "61-90"), (90, "61-90"),
                                          (91, ">90")])
def test_bucket_boundaries(days, bucket):
    assert bucket_for(days) == bucket


def test_aging_and_eligibility_boundaries_end_to_end(tmp_path):
    days_list = [-1, 0, 1, 30, 31, 60, 61, 90, 91]
    invoices = []
    for n, days in enumerate(days_list):
        due = VALUATION_DATE - timedelta(days=days)
        invoices.append({"invoice_id": f"I{n}", "customer_id": "C1", "issue_date": due - timedelta(days=30),
                         "due_date": due, "currency": "SAR", "net_amount": 100.0, "vat_amount": 15.0,
                         "total_amount": 115.0})
    path = write_raw_workbook(tmp_path / "book.xlsx", {
        "customers": [{"customer_id": "C1", "customer_name": "One", "payment_terms_days": 30}],
        "invoices": invoices})
    _, metrics = analyse(path)
    by_id = {i.invoice_id: i for i in metrics.receivables.invoices}
    assert [(by_id[f"I{n}"].days_overdue, by_id[f"I{n}"].bucket) for n in range(len(days_list))] == \
        [(d, bucket_for(d)) for d in days_list]
    assert metrics.receivables.buckets == {"current": 23000, "1-30": 23000, "31-60": 23000,
                                           "61-90": 23000, ">90": 11500}
    estimate = estimate_financing(metrics)
    assert estimate.eligible_halalas == 8 * 11500  # 90 days is eligible, 91 is not
    assert estimate.amount_halalas == 64400


@pytest.mark.parametrize("rate, amount", [(0, 0), (70, 898827), (100, 1284038)])
def test_financing_rates(worked, rate, amount):
    estimate = estimate_financing(worked, rate)
    assert (estimate.rate_percent, estimate.amount_halalas) == (rate, amount)


@pytest.mark.parametrize("rate", [-1, 101, 70.0, 70.5, "70", None, True, float("nan")])
def test_invalid_rates_are_rejected(worked, rate):
    with pytest.raises(ValueError):
        estimate_financing(worked, rate)
    with pytest.raises(ValueError):
        validate_rate(rate)


@pytest.mark.parametrize("eligible, rate, expected", [(1, 50, 1), (3, 50, 2), (1, 70, 1), (1, 40, 0),
                                                      (1284038, 70, 898827), (0, 70, 0)])
def test_financing_rounds_half_up_to_the_halala(eligible, rate, expected):
    assert apply_rate(eligible, rate) == expected


# ---------------------------------------------------------------- N/A and zero handling

def test_display_ratio_rounds_half_up_only_for_display():
    assert display_ratio(Fraction(1, 20000)) == "0.0001"   # 0.00005 -> half up
    assert display_ratio(Fraction(1, 20001)) == "0.0000"
    assert display_ratio(Fraction(3, 10)) == "0.3000"
    assert display_ratio(None) is None


@pytest.mark.parametrize("value, shown", [
    (Fraction(-1, 10), "-0.1000"),
    (Fraction(-1, 20000), "-0.0001"),    # -0.00005: half-up on the absolute value, then the sign
    (Fraction(-1, 20001), "0.0000"),     # rounds to zero: no "-0.0000"
    (Fraction(-12195, 10000), "-1.2195"),
    (Fraction(0), "0.0000"),
])
def test_display_ratio_handles_negative_values(value, shown):
    assert display_ratio(value) == shown


@pytest.mark.parametrize("numerator, denominator", [(-1, 2), (1, 0), (1, -2)])
def test_round_half_up_div_rejects_values_outside_its_contract(numerator, denominator):
    with pytest.raises(ValueError):
        round_half_up_div(numerator, denominator)


def test_negative_customer_net_sales_share_is_displayed_correctly(tmp_path):
    """B's credit note exceeds its invoice, so B's net sales are negative while the total stays
    positive. Existing rules apply unchanged: B's invoice is over-settled (R-13), kept in sales,
    removed from receivables."""
    def inv(invoice_id, customer, net):
        return {"invoice_id": invoice_id, "customer_id": customer, "issue_date": date(2026, 8, 1),
                "due_date": date(2026, 8, 31), "currency": "SAR", "net_amount": net,
                "vat_amount": round(net * 0.15, 2), "total_amount": round(net * 1.15, 2)}
    path = write_raw_workbook(tmp_path / "book.xlsx", {
        "customers": [{"customer_id": c, "customer_name": c, "payment_terms_days": 30} for c in ("A", "B")],
        "invoices": [inv("I1", "A", 5000.0), inv("I2", "B", 100.0)],
        "credit_notes": [{"credit_note_id": "N1", "invoice_id": "I2", "credit_date": date(2026, 8, 5),
                          "net_amount": 1000.0, "vat_amount": 150.0, "total_amount": 1150.0}]})
    validation, metrics = analyse(path)
    assert validation.unreliable_rules() == {"I2": ["R-13"]}
    assert metrics.net_sales.total == 410000                         # 5,000.00 - 900.00
    shares = {e.customer_id: (e.amount, e.share, e.share_display) for e in metrics.sales_concentration.top}
    assert shares["A"] == (500000, Fraction(500000, 410000), "1.2195")
    assert shares["B"] == (-90000, Fraction(-90000, 410000), "-0.2195")
    assert metrics.sales_concentration.warnings == ["A"]


def test_exact_threshold_is_not_a_warning_but_just_above_is(tmp_path):
    def inv(i, customer, net):
        return {"invoice_id": i, "customer_id": customer, "issue_date": date(2026, 9, 1),
                "due_date": date(2026, 10, 1), "currency": "SAR", "net_amount": net,
                "vat_amount": round(net * 0.15, 2), "total_amount": round(net * 1.15, 2)}
    customers = [{"customer_id": c, "customer_name": c, "payment_terms_days": 30} for c in ("A", "B", "C")]
    # A holds exactly 30% -> no WARN; B holds 30.01% -> WARN (compared before display rounding)
    path = write_raw_workbook(tmp_path / "book.xlsx", {"customers": customers, "invoices": [
        inv("I1", "A", 3000.00), inv("I2", "B", 3001.00), inv("I3", "C", 3999.00)]})
    _, metrics = analyse(path)
    shares = {e.customer_id: e.share for e in metrics.sales_concentration.top}
    assert shares["A"] == Fraction(3, 10) and "A" not in metrics.sales_concentration.warnings
    assert metrics.sales_concentration.warnings == ["C", "B"]


def test_zero_net_sales_makes_sales_ranking_na_not_zero(tmp_path):
    path = write_raw_workbook(tmp_path / "book.xlsx", {
        "customers": [{"customer_id": "C1", "customer_name": "One", "payment_terms_days": 30}],
        "invoices": [{"invoice_id": "I1", "customer_id": "C1", "issue_date": date(2026, 8, 1),
                      "due_date": date(2026, 8, 31), "currency": "SAR", "net_amount": 1000.0,
                      "vat_amount": 150.0, "total_amount": 1150.0}],
        "credit_notes": [{"credit_note_id": "N1", "invoice_id": "I1", "credit_date": date(2026, 8, 5),
                          "net_amount": 1000.0, "vat_amount": 150.0, "total_amount": 1150.0}]})
    _, metrics = analyse(path)
    assert metrics.net_sales.total == 0                        # a true zero is shown as zero
    assert metrics.credit_note_ratio.display == "1.0000"
    assert metrics.sales_concentration.top is None and metrics.sales_concentration.reason
    assert metrics.receivables.total == 0 and metrics.receivables_concentration.top is None
    assert estimate_financing(metrics).amount_halalas == 0     # scope not empty -> 0.00, not N/A


def test_affected_month_blocks_totals_but_not_other_months():
    _, metrics = analyse(FIXTURES / "cases" / "TC-09.xlsx")
    assert metrics.gross_sales.total == 300000                 # gross stays available
    assert metrics.net_sales.monthly["2026-09"] == 200000
    assert metrics.net_sales.monthly["2026-08"] is None and metrics.net_sales.total is None
    assert metrics.credit_note_ratio.value is None and metrics.sales_concentration.top is None


def test_unresolved_links_keep_aging_but_withhold_financing():
    _, metrics = analyse(FIXTURES / "cases" / "TC-08.xlsx")
    assert metrics.receivables.total == 345000
    assert metrics.receivables.quality_status == "unresolved_links"
    estimate = estimate_financing(metrics)
    assert estimate.amount_halalas is None and "unresolved_links" in estimate.reason
