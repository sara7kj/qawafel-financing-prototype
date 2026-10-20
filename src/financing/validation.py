"""Validation: record, reference, period, dependency, propagation and over-settlement rules.

Implements docs/requirements.md §3-§5 in the order of docs/data-design.md §7.
It decides which records are accepted and which indicator inputs are unreliable;
it computes no indicators. R-20 (concentration) belongs to the metrics stage.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date

from financing.schema import (
    CURRENCY,
    LINK_DATE_COLUMN,
    LINK_SHEETS,
    RECORDED_AMOUNT_COLUMN,
    REPORTING_PERIOD_END,
    REPORTING_PERIOD_START,
    SHEETS,
    VALUATION_DATE,
    VAT_TOLERANCE_HALALAS,
    ExcludedAmounts,
    Issue,
    LoadResult,
    ReceivablesQuality,
    Record,
    RecordStatus,
    SeriesReliability,
    Severity,
    UnreliableReason,
    ValidatedRecord,
    ValidationResult,
)


def month_of(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def reporting_months() -> list[str]:
    months, y, m = [], REPORTING_PERIOD_START.year, REPORTING_PERIOD_START.month
    while (y, m) <= (REPORTING_PERIOD_END.year, REPORTING_PERIOD_END.month):
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def normalise_name(name: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", name.casefold()).split())


def validate(load: LoadResult) -> ValidationResult:
    if load.blocked:
        return ValidationResult(issues=list(load.issues))
    return _Validator(load).run()


class _Validator:
    def __init__(self, load: LoadResult):
        self.rows = {name: load.rows.get(name, []) for name in SHEETS}
        self.issues: list[Issue] = list(load.issues)
        self.status: dict[tuple[str, int], RecordStatus] = {}
        self.rules: dict[tuple[str, int], list[str]] = defaultdict(list)
        self.unreliable: dict[str, list[UnreliableReason]] = {}
        self.dependency: dict[tuple[str, int], tuple[str, list[str]]] = {}  # link -> (invoice, its rules)
        self.unresolved_rules: set[str] = set()
        self.collections = SeriesReliability()
        self.credit_notes = SeriesReliability()

    # ------------------------------------------------------------ helpers

    @staticmethod
    def key(rec: Record) -> tuple[str, int]:
        return rec.sheet, rec.row

    def status_of(self, rec: Record) -> RecordStatus:
        return self.status.get(self.key(rec), RecordStatus.ACCEPTED)

    def flag(self, rec: Record, rule: str, severity: Severity, status: RecordStatus | None,
             message: str, column: str | None = None) -> None:
        if status is not None:
            self.status[self.key(rec)] = status
        if rule not in self.rules[self.key(rec)]:
            self.rules[self.key(rec)].append(rule)
        self.issues.append(Issue(rule, severity, message, rec.sheet, rec.row, rec.record_id, column,
                                 rec.raw.get(column) if column else None))

    def quarantine(self, rec: Record, rule: str, message: str, column: str | None = None) -> None:
        self.flag(rec, rule, Severity.QUARANTINE, RecordStatus.QUARANTINED, message, column)

    def check_amounts(self, rec: Record, rule: str) -> None:
        for column in SHEETS[rec.sheet].amount_columns:
            error = rec.errors.get(column)
            if error:
                self.quarantine(rec, rule, f"{column} is {error.kind}: {error.detail}", column)
            elif rec.values[column] <= 0:
                self.quarantine(rec, rule, f"{column} must be greater than 0", column)

    def check_vat_arithmetic(self, rec: Record) -> None:
        net, vat, total = (rec.values.get(c) for c in ("net_amount", "vat_amount", "total_amount"))
        if None not in (net, vat, total) and abs(net + vat - total) > VAT_TOLERANCE_HALALAS:
            self.quarantine(rec, "R-16", "net_amount + vat_amount differs from total_amount by more "
                                         "than 0.01 SAR; amounts are not corrected", "total_amount")

    def check_ids(self, records: list[Record], missing_rule: str, duplicate_rule: str) -> Counter:
        counts = Counter(r.record_id for r in records if r.record_id)
        id_column = SHEETS[records[0].sheet].id_column if records else None
        for rec in records:
            if rec.record_id is None:
                self.quarantine(rec, missing_rule, f"{id_column} is missing", id_column)
            elif counts[rec.record_id] > 1:
                self.quarantine(rec, duplicate_rule, f"{id_column} '{rec.record_id}' is duplicated", id_column)
        return counts

    # ------------------------------------------------------------ steps (data-design §7)

    def run(self) -> ValidationResult:
        customers, invoices = self.rows["customers"], self.rows["invoices"]

        # 4. record rules and 5. reference rules
        customer_counts = self.check_ids(customers, "R-01", "R-02")
        self.invoice_counts = self.check_ids(invoices, "R-03", "R-03")
        for rec in invoices:
            self.check_invoice(rec, customer_counts)
        self.invoice_ids_present = set(self.invoice_counts)
        self.link_counts = {sheet: self.check_ids(self.rows[sheet], "R-08", "R-09") for sheet in LINK_SHEETS}
        for sheet in LINK_SHEETS:
            for rec in self.rows[sheet]:
                self.check_link(rec)

        # 6. period rules, 7. dependencies
        for rec in invoices:
            issue_date = rec.values["issue_date"]
            if self.status_of(rec) is RecordStatus.ACCEPTED and not (
                    REPORTING_PERIOD_START <= issue_date <= REPORTING_PERIOD_END):
                self.flag(rec, "R-14", Severity.OUT_OF_PERIOD, RecordStatus.OUT_OF_PERIOD,
                          f"issued {issue_date}, outside the reporting period")
        invoice_status = self.invoice_status_by_id()
        for sheet in LINK_SHEETS:
            for rec in self.rows[sheet]:
                self.apply_period_and_dependency(rec, invoice_status)

        # 8. S-03
        self.sales_scope = {r.record_id: r for r in invoices if self.status_of(r) is RecordStatus.ACCEPTED}
        if not self.sales_scope:
            self.issues.append(Issue("S-03", Severity.BLOCK, "no valid invoice remains in the reporting period"))
            return self.result()

        # 9. propagation, 10. over-settlement, 11. warnings
        for sheet in LINK_SHEETS:
            for rec in self.rows[sheet]:
                if self.status_of(rec) is RecordStatus.QUARANTINED:
                    self.propagate(rec)
        self.check_over_settlement()
        self.warnings()
        return self.result()

    def check_invoice(self, rec: Record, customer_counts: Counter) -> None:
        self.check_amounts(rec, "R-05")
        for column in ("issue_date", "due_date"):
            error = rec.errors.get(column)
            if error:
                self.quarantine(rec, "R-06", f"{column} is {error.kind}: {error.detail}", column)
        issue_date, due_date = rec.values["issue_date"], rec.values["due_date"]
        if issue_date and due_date and due_date < issue_date:
            self.quarantine(rec, "R-06", "due_date is before issue_date", "due_date")
        if rec.values["currency"] != CURRENCY:
            self.quarantine(rec, "R-07", f"currency must be {CURRENCY}", "currency")
        self.check_vat_arithmetic(rec)
        customer_id = rec.values["customer_id"]
        if customer_id is not None and customer_counts[customer_id] > 1:
            self.quarantine(rec, "R-04", f"customer_id '{customer_id}' is ambiguous (duplicated)", "customer_id")
        elif customer_id is None or customer_counts[customer_id] == 0:
            self.quarantine(rec, "R-04", "customer_id is missing or unknown", "customer_id")

    def check_link(self, rec: Record) -> None:
        self.check_amounts(rec, "R-10")
        date_column = LINK_DATE_COLUMN[rec.sheet]
        error = rec.errors.get(date_column)
        if error:
            self.quarantine(rec, "R-11", f"{date_column} is {error.kind}: {error.detail}", date_column)
        if rec.sheet == "credit_notes":
            self.check_vat_arithmetic(rec)
        if rec.values["invoice_id"] not in self.invoice_ids_present:
            self.quarantine(rec, "R-12", "invoice_id is missing or matches no invoice", "invoice_id")

    def invoice_status_by_id(self) -> dict[str, RecordStatus]:
        """Quarantine wins when an ID has several rows (all copies of a duplicate are quarantined)."""
        status: dict[str, RecordStatus] = {}
        for rec in self.rows["invoices"]:
            if rec.record_id and status.get(rec.record_id) is not RecordStatus.QUARANTINED:
                status[rec.record_id] = self.status_of(rec)
        return status

    def apply_period_and_dependency(self, rec: Record, invoice_status: dict[str, RecordStatus]) -> None:
        if self.status_of(rec) is not RecordStatus.ACCEPTED:
            return
        d = rec.values[LINK_DATE_COLUMN[rec.sheet]]
        if d > VALUATION_DATE:
            self.flag(rec, "R-15", Severity.OUT_OF_PERIOD, RecordStatus.OUT_OF_PERIOD,
                      f"dated {d}, after the valuation date; excluded from valuation-date calculations")
            return
        invoice_id = rec.values["invoice_id"]
        linked = invoice_status[invoice_id]
        if linked is RecordStatus.QUARANTINED:
            # Not a problem class: the record is sound; it is excluded because of its invoice (D-13).
            self.status[self.key(rec)] = RecordStatus.DEPENDENT
            self.dependency[self.key(rec)] = (invoice_id, self.invoice_rules(invoice_id))
        elif linked is RecordStatus.OUT_OF_PERIOD:
            self.flag(rec, "R-14", Severity.OUT_OF_PERIOD, RecordStatus.OUT_OF_PERIOD,
                      f"invoice {invoice_id} is outside the reporting period")
            self.dependency[self.key(rec)] = (invoice_id, ["R-14"])

    def invoice_rules(self, invoice_id: str) -> list[str]:
        """Rules of every quarantined row carrying this invoice ID."""
        return list(dict.fromkeys(rule for r in self.rows["invoices"] if r.record_id == invoice_id
                                  for rule in self.rules.get(self.key(r), [])))

    def propagate(self, rec: Record) -> None:
        """Effect of an own-defect payment or credit note (requirements §3.4)."""
        d = rec.values[LINK_DATE_COLUMN[rec.sheet]]
        if d is not None and d > VALUATION_DATE:
            return  # no effect on valuation-date calculations or RP series
        invoice_id = rec.values["invoice_id"]
        unresolved = invoice_id not in self.invoice_ids_present
        if not unresolved and invoice_id not in self.sales_scope:
            return  # its invoice is already excluded from every scope

        series = self.collections if rec.sheet == "payments" else self.credit_notes
        rules = ", ".join(self.rules[self.key(rec)])
        if d is None:
            series.all_months = True
            effect = "its month is unknown, so the whole series is N/A"
        elif REPORTING_PERIOD_START <= d:
            series.affected_months.add(month_of(d))
            effect = f"month {month_of(d)} is N/A"
        else:
            effect = "dated before the reporting period; no month affected"
        series.reasons.append(Issue(rules, Severity.QUARANTINE, f"{rec.label} is defective: {effect}",
                                    rec.sheet, rec.row, rec.record_id))

        if unresolved:
            self.unresolved_rules.update(self.rules[self.key(rec)])
            return
        for rule in self.rules[self.key(rec)]:
            self.mark_unreliable(invoice_id, UnreliableReason(
                rule, f"{rec.label} is defective", rec.sheet, rec.row, rec.record_id))
        self.issues = [replace(i, affected_invoice=invoice_id)
                       if (i.sheet, i.row) == self.key(rec) and i.severity is Severity.QUARANTINE else i
                       for i in self.issues]

    def mark_unreliable(self, invoice_id: str, reason: UnreliableReason) -> None:
        self.unreliable.setdefault(invoice_id, []).append(reason)

    def check_over_settlement(self) -> None:
        settled: dict[str, int] = defaultdict(int)
        for sheet in LINK_SHEETS:
            amount_column = RECORDED_AMOUNT_COLUMN[sheet]
            for rec in self.rows[sheet]:
                if self.status_of(rec) is RecordStatus.ACCEPTED:  # accepted links are dated <= VD
                    settled[rec.values["invoice_id"]] += rec.values[amount_column]
        for invoice_id, rec in self.sales_scope.items():
            if settled[invoice_id] > rec.values["total_amount"]:
                # An invoice state, not a problem class: kept in sales, removed from receivables.
                self.mark_unreliable(invoice_id, UnreliableReason(
                    "R-13", "payments + credit notes dated on or before the valuation date exceed "
                            "the invoice total (over-settled)", rec.sheet, rec.row, invoice_id))

    def warnings(self) -> None:
        # R-17: accepted link dated before its invoice
        for sheet in LINK_SHEETS:
            for rec in self.rows[sheet]:
                invoice = self.sales_scope.get(rec.values["invoice_id"])
                d = rec.values[LINK_DATE_COLUMN[sheet]]
                if self.status_of(rec) is RecordStatus.ACCEPTED and invoice and d < invoice.values["issue_date"]:
                    self.issues.append(Issue("R-17", Severity.WARN, "dated before its invoice's issue date",
                                             sheet, rec.row, rec.record_id, affected_invoice=invoice.record_id))
        # R-18: one normalised name under several customer IDs
        ids_by_name: dict[str, set[str]] = defaultdict(set)
        for rec in self.rows["customers"]:
            if rec.record_id and rec.values.get("customer_name"):
                ids_by_name[normalise_name(rec.values["customer_name"])].add(rec.record_id)
        flagged = sorted(i for ids in ids_by_name.values() if len(ids) > 1 for i in ids)
        for rec in self.rows["customers"]:
            if rec.record_id in flagged:
                self.issues.append(Issue("R-18", Severity.WARN, "the same customer name is used under several IDs",
                                         "customers", rec.row, rec.record_id))
        # R-19: months with no invoices in sales scope
        months = {month_of(r.values["issue_date"]) for r in self.sales_scope.values()}
        for month in reporting_months():
            if month not in months:
                self.issues.append(Issue("R-19", Severity.WARN, f"no invoices recorded in {month}; the data alone "
                                         "cannot show whether this means no activity or an incomplete export"))
        # R-21
        self.issues.append(Issue("R-21", Severity.WARN,
                                 "payments are not verified against bank statements (unverified)"))

    # ------------------------------------------------------------ result

    def currency_of(self, rec: Record) -> str | None:
        """An invoice's own currency; a link inherits its invoice's currency (data-design §1.4).

        None means unknown: a missing currency, a missing or unknown invoice reference, or a
        reference to a duplicated invoice ID (an ambiguous link). SAR is never assumed.
        """
        if rec.sheet == "invoices":
            return rec.values.get("currency")
        invoice_id = rec.values.get("invoice_id")
        if invoice_id is None or self.invoice_counts[invoice_id] != 1:
            return None
        return next(r.values.get("currency") for r in self.rows["invoices"] if r.record_id == invoice_id)

    def amount_summary(self, sheet: str, status: RecordStatus) -> ExcludedAmounts:
        """Each record lands in exactly one bucket, in this order of precedence."""
        summary = ExcludedAmounts()
        counts = self.invoice_counts if sheet == "invoices" else self.link_counts[sheet]
        for rec in self.rows[sheet]:
            if self.status_of(rec) is not status:
                continue
            summary.records += 1
            amount = rec.values.get(RECORDED_AMOUNT_COLUMN[sheet])
            currency = self.currency_of(rec)
            if rec.record_id and counts[rec.record_id] > 1:
                summary.duplicate_id += 1
            elif currency is None:
                summary.unknown_currency += 1
            elif currency != CURRENCY:
                summary.non_sar += 1
            elif amount is None or amount <= 0:
                summary.unreadable_or_not_positive += 1
            else:
                summary.known_records += 1
                summary.known_recorded_halalas += amount
        return summary

    def result(self) -> ValidationResult:
        records = {sheet: [ValidatedRecord(r, self.status_of(r), list(self.rules.get(self.key(r), [])),
                                           *self.dependency.get(self.key(r), (None, [])))
                           for r in rows] for sheet, rows in self.rows.items()}
        quarantined_invoice_rules = {rule for r in self.rows["invoices"]
                                     if self.status_of(r) is RecordStatus.QUARANTINED
                                     for rule in self.rules[self.key(r)]}
        unreliable_rules = {reason.rule for reasons in self.unreliable.values() for reason in reasons}
        if self.unresolved_rules:
            quality = ReceivablesQuality.UNRESOLVED_LINKS
        elif quarantined_invoice_rules or unreliable_rules:
            quality = ReceivablesQuality.INVOICES_EXCLUDED
        else:
            quality = ReceivablesQuality.NO_KNOWN_ISSUES
        return ValidationResult(
            issues=self.issues,
            records=records,
            receivable_unreliable=dict(self.unreliable),
            collections_reliability=self.collections,
            credit_note_reliability=self.credit_notes,
            receivables_quality=quality,
            receivables_quality_reasons=sorted(quarantined_invoice_rules | unreliable_rules | self.unresolved_rules),
            excluded_amounts={sheet: self.amount_summary(sheet, RecordStatus.QUARANTINED)
                              for sheet in ("invoices", "payments", "credit_notes")},
            dependent_payments=self.amount_summary("payments", RecordStatus.DEPENDENT),
        )
