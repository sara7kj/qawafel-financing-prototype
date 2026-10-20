"""Workbook schema, fixed dates and shared result types.

Source of truth: docs/data-design.md §1 (schema) and docs/requirements.md §3, §5 (rules).
All amounts are integer halalas (1 SAR = 100 halalas).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

REPORTING_PERIOD_START = date(2025, 10, 1)
REPORTING_PERIOD_END = date(2026, 9, 30)
VALUATION_DATE = date(2026, 9, 30)
CURRENCY = "SAR"
VAT_TOLERANCE_HALALAS = 1  # |net + VAT - total| <= 0.01 SAR is accepted (R-16)


@dataclass(frozen=True)
class SheetSpec:
    name: str
    id_column: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    amount_columns: tuple[str, ...] = ()
    date_columns: tuple[str, ...] = ()
    invoice_ref_column: str | None = None

    @property
    def columns(self) -> tuple[str, ...]:
        return self.required + self.optional


SHEETS: dict[str, SheetSpec] = {
    "customers": SheetSpec(
        "customers", "customer_id",
        required=("customer_id", "customer_name", "payment_terms_days"),
        optional=("city", "segment"),
    ),
    "invoices": SheetSpec(
        "invoices", "invoice_id",
        required=("invoice_id", "customer_id", "issue_date", "due_date", "currency",
                  "net_amount", "vat_amount", "total_amount"),
        amount_columns=("net_amount", "vat_amount", "total_amount"),
        date_columns=("issue_date", "due_date"),
    ),
    "payments": SheetSpec(
        "payments", "payment_id",
        required=("payment_id", "invoice_id", "payment_date", "amount"),
        amount_columns=("amount",),
        date_columns=("payment_date",),
        invoice_ref_column="invoice_id",
    ),
    "credit_notes": SheetSpec(
        "credit_notes", "credit_note_id",
        required=("credit_note_id", "invoice_id", "credit_date",
                  "net_amount", "vat_amount", "total_amount"),
        optional=("reason",),
        amount_columns=("net_amount", "vat_amount", "total_amount"),
        date_columns=("credit_date",),
        invoice_ref_column="invoice_id",
    ),
}
LINK_SHEETS = ("payments", "credit_notes")
LINK_DATE_COLUMN = {"payments": "payment_date", "credit_notes": "credit_date"}
# Amount used for the "Known recorded amount excluded" (data-design §5.0).
RECORDED_AMOUNT_COLUMN = {"invoices": "total_amount", "payments": "amount", "credit_notes": "total_amount"}


class Severity(str, Enum):
    """Problem classes of requirements §3.1. Dependent exclusion and 'receivable unreliable'
    are not problem classes: see RecordStatus.DEPENDENT and ValidationResult.receivable_unreliable."""

    BLOCK = "BLOCK"
    QUARANTINE = "QUARANTINE"
    OUT_OF_PERIOD = "OUT OF PERIOD"
    WARN = "WARN"


class RecordStatus(str, Enum):
    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"
    OUT_OF_PERIOD = "out_of_period"
    DEPENDENT = "dependent"  # excluded only because its invoice is quarantined (D-13)


class ReceivablesQuality(str, Enum):
    NO_KNOWN_ISSUES = "no_known_issues"
    INVOICES_EXCLUDED = "invoices_excluded"
    UNRESOLVED_LINKS = "unresolved_links"


@dataclass(frozen=True)
class FieldError:
    column: str
    kind: str  # "missing" | "invalid"
    detail: str
    raw: Any


@dataclass
class Record:
    """One spreadsheet row: its Excel row number, the original cell values and parsed fields."""

    sheet: str
    row: int
    raw: dict[str, Any]
    values: dict[str, Any]  # parsed value per column; None when missing or invalid
    errors: dict[str, FieldError] = field(default_factory=dict)

    @property
    def record_id(self) -> str | None:
        return self.values.get(SHEETS[self.sheet].id_column)

    @property
    def label(self) -> str:
        return f"{self.sheet}:{self.record_id or '<missing id>'} row {self.row}"


@dataclass(frozen=True)
class Issue:
    rule: str
    severity: Severity
    message: str
    sheet: str | None = None
    row: int | None = None
    record_id: str | None = None
    column: str | None = None
    raw_value: Any = None
    affected_invoice: str | None = None


@dataclass
class LoadResult:
    rows: dict[str, list[Record]]
    issues: list[Issue]
    source_name: str | None = None

    @property
    def blocked(self) -> bool:
        return any(i.severity is Severity.BLOCK for i in self.issues)


@dataclass
class ValidatedRecord:
    record: Record
    status: RecordStatus
    rules: list[str] = field(default_factory=list)  # the record's own broken rules
    # For links excluded because of their invoice (DEPENDENT, or OUT_OF_PERIOD via R-14):
    dependent_on: str | None = None  # the invoice ID
    dependency_rules: list[str] = field(default_factory=list)  # why that invoice is excluded


@dataclass(frozen=True)
class UnreliableReason:
    """Why an invoice is 'receivable unreliable': kept in sales if sound, removed from receivables."""

    rule: str
    message: str
    sheet: str  # the record that caused it (a defective link, or the invoice itself for R-13)
    row: int
    record_id: str | None


@dataclass
class SeriesReliability:
    """Months of a monthly series that cannot be computed reliably (affected-month rule)."""

    affected_months: set[str] = field(default_factory=set)
    all_months: bool = False  # a defective record's month is unknown
    reasons: list[Issue] = field(default_factory=list)

    @property
    def reliable(self) -> bool:
        return not self.all_months and not self.affected_months

    def month_reliable(self, month: str) -> bool:
        return not self.all_months and month not in self.affected_months


@dataclass
class ExcludedAmounts:
    """'Known recorded amount excluded': recorded SAR values, not verified (data-design §5.0).

    Each excluded record is counted once: either in `known_records` or in exactly one
    of the reason counts, so the reason counts plus `known_records` equal `records`.
    """

    records: int = 0
    known_records: int = 0
    known_recorded_halalas: int = 0
    unreadable_or_not_positive: int = 0
    duplicate_id: int = 0
    non_sar: int = 0
    unknown_currency: int = 0


@dataclass
class ValidationResult:
    issues: list[Issue]
    records: dict[str, list[ValidatedRecord]] = field(default_factory=dict)
    receivable_unreliable: dict[str, list[UnreliableReason]] = field(default_factory=dict)
    collections_reliability: SeriesReliability = field(default_factory=SeriesReliability)
    credit_note_reliability: SeriesReliability = field(default_factory=SeriesReliability)
    receivables_quality: ReceivablesQuality = ReceivablesQuality.NO_KNOWN_ISSUES
    receivables_quality_reasons: list[str] = field(default_factory=list)
    excluded_amounts: dict[str, ExcludedAmounts] = field(default_factory=dict)
    dependent_payments: ExcludedAmounts = field(default_factory=ExcludedAmounts)
    source_completeness_verified: bool = False  # always False in this prototype (requirements §3.2)

    @property
    def blocked(self) -> bool:
        return any(i.severity is Severity.BLOCK for i in self.issues)

    def with_status(self, sheet: str, status: RecordStatus) -> list[ValidatedRecord]:
        return [r for r in self.records.get(sheet, []) if r.status is status]

    def accepted(self, sheet: str) -> list[Record]:
        return [r.record for r in self.with_status(sheet, RecordStatus.ACCEPTED)]

    def issues_for(self, rule: str) -> list[Issue]:
        return [i for i in self.issues if i.rule == rule]

    def unreliable_rules(self) -> dict[str, list[str]]:
        """Invoice -> distinct rules that made it receivable unreliable."""
        return {inv: list(dict.fromkeys(r.rule for r in reasons))
                for inv, reasons in self.receivable_unreliable.items()}

    def status_counts(self) -> dict[str, dict[str, int]]:
        """Records per sheet and status; each record is counted once whatever its number of issues."""
        return {sheet: {s.value: sum(1 for r in rows if r.status is s) for s in RecordStatus}
                for sheet, rows in self.records.items()}
