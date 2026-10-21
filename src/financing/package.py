"""JSON package and issue table built from the same results the UI displays.

Nothing is recomputed here: every value comes from ValidationResult, MetricsResult and
FinancingEstimate. Amounts are decimal strings with 2 places; ratios and shares are
decimal strings of the fraction (e.g. "0.3231" = 32.31%); unavailable values are null
with a reason (requirements US-09, D-11).
"""

from __future__ import annotations

import csv
import io
import json

from financing.financing import FinancingEstimate
from financing.loader import load_workbook_file
from financing.metrics import (
    BUCKETS,
    COLLECTIONS_LABEL,
    UNVERIFIED_LABEL,
    Concentration,
    MetricsResult,
    MonthlySeries,
    Ratio,
    Scope,
    compute_metrics,
    format_sar,
)
from financing.rule_summary import build_rule_summary
from financing.validation import validate
from financing.schema import (
    REPORTING_PERIOD_END,
    REPORTING_PERIOD_START,
    VALUATION_DATE,
    RecordStatus,
    ValidationResult,
)

PACKAGE_VERSION = "1.0"
SYNTHETIC_DISCLAIMER = "All data is synthetic. No real company or customer data is used."
UNVERIFIED_ORIGIN_DISCLAIMER = ("The origin of this file is not verified and it cannot be proven synthetic. "
                                "This prototype is intended for synthetic data only; do not use real company "
                                "or customer data.")
DISCLAIMERS = [
    "The outputs support a human review. They are not a credit approval, a credit score or a proven "
    "default probability.",
    "Payments are recorded by the applicant and are not reconciled to bank statements.",
    "Completeness of the source export cannot be proven from dates alone (source_completeness_verified = false).",
]
ASSUMPTIONS = [
    "All amounts are in SAR; invoices and credit notes carry net, VAT and total (A-01).",
    "Each payment and credit note is linked to exactly one invoice (D-04, D-05).",
    "Reporting period 2025-10-01 to 2026-09-30 inclusive; valuation date 2026-09-30 (D-01).",
    "Receivables include only invoices issued within the reporting period (A-06).",
    "The concentration threshold of 30% is experimental, not a bank policy (D-08).",
    "The advance rate and eligibility rule are illustrative assumptions, not a bank policy or credit decision (D-09).",
]
CONVENTIONS = {
    "amounts": "SAR decimal strings with 2 places, e.g. \"1150.00\".",
    "ratios_and_shares": "Decimal strings of the fraction with 4 places, ROUND_HALF_UP on the absolute value, "
                         "e.g. \"0.3231\" means 32.31%. Shares can be negative or above 1.0000.",
    "unavailable": "null, with the reason in the matching 'reason' or 'na_reasons' field. Unknown values are "
                   "never reported as 0.",
    "sales_basis": "Sales and credit notes exclude VAT.",
    "collections_basis": "Collections are payment amounts as recorded (VAT-inclusive cash). They are never "
                         "subtracted from or divided by sales.",
    "receivables_basis": "Open balances include VAT.",
}


def analyse(source, filename: str | None = None) -> tuple[ValidationResult, MetricsResult]:
    """Load, validate and compute indicators once; the UI and the export share this result."""
    validation = validate(load_workbook_file(source, filename))
    return validation, compute_metrics(validation)


# ---------------------------------------------------------------- small converters

def _amount(value: int | None, reason: str | None = None) -> dict:
    out = {"value": format_sar(value)}
    if value is None:
        out["reason"] = reason or "not available"
    return out


def _ratio(ratio: Ratio) -> dict:
    return {"value": ratio.display, "reason": ratio.reason} if ratio.value is None else {"value": ratio.display}


def _scope(scope: Scope) -> dict:
    return {"name": scope.name, "description": scope.description, "record_counts": dict(scope.record_counts)}


def _series(series: MonthlySeries) -> dict:
    out = {
        "basis": series.basis,
        "scope": _scope(series.scope),
        "monthly": {m: format_sar(v) for m, v in series.monthly.items()},
        "total": format_sar(series.total),
        "na_reasons": dict(series.na_reasons),
    }
    if series.label:
        out["label"] = series.label
    return out


