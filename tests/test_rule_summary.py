"""Rule-based automated summary: no AI, no SDK, no key; built only from computed results."""

import json

import pytest

from conftest import FIXTURES, SAMPLE
from financing.ai_summary import _FORBIDDEN
from financing.financing import estimate_financing
from financing.package import analyse, build_package, package_json
from financing.rule_summary import TITLE, build_rule_summary


def make_package(path, rate=70):
    validation, metrics = analyse(path)
    return build_package(validation, metrics, estimate_financing(metrics, rate), applicant_id="APP-DEMO-001",
                         source_name=path.name, known_synthetic_sample=True)


def figures(summary):
    return {f["label"]: f for f in summary["key_figures"]}


def all_text(summary):
    return json.dumps(summary, ensure_ascii=False)


@pytest.fixture(scope="module")
def clean():
    return make_package(SAMPLE / "sample_clean.xlsx")


@pytest.fixture(scope="module")
def defects():
    return make_package(SAMPLE / "sample_defects.xlsx")


def test_title_and_not_called_ai(clean):
    summary = clean["automated_summary"]
    assert summary["title"] == TITLE == "Automated analyst summary — rule-based"
    assert "(no AI)" in summary["note"]
    assert "AI-generated" not in all_text(summary)


def test_clean_sample_figures_come_from_the_package(clean):
    summary, ind = clean["automated_summary"], clean["indicators"]
    fig = figures(summary)
    assert fig["Gross sales, reporting period (excl. VAT)"]["value"] == "SAR 7,597,838.04" == \
        f"SAR {float(ind['gross_sales']['total']):,.2f}"
    assert fig["Net sales, reporting period (excl. VAT)"]["value"] == "SAR 7,548,825.74"
    assert fig["Collections from accepted records only (incl. VAT)"]["value"] == "SAR 7,103,249.10"
    assert "unverified" in fig["Collections from accepted records only (incl. VAT)"]["note"]
    assert fig["Open receivables at the valuation date (incl. VAT)"]["value"] == "SAR 1,577,900.74"
    assert fig["Open receivables more than 90 days overdue"]["value"] == "SAR 90,898.64"
    assert fig["Illustrative financing estimate at 70%"]["value"] == "SAR 1,040,901.47"
    assert fig["Credit-note ratio (credit notes / gross sales)"]["value"] == "0.65%"
    assert fig["Largest customer by open receivables"]["value"] == "CUST-003 (32.31%)"
    assert summary["headline"].startswith("Net sales of SAR 7,548,825.74 over the reporting period")


def test_clean_sample_warnings_limits_and_questions(clean):
    summary = clean["automated_summary"]
    assert any(w.startswith("R-20: CUST-003 holds 32.31% of open receivables") for w in summary["warnings"])
    assert summary["data_limits"][0].startswith("Collections are payments recorded by the applicant")
    assert any("CUST-003" in q for q in summary["follow_up_questions"])
    assert any("SAR 90,898.64 more than 90 days overdue" in q for q in summary["follow_up_questions"])
    assert summary["follow_up_questions"][-1].startswith("Can the applicant provide bank statements")


def test_defect_sample_reports_r13_quarantine_and_d13(defects):
    summary = defects["automated_summary"]
    text = " ".join(summary["warnings"])
    assert "Invoice INV-D11 is excluded from receivables (R-13)" in text
    assert "Quarantined records: 3 customers, 10 invoices" in text
    assert "1 payment(s) excluded from collections because their invoice is quarantined (D-13)" in text
    assert "R-17: 1" in text and "R-18: 2" in text
    assert "Receivables quality: invoices_excluded" in text
    assert any("INV-D11" in q for q in summary["follow_up_questions"])
    assert any(q.startswith("Can the applicant correct the quarantined records") for q in summary["follow_up_questions"])
    assert any("before the reporting period" in q for q in summary["follow_up_questions"])


@pytest.mark.parametrize("case", ["TC-01.csv", "TC-02.xlsx", "TC-03.xlsx"])
def test_block_reports_reasons_and_no_figures(case):
    summary = make_package(FIXTURES / "cases" / case)["automated_summary"]
    assert summary["status"] == "blocked" and summary["key_figures"] == []
    assert summary["warnings"] and summary["warnings"][0][:4] in ("S-01", "S-02", "S-03")
    assert "SAR" not in all_text(summary)
    assert summary["follow_up_questions"] == ["Can the applicant provide a corrected workbook that fixes the "
                                              "blocking problems listed above?"]


def test_na_values_stay_na_with_reasons():
    summary = make_package(FIXTURES / "cases" / "TC-09.xlsx")["automated_summary"]
    fig = figures(summary)
    net = fig["Net sales, reporting period (excl. VAT)"]
    assert net["value"] == "N/A" and net["reason"]
    assert fig["Credit-note ratio (credit notes / gross sales)"]["value"] == "N/A"
    assert fig["Largest customer by net sales"]["value"] == "N/A"
    assert fig["Gross sales, reporting period (excl. VAT)"]["value"] == "SAR 3,000.00"
    assert summary["headline"].startswith("Net sales of N/A (see data limits)")
    assert any(line.startswith("Net sales 2026-08: N/A") for line in summary["data_limits"])


def test_unresolved_links_and_empty_scope():
    tc08 = make_package(FIXTURES / "cases" / "TC-08.xlsx")["automated_summary"]
    assert figures(tc08)["Illustrative financing estimate at 70%"]["value"] == "N/A"
    assert any("unlinked payments" in q for q in tc08["follow_up_questions"])
    tc07 = make_package(FIXTURES / "cases" / "TC-07.xlsx")["automated_summary"]
    assert figures(tc07)["Open receivables at the valuation date (incl. VAT)"]["reason"] == "receivables scope is empty"


def test_true_zero_is_shown_as_zero():
    summary = make_package(FIXTURES / "cases" / "TC-15.xlsx")["automated_summary"]
    assert figures(summary)["Open receivables at the valuation date (incl. VAT)"]["value"] == "SAR 0.00"
    assert figures(summary)["Illustrative financing estimate at 70%"]["value"] == "SAR 0.00"


@pytest.mark.parametrize("path", [SAMPLE / "sample_clean.xlsx", SAMPLE / "sample_defects.xlsx",
                                  FIXTURES / "cases" / "TC-08.xlsx", FIXTURES / "cases" / "TC-02.xlsx"])
def test_no_decision_wording_and_exported(path):
    package = make_package(path)
    summary = package["automated_summary"]
    assert not _FORBIDDEN.search(all_text({k: v for k, v in summary.items() if k != "note"}))
    assert json.loads(package_json(package))["automated_summary"] == summary


def test_summary_follows_the_rate_without_new_calculations(clean):
    at_60 = make_package(SAMPLE / "sample_clean.xlsx", rate=60)["automated_summary"]
    assert figures(at_60)["Illustrative financing estimate at 60%"]["value"] == \
        f"SAR {float(make_package(SAMPLE / 'sample_clean.xlsx', rate=60)['financing_estimate']['value']):,.2f}"
    assert build_rule_summary(clean) == clean["automated_summary"]  # deterministic
