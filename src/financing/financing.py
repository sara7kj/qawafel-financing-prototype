"""Illustrative financing estimate (requirements US-08, D-09).

Estimate = advance rate x eligible receivables, where eligible receivables are the
reliable open balances (receivables scope) that are at most 90 days overdue.
The rate is a declared, adjustable assumption - not a bank policy or credit decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from financing.metrics import MetricsResult, round_half_up_div

DEFAULT_RATE_PERCENT = 70
MAX_DAYS_OVERDUE = 90
ILLUSTRATIVE_LABEL = "Illustrative assumption - not a bank policy or credit decision."
UNCERTAIN_LABEL = "Receivables have unresolved links; open balances may be overstated."


@dataclass(frozen=True)
class FinancingEstimate:
    rate_percent: int
    eligible_halalas: int | None  # None = N/A
    amount_halalas: int | None  # None = N/A
    reason: str | None
    receivables_quality: str | None
    eligibility_rule: str = f"reliable open balances at most {MAX_DAYS_OVERDUE} days overdue at the valuation date"
    label: str = ILLUSTRATIVE_LABEL


def validate_rate(rate_percent: object) -> int:
    """A whole percent from 0 to 100; anything else is rejected."""
    if isinstance(rate_percent, bool) or not isinstance(rate_percent, int):
        raise ValueError(f"advance rate must be a whole percent (int), got {rate_percent!r}")
    if not 0 <= rate_percent <= 100:
        raise ValueError(f"advance rate must be between 0 and 100, got {rate_percent}")
    return rate_percent


def apply_rate(eligible_halalas: int, rate_percent: int) -> int:
    """eligible x rate / 100, ROUND_HALF_UP to the halala."""
    return round_half_up_div(eligible_halalas * validate_rate(rate_percent), 100)


def estimate_financing(metrics: MetricsResult, rate_percent: int = DEFAULT_RATE_PERCENT) -> FinancingEstimate:
    rate = validate_rate(rate_percent)
    if metrics.blocked:
        return FinancingEstimate(rate, None, None, "analysis is blocked", None)
    receivables = metrics.receivables
    if receivables.buckets is None:
        return FinancingEstimate(rate, None, None, receivables.reason, metrics.receivables_quality)
    eligible = sum(i.open_halalas for i in receivables.invoices if i.days_overdue <= MAX_DAYS_OVERDUE)
    if metrics.receivables_quality == "unresolved_links":
        return FinancingEstimate(rate, eligible, None,
                                 "receivables_quality_status is unresolved_links: a defective payment or credit "
                                 "note cannot be linked to an invoice. " + UNCERTAIN_LABEL,
                                 metrics.receivables_quality)
    return FinancingEstimate(rate, eligible, apply_rate(eligible, rate), None, metrics.receivables_quality)