def _concentration(conc: Concentration) -> dict:
    out = {"basis": conc.basis, "scope": _scope(conc.scope), "total": format_sar(conc.total),
           "threshold": "0.3000", "threshold_note": "experimental - not a bank policy"}
    if conc.top is None:
        out.update(top=None, reason=conc.reason, warnings=[])
    else:
        out.update(top=[{"customer_id": e.customer_id, "amount": format_sar(e.amount), "share": e.share_display}
                        for e in conc.top], warnings=list(conc.warnings))
    return out


# ---------------------------------------------------------------- issue table (UI and CSV)

ISSUE_COLUMNS = ["class", "rule", "sheet", "row", "record_id", "column", "raw_value", "affected_invoice", "message"]


def issue_rows(validation: ValidationResult, metrics: MetricsResult | None = None) -> list[dict]:
    """Every problem, exclusion reason and invoice state, one row each.

    Besides the validation issues, this lists dependent records (D-13) and receivable-unreliable
    invoices with each cause - including R-13, which is an invoice state, not an issue (X-12) -
    and the R-20 warnings raised with the indicators.
    """
    rows = [{"class": i.severity.value, "rule": i.rule, "sheet": i.sheet, "row": i.row, "record_id": i.record_id,
             "column": i.column, "raw_value": None if i.raw_value is None else str(i.raw_value),
             "affected_invoice": i.affected_invoice, "message": i.message} for i in validation.issues]
    for sheet in ("payments", "credit_notes"):
        for r in validation.with_status(sheet, RecordStatus.DEPENDENT):
            rows.append({"class": "DEPENDENT", "rule": ", ".join(r.dependency_rules), "sheet": sheet,
                         "row": r.record.row, "record_id": r.record.record_id, "column": None, "raw_value": None,
                         "affected_invoice": r.dependent_on,
                         "message": f"excluded because invoice {r.dependent_on} is quarantined "
                                    f"({', '.join(r.dependency_rules)})"})
    for invoice_id, reasons in validation.receivable_unreliable.items():
        for reason in reasons:
            rows.append({"class": "RECEIVABLE UNRELIABLE", "rule": reason.rule, "sheet": reason.sheet,
                         "row": reason.row, "record_id": reason.record_id, "column": None, "raw_value": None,
                         "affected_invoice": invoice_id,
                         "message": f"invoice {invoice_id} kept in sales, removed from receivables: {reason.message}"})
    for w in (metrics.warnings if metrics and not metrics.blocked else []):
        rows.append({"class": w.severity.value, "rule": w.rule, "sheet": w.sheet, "row": w.row,
                     "record_id": w.record_id, "column": None, "raw_value": None, "affected_invoice": None,
                     "message": w.message})
    return rows


