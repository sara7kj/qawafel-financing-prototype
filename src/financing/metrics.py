"""Indicators from a ValidationResult (requirements §3.3-§3.4, data-design §7 step 12).

Amounts are integer halalas; ratios and shares are exact Fractions and are rounded
only by the display helpers. Unknown values are None with a reason, never 0.
The input ValidationResult is only read, never modified.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from fractions import Fraction

from financing.schema import (
    REPORTING_PERIOD_END,
    REPORTING_PERIOD_START,
    VALUATION_DATE,
    ExcludedAmounts,
    Issue,
    Record,
    SeriesReliability,
    Severity,
    ValidationResult,
)
from financing.validation import month_of, reporting_months

BUCKETS = ("current", "1-30", "31-60", "61-90", ">90")
CONCENTRATION_THRESHOLD = Fraction(30, 100)  # experimental, not a bank policy (D-08)
TOP_N = 5
COLLECTIONS_LABEL = "Collections from accepted records only"
UNVERIFIED_LABEL = "Unverified - not reconciled to bank statements"


# ---------------------------------------------------------------- display helpers

def round_half_up_div(numerator: int, denominator: int) -> int:
    """numerator / denominator rounded half-up (ties away from zero).

    Defined for numerator >= 0 and denominator > 0 only; callers with a signed value
    apply it to the absolute value and restore the sign (see display_ratio).
    """
    if numerator < 0 or denominator <= 0:
        raise ValueError("round_half_up_div needs numerator >= 0 and denominator > 0")
    return (2 * numerator + denominator) // (2 * denominator)


def display_ratio(value: Fraction | None, places: int = 4) -> str | None:
    """Exact value rounded ROUND_HALF_UP on its absolute value for display; never shows -0."""
    if value is None:
        return None
    scale = 10 ** places
    scaled = round_half_up_div(abs(value.numerator) * scale, value.denominator)  # Fraction keeps den > 0
    sign = "-" if value < 0 and scaled != 0 else ""
    return f"{sign}{scaled // scale}.{scaled % scale:0{places}d}"


def format_sar(halalas: int | None) -> str | None:
    if halalas is None:
        return None
    sign = "-" if halalas < 0 else ""
    return f"{sign}{abs(halalas) // 100}.{abs(halalas) % 100:02d}"


def bucket_for(days_overdue: int) -> str:
    """Current when not yet overdue (an invoice due on VD is Current); overdue starts the next day."""
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return ">90"


# ---------------------------------------------------------------- result types

@dataclass(frozen=True)
class Scope:
    name: str
    description: str
    record_counts: dict[str, int]


@dataclass
class MonthlySeries:
    name: str
    basis: str
    monthly: dict[str, int | None]  # every RP month; None = N/A
    total: int | None
    scope: Scope
    na_reasons: dict[str, str] = field(default_factory=dict)  # month or "total" -> reason
    label: str | None = None


@dataclass
class Ratio:
    numerator: int | None
    denominator: int | None
    value: Fraction | None
    reason: str | None = None

    @property
    def display(self) -> str | None:
        return display_ratio(self.value)


@dataclass(frozen=True)
class OpenInvoice:
    invoice_id: str
    customer_id: str
    due_date: object
    days_overdue: int
    open_halalas: int
    bucket: str


@dataclass
class Receivables:
    scope: Scope
    buckets: dict[str, int] | None  # None = N/A
    total: int | None
    invoices: list[OpenInvoice]  # invoices with an open balance > 0
    reason: str | None = None
    quality_status: str = "no_known_issues"


@dataclass(frozen=True)
class ConcentrationEntry:
    customer_id: str
    amount: int
    share: Fraction

    @property
    def share_display(self) -> str:
        return display_ratio(self.share)


@dataclass
class Concentration:
    basis: str
    scope: Scope
    total: int | None
    top: list[ConcentrationEntry] | None  # None = N/A
    warnings: list[str] = field(default_factory=list)  # customer IDs above the threshold (R-20)
    reason: str | None = None


@dataclass
class MetricsResult:
    blocked: bool
    block_reasons: list[str] = field(default_factory=list)
    gross_sales: MonthlySeries | None = None
    credit_notes: MonthlySeries | None = None
    net_sales: MonthlySeries | None = None
    collections: MonthlySeries | None = None
    credit_note_ratio: Ratio | None = None
    receivables: Receivables | None = None
    sales_concentration: Concentration | None = None
    receivables_concentration: Concentration | None = None
    warnings: list[Issue] = field(default_factory=list)  # R-20 from this stage
    receivables_quality: str | None = None
    receivables_quality_reasons: list[str] = field(default_factory=list)
    receivable_unreliable: dict[str, list[str]] = field(default_factory=dict)  # invoice -> rules (incl. R-13)
    dependent_payments: ExcludedAmounts | None = None  # D-13
    source_completeness_verified: bool = False


# ---------------------------------------------------------------- computation

def _reason_text(reliability: SeriesReliability) -> str:
    return "; ".join(r.message for r in reliability.reasons) or "a defective record affects this series"


def _series(name: str, basis: str, by_month: dict[str, int], scope: Scope,
            reliability: SeriesReliability | None = None, label: str | None = None) -> MonthlySeries:
    monthly, na = {}, {}
    for month in reporting_months():
        if reliability is not None and not reliability.month_reliable(month):
            monthly[month] = None
            na[month] = _reason_text(reliability)
        else:
            monthly[month] = by_month.get(month, 0)
    if any(v is None for v in monthly.values()):
        total = None
        na["total"] = "at least one month is N/A, so the reporting-period total is unknown"
    else:
        total = sum(monthly.values())
    return MonthlySeries(name, basis, monthly, total, scope, na, label)


def _concentration(basis: str, amounts: dict[str, int], total: int | None, scope: Scope,
                   na_reason: str | None) -> Concentration:
    if na_reason is not None:
        return Concentration(basis, scope, total, None, reason=na_reason)
    if total is None or total <= 0:
        return Concentration(basis, scope, total, None, reason=f"total {basis} is {format_sar(total)}; "
                                                                "shares cannot be computed")
    entries = [ConcentrationEntry(c, a, Fraction(a, total)) for c, a in amounts.items() if a != 0]
    entries.sort(key=lambda e: (-e.amount, e.customer_id))
    warnings = [e.customer_id for e in entries if e.share > CONCENTRATION_THRESHOLD]  # exact comparison
    return Concentration(basis, scope, total, entries[:TOP_N], warnings)


def compute_metrics(validation: ValidationResult) -> MetricsResult:
    if validation.blocked:
        return MetricsResult(blocked=True, block_reasons=[f"{i.rule}: {i.message}" for i in validation.issues
                                                          if i.severity is Severity.BLOCK])

    invoices = validation.accepted("invoices")  # sales scope: valid invoices issued in RP
    payments = validation.accepted("payments")  # valid, dated <= VD, invoice in sales scope
    credits = validation.accepted("credit_notes")
    in_rp = lambda d: REPORTING_PERIOD_START <= d <= REPORTING_PERIOD_END  # noqa: E731
    credits_in_rp = [c for c in credits if in_rp(c.values["credit_date"])]
    payments_in_rp = [p for p in payments if in_rp(p.values["payment_date"])]

    sales_scope = Scope("sales", "accepted invoices issued in the reporting period, with their accepted "
                                 "credit notes dated in the reporting period",
                        {"invoices": len(invoices), "credit_notes": len(credits_in_rp)})
    collections_scope = Scope("collections", "accepted payments dated in the reporting period whose invoice "
                                             "is in sales scope", {"payments": len(payments_in_rp)})

    gross_by_month, credit_by_month = defaultdict(int), defaultdict(int)
    for inv in invoices:
        gross_by_month[month_of(inv.values["issue_date"])] += inv.values["net_amount"]
    for cn in credits_in_rp:
        credit_by_month[month_of(cn.values["credit_date"])] += cn.values["net_amount"]
    collected_by_month = defaultdict(int)
    for p in payments_in_rp:
        collected_by_month[month_of(p.values["payment_date"])] += p.values["amount"]
    net_by_month = {m: gross_by_month.get(m, 0) - credit_by_month.get(m, 0) for m in reporting_months()}

    cn_reliability = validation.credit_note_reliability
    gross = _series("gross_sales", "excl. VAT", gross_by_month, sales_scope)
    credit_notes = _series("credit_notes", "excl. VAT", credit_by_month, sales_scope, cn_reliability)
    net = _series("net_sales", "excl. VAT", net_by_month, sales_scope, cn_reliability)
    collections = _series("collections", "amount as recorded (incl. VAT)", collected_by_month,
                          collections_scope, validation.collections_reliability,
                          f"{COLLECTIONS_LABEL}; {UNVERIFIED_LABEL}")

    if credit_notes.total is None:
        ratio = Ratio(None, gross.total, None, "credit-note total is N/A: " + _reason_text(cn_reliability))
    elif gross.total == 0:
        ratio = Ratio(credit_notes.total, 0, None, "gross sales are 0; the ratio is undefined")
    else:
        ratio = Ratio(credit_notes.total, gross.total, Fraction(credit_notes.total, gross.total))

    receivables, open_by_customer = _receivables(validation, invoices, payments, credits)

    net_by_customer: dict[str, int] = defaultdict(int)
    customer_of = {inv.record_id: inv.values["customer_id"] for inv in invoices}
    for inv in invoices:
        net_by_customer[inv.values["customer_id"]] += inv.values["net_amount"]
    for cn in credits_in_rp:
        net_by_customer[customer_of[cn.values["invoice_id"]]] -= cn.values["net_amount"]
    sales_conc = _concentration(
        "net sales", net_by_customer, net.total, sales_scope,
        None if cn_reliability.reliable else "a defective credit note makes net sales unknown: "
                                             + _reason_text(cn_reliability))
    receivables_conc = _concentration("open balance", open_by_customer, receivables.total, receivables.scope,
                                      receivables.reason)

    warnings = [Issue("R-20", Severity.WARN, f"{c} holds more than 30% of {conc.basis} "
                                             "(experimental threshold - not a bank policy)",
                      "customers", None, c)
                for conc in (sales_conc, receivables_conc) for c in conc.warnings]

    return MetricsResult(
        blocked=False,
        gross_sales=gross, credit_notes=credit_notes, net_sales=net, collections=collections,
        credit_note_ratio=ratio, receivables=receivables,
        sales_concentration=sales_conc, receivables_concentration=receivables_conc,
        warnings=warnings,
        receivables_quality=validation.receivables_quality.value,
        receivables_quality_reasons=list(validation.receivables_quality_reasons),
        receivable_unreliable=validation.unreliable_rules(),
        dependent_payments=replace(validation.dependent_payments),
        source_completeness_verified=validation.source_completeness_verified,
    )


def _receivables(validation: ValidationResult, invoices: list[Record], payments: list[Record],
                 credits: list[Record]) -> tuple[Receivables, dict[str, int]]:
    unreliable = set(validation.receivable_unreliable)
    scope_invoices = [inv for inv in invoices if inv.record_id not in unreliable]
    scope_ids = {inv.record_id for inv in scope_invoices}
    settled: dict[str, int] = defaultdict(int)
    links = 0
    for p in payments:  # accepted links are all dated <= VD
        if p.values["invoice_id"] in scope_ids:
            settled[p.values["invoice_id"]] += p.values["amount"]
            links += 1
    for cn in credits:
        if cn.values["invoice_id"] in scope_ids:
            settled[cn.values["invoice_id"]] += cn.values["total_amount"]
            links += 1
    scope = Scope("receivables", "sales-scope invoices not marked receivable unreliable, with their accepted "
                                 "payments and credit notes dated on or before the valuation date",
                  {"invoices": len(scope_invoices), "links": links,
                   "receivable_unreliable_excluded": len(unreliable)})
    quality = validation.receivables_quality.value

    if not scope_invoices:
        return Receivables(scope, None, None, [], "receivables scope is empty", quality), {}

    buckets = {b: 0 for b in BUCKETS}
    open_invoices, open_by_customer = [], defaultdict(int)
    for inv in scope_invoices:
        balance = inv.values["total_amount"] - settled[inv.record_id]
        if balance == 0:
            continue  # fully settled invoices do not appear
        days = (VALUATION_DATE - inv.values["due_date"]).days
        bucket = bucket_for(days)
        buckets[bucket] += balance
        open_by_customer[inv.values["customer_id"]] += balance
        open_invoices.append(OpenInvoice(inv.record_id, inv.values["customer_id"], inv.values["due_date"],
                                         days, balance, bucket))
    return Receivables(scope, buckets, sum(buckets.values()), open_invoices, None, quality), open_by_customer