def issues_csv(validation: ValidationResult, metrics: MetricsResult | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=ISSUE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(issue_rows(validation, metrics))
    return buffer.getvalue()


# ---------------------------------------------------------------- package

def _excluded(summary) -> dict:
    return {"records": summary.records, "known_recorded_amount_excluded": format_sar(summary.known_recorded_halalas),
            "known_recorded_amount_note": "sum of recorded SAR values; not verified; may understate",
            "known_records": summary.known_records, "duplicate_id": summary.duplicate_id,
            "unknown_currency": summary.unknown_currency, "non_sar": summary.non_sar,
            "unreadable_or_not_positive": summary.unreadable_or_not_positive}


def build_package(validation: ValidationResult, metrics: MetricsResult, estimate: FinancingEstimate,
                  applicant_id: str | None = None, source_name: str | None = None,
                  known_synthetic_sample: bool = False) -> dict:
    """`applicant_id` is the analyst-supplied identifier (None when not given) - never the file name.
    `known_synthetic_sample` is True only for the samples shipped with the prototype; any other
    file cannot be proven synthetic, so `synthetic_data` is null for it."""
    data_quality = {
        "source_completeness_verified": validation.source_completeness_verified,
        "record_counts_by_status": validation.status_counts(),
        "issues": issue_rows(validation, metrics),
    }
    if not validation.blocked:
        data_quality.update(
            receivables_quality_status={"value": validation.receivables_quality.value,
                                        "reasons": list(validation.receivables_quality_reasons)},
            excluded_records={sheet: _excluded(s) for sheet, s in validation.excluded_amounts.items()},
            dependent_payments_d13=_excluded(validation.dependent_payments),
            receivable_unreliable_invoices=[
                {"invoice_id": invoice_id,
                 "reasons": [{"rule": r.rule, "message": r.message, "source_sheet": r.sheet,
                              "source_row": r.row, "source_record_id": r.record_id} for r in reasons]}
                for invoice_id, reasons in validation.receivable_unreliable.items()],
        )

    package = {
        "package_version": PACKAGE_VERSION,
        "synthetic_data": True if known_synthetic_sample else None,
        "synthetic_data_note": ("known synthetic sample shipped with the prototype" if known_synthetic_sample
                                else "origin not verified; the file cannot be proven synthetic"),
        "applicant_id": applicant_id,
        "applicant_id_note": None if applicant_id else "not provided",
        "source_file": source_name,
        "reporting_period": {"start": REPORTING_PERIOD_START.isoformat(), "end": REPORTING_PERIOD_END.isoformat()},
        "valuation_date": VALUATION_DATE.isoformat(),
        "conventions": CONVENTIONS,
        "status": {"blocked": metrics.blocked, "block_reasons": list(metrics.block_reasons)},
        "data_quality": data_quality,
        "indicators": None,
        "financing_estimate": {
            "value": format_sar(estimate.amount_halalas),
            "reason": estimate.reason,
            "advance_rate": f"{estimate.rate_percent / 100:.4f}",
            "advance_rate_percent": estimate.rate_percent,
            "eligible_receivables": format_sar(estimate.eligible_halalas),
            "eligibility_rule": estimate.eligibility_rule,
            "label": estimate.label,
        },
        "warnings": [r for r in issue_rows(validation, metrics) if r["class"] == "WARN"],
        "ai_summary": {"value": None, "reason": "not requested"},  # replaced by ai_summary.with_ai_summary
        "assumptions": ASSUMPTIONS,
        "disclaimers": [SYNTHETIC_DISCLAIMER if known_synthetic_sample else UNVERIFIED_ORIGIN_DISCLAIMER,
                        *DISCLAIMERS],
    }
    if metrics.blocked:
        package["indicators_reason"] = "analysis is blocked; no indicators are produced"
        package["automated_summary"] = build_rule_summary(package)
        return package

    receivables = metrics.receivables
    package["indicators"] = {
        "gross_sales": _series(metrics.gross_sales),
        "credit_notes": _series(metrics.credit_notes),
        "net_sales": _series(metrics.net_sales),
        "collections": {**_series(metrics.collections), "label": f"{COLLECTIONS_LABEL}; {UNVERIFIED_LABEL}"},
        "credit_note_ratio": {**_ratio(metrics.credit_note_ratio),
                              "meaning": "credit notes / gross sales over the reporting period, both excl. VAT"},
        "receivables": {
            "basis": "incl. VAT, at the valuation date",
            "scope": _scope(receivables.scope),
            "quality_status": receivables.quality_status,
            "aging": None if receivables.buckets is None else {b: format_sar(receivables.buckets[b]) for b in BUCKETS},
            "total": format_sar(receivables.total),
            "reason": receivables.reason,
            "open_invoices": [{"invoice_id": i.invoice_id, "customer_id": i.customer_id,
                               "due_date": i.due_date.isoformat(), "days_overdue": i.days_overdue,
                               "open_balance": format_sar(i.open_halalas), "bucket": i.bucket}
                              for i in receivables.invoices],
        },
        "sales_concentration": _concentration(metrics.sales_concentration),
        "receivables_concentration": _concentration(metrics.receivables_concentration),
    }
    package["automated_summary"] = build_rule_summary(package)  # rule-based default summary, from the above
    return package


def package_json(package: dict) -> str:
    return json.dumps(package, indent=2, ensure_ascii=False) + "\n"
