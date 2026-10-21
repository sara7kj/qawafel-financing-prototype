"""Optional AI analyst summary (requirements US-11, D-10).

Turns the computed JSON package into a short analyst note. The model never writes a
number: it writes fact placeholders such as {F07}, and this module fills them from the
package. The note supports a review; it is not a credit decision.

What the checks guarantee - and what they do not:
- Every number shown equals the package value (placeholders only; digits elsewhere are rejected).
- Every placeholder exists and is listed in the same point's fact_ids.
- Risk-only facts (e.g. concentration warnings, >90 days) cannot be cited as strengths, and a
  small set of wording checks rejects mislabelling such as calling collections "sales".
- They do NOT guarantee that the reasoning is right or that each number is described
  correctly in every possible wording. An analyst must read the note before using it.
- Data limits (N/A items, unverified collections, receivables status) are shown in a section
  built by code from the results, never from the model's wording.

The SDK (`anthropic`) is optional: without it, or without credentials, the feature reports
"unavailable" and the rest of the app is unaffected. Install: pip install -r requirements-ai.txt
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from financing.rule_summary import data_limits, pct, sar  # data_limits: code-built section shown with the summary

PROMPT_VERSION = "analyst-summary-v1"
DEFAULT_MODEL = "claude-sonnet-5-5"
MODEL_ENV_VAR = "FINANCING_AI_MODEL"
TIMEOUT_SECONDS_PER_ATTEMPT = 30.0  # per HTTP attempt, not for the whole operation
MAX_RETRIES = 2  # the SDK retries only connection errors, timeouts, 408/409/429 and 5xx
MAX_TOKENS = 4000
AI_LABEL = "AI-generated from computed indicators - verify before use. Not a credit decision."

_PLACEHOLDER = re.compile(r"\{(F\d{2,3})\}")
_DIGIT = re.compile(r"\d")
_FORBIDDEN = re.compile(
    r"\b(approv\w*|reject\w*|creditworth\w*|credit[- ]?score\w*|probability of default|likelihood of default|"
    r"recommend\w*|should (?:be )?(?:lend|financ|fund)\w*|guarantee\w*|safe to lend)\b", re.IGNORECASE)


def configured_model() -> str:
    return os.environ.get(MODEL_ENV_VAR, "").strip() or DEFAULT_MODEL


# ---------------------------------------------------------------- facts

@dataclass(frozen=True)
class Fact:
    id: str
    label: str
    value: str | None  # display text; None = N/A
    reason: str | None = None  # why it is N/A
    topic: str = "other"  # sales | collections | receivables | financing | concentration | quality | rule | customer
    risk_only: bool = False  # cannot support a "strength" point

    @property
    def display(self) -> str:
        return self.value if self.value is not None else "N/A"


_sar, _pct = sar, pct  # shared display helpers (rule_summary)


def build_facts(package: dict) -> list[Fact]:
    """Deterministic fact sheet from the exported package (same values as the UI and JSON)."""
    raw: list[dict] = []

    def add(label, value, *, reason=None, topic="other", risk_only=False):
        raw.append(dict(label=label, value=value, reason=reason, topic=topic, risk_only=risk_only))

    ind, fin, dq = package["indicators"], package["financing_estimate"], package["data_quality"]
    for key, label, topic in (("gross_sales", "Gross sales over the reporting period (excl. VAT)", "sales"),
                              ("credit_notes", "Credit notes over the reporting period (excl. VAT)", "credit_notes"),
                              ("net_sales", "Net sales over the reporting period (excl. VAT)", "sales"),
                              ("collections", "Collections from accepted records only (incl. VAT, unverified)",
                               "collections")):
        series = ind[key]
        add(label, _sar(series["total"]), reason=series["na_reasons"].get("total"), topic=topic)
    ratio = ind["credit_note_ratio"]
    add("Credit-note ratio (credit notes / gross sales, excl. VAT)", _pct(ratio["value"]),
        reason=ratio.get("reason"), topic="credit_notes")

    rec = ind["receivables"]
    add("Open receivables at the valuation date (incl. VAT, reliable invoices only)", _sar(rec["total"]),
        reason=rec["reason"], topic="receivables")
    for bucket, label in (("current", "not yet due"), ("1-30", "1-30 days overdue"), ("31-60", "31-60 days overdue"),
                          ("61-90", "61-90 days overdue"), (">90", "more than 90 days overdue")):
        add(f"Open receivables {label}", _sar(rec["aging"][bucket]) if rec["aging"] else None,
            reason=rec["reason"], topic="receivables", risk_only=bucket in ("61-90", ">90"))
    add("Eligible receivables for the illustrative estimate", _sar(fin["eligible_receivables"]),
        reason=fin["reason"] if fin["eligible_receivables"] is None else None, topic="financing")
    add(f"Illustrative financing estimate at {fin['advance_rate_percent']}% (assumption, not a bank policy)",
        _sar(fin["value"]), reason=fin["reason"], topic="financing")

    for key, basis in (("sales_concentration", "net sales"), ("receivables_concentration", "open receivables")):
        conc = ind[key]
        if conc["top"] is None:
            add(f"Customer concentration by {basis}", None, reason=conc["reason"], topic="concentration")
            continue
        for rank, entry in enumerate(conc["top"], 1):
            warned = entry["customer_id"] in conc["warnings"]
            add(f"Customer ranked {rank} by {basis}", entry["customer_id"], topic="customer")
            add(f"Share of {basis} held by {entry['customer_id']}"
                + (" (above the experimental 30% threshold, R-20)" if warned else ""),
                _pct(entry["share"]), topic="concentration", risk_only=warned)

    status = dq["receivables_quality_status"]
    add("Receivables quality status", status["value"], topic="quality",
        risk_only=status["value"] != "no_known_issues")
    counts = dq["record_counts_by_status"]
    for sheet in ("invoices", "payments", "credit_notes"):
        add(f"{sheet.replace('_', ' ').capitalize()} quarantined", str(counts[sheet]["quarantined"]),
            topic="quality", risk_only=True)
        add(f"{sheet.replace('_', ' ').capitalize()} out of period", str(counts[sheet]["out_of_period"]),
            topic="quality")
    add("Payments excluded because their invoice is quarantined (D-13)",
        str(dq["dependent_payments_d13"]["records"]), topic="quality", risk_only=True)
    add("Invoices marked receivable unreliable", str(len(dq["receivable_unreliable_invoices"])),
        topic="quality", risk_only=True)

    rule_counts: dict[str, int] = {}
    for row in dq["issues"]:
        rule_counts[row["rule"]] = rule_counts.get(row["rule"], 0) + 1
    for rule in sorted(rule_counts):
        add(f"Rule {rule} (appears {rule_counts[rule]} time(s) in the issue table)", rule, topic="rule",
            risk_only=rule != "R-21")

    return [Fact(id=f"F{n:02d}", **item) for n, item in enumerate(raw, 1)]


# ---------------------------------------------------------------- model output schema

def _output_models():
    """Pydantic models for messages.parse(); imported lazily so pydantic stays optional."""
    from pydantic import BaseModel, ConfigDict

    class Point(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: Literal["strength", "risk", "data_quality", "question"]
        text: str
        fact_ids: list[str]

    class SummaryOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        headline: str
        headline_fact_ids: list[str]
        points: list[Point]

    return Point, SummaryOutput


SYSTEM_PROMPT = """You write a short briefing for a credit analyst who is reviewing an SME financing \
application. You receive a numbered fact sheet computed by software. Your briefing helps the analyst \
decide what to look at; the decision belongs to the analyst and the bank.

Rules:
- Refer to a fact only through its placeholder, e.g. {F07}. Never write digits yourself - not for \
amounts, percentages, counts, customer IDs, rule codes or dates. Every placeholder you use in a point \
must also appear in that point's fact_ids.
- Describe each fact with the meaning in its label. Collections are cash recorded by the applicant \
(including VAT, unverified); sales exclude VAT. Never call collections sales, or sales collections, and \
never compare or divide them.
- A fact marked [risk] can only appear in risk, data_quality or question points, never as a strength.
- A fact with value N/A is unknown: say it is unavailable and why; never estimate it.
- Do not approve, reject, score or recommend financing, and do not talk about default probability.
- Write a one-sentence headline and about 4 to 8 points in plain English. Questions should be ones the \
analyst can put to the applicant or the bank."""


def _fact_sheet_text(facts: list[Fact]) -> str:
    lines = []
    for f in facts:
        tag = " [risk]" if f.risk_only else ""
        value = f.display + (f" (reason: {f.reason})" if f.value is None and f.reason else "")
        lines.append(f"{f.id}{tag} | {f.label} | {value}")
    return "\n".join(lines)


# ---------------------------------------------------------------- validation

class SummaryRejected(ValueError):
    pass


_TOPIC_TERMS = {"collections": "collect", "sales": "sales", "credit_notes": "credit", "receivables": "receivable"}
_CONFLICTS = {"collections": ("sales", "revenue", "turnover", "sold"),
              "sales": ("collected", "collections", "cash", "received")}


def _check_text(text: str, fact_ids: list[str], facts: dict[str, Fact], kind: str) -> None:
    used = _PLACEHOLDER.findall(text)
    for fid in used:
        if fid not in facts:
            raise SummaryRejected(f"unknown fact {fid}")
        if fid not in fact_ids:
            raise SummaryRejected(f"{fid} is used but not listed in fact_ids")
    for fid in fact_ids:
        if fid not in facts:
            raise SummaryRejected(f"unknown fact {fid} in fact_ids")
    if _DIGIT.search(_PLACEHOLDER.sub("", text)):
        raise SummaryRejected("a number was written outside the fact placeholders")
    if _FORBIDDEN.search(text):
        raise SummaryRejected("decision or recommendation wording is not allowed")
    for fid in used:
        fact = facts[fid]
        if kind == "strength" and fact.risk_only:
            raise SummaryRejected(f"{fid} is a risk fact and cannot support a strength")
        # Heuristic wording checks: the words just before a placeholder must not mislabel it.
        before = text[:text.index("{" + fid + "}")].lower().split()[-4:]
        if any(word.strip(",.:;()") in _CONFLICTS.get(fact.topic, ()) for word in before):
            raise SummaryRejected(f"{fid} ({fact.topic}) is described with conflicting wording")
        term = _TOPIC_TERMS.get(fact.topic)
        if term and term not in text.lower():
            raise SummaryRejected(f"{fid} ({fact.topic}) is cited without saying what it measures")


def validate_output(output: Any, facts: list[Fact]) -> None:
    by_id = {f.id: f for f in facts}
    if not output.points:
        raise SummaryRejected("no points were returned")
    _check_text(output.headline, output.headline_fact_ids, by_id, "headline")
    for point in output.points:
        if point.kind in ("strength", "risk", "data_quality") and not point.fact_ids:
            raise SummaryRejected(f"a {point.kind} point cites no fact")
        _check_text(point.text, point.fact_ids, by_id, point.kind)


def render(text: str, facts: dict[str, Fact]) -> str:
    """Placeholders are checked before this runs; values come from the package."""
    return _PLACEHOLDER.sub(lambda m: facts[m.group(1)].display, text)


# ---------------------------------------------------------------- fingerprint and result

def fingerprint(package: dict, model: str) -> str:
    """Hash of the package (without any ai_summary), the model and the prompt version.
    The advance rate is inside the package, so a rate change gives a new fingerprint."""
    base = {k: v for k, v in package.items() if k != "ai_summary"}
    payload = json.dumps({"package": base, "model": model, "prompt_version": PROMPT_VERSION},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RenderedPoint:
    kind: str
    text: str
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class AISummary:
    available: bool
    reason: str | None  # short, sanitised; never a raw exception text
    fingerprint: str
    requested_model: str
    model: str | None = None  # the model reported by the API response
    generated_at: str | None = None
    headline: str | None = None
    points: tuple[RenderedPoint, ...] = ()
    limits: tuple[str, ...] = field(default_factory=tuple)
    prompt_version: str = PROMPT_VERSION
    label: str = AI_LABEL

    def to_export(self) -> dict:
        if not self.available:
            return {"value": None, "reason": self.reason}
        return {"value": {"headline": self.headline,
                          "points": [{"kind": p.kind, "text": p.text, "fact_ids": list(p.fact_ids)}
                                     for p in self.points],
                          "data_limits": list(self.limits)},
                "label": self.label, "model": self.model, "generated_at": self.generated_at,
                "fingerprint": self.fingerprint, "prompt_version": self.prompt_version}


def _error_reason(exc: BaseException) -> str:
    """Map an error to a short message; the raw text is never shown (it may hold credential details)."""
    names = {k.__name__ for k in type(exc).__mro__}
    if isinstance(exc, TypeError) and "authentication" in str(exc).lower():
        return "AI service not configured (no API key or credentials found)"
    if names & {"AuthenticationError", "PermissionDeniedError"}:
        return "AI service not configured (credentials were rejected)"
    if "APITimeoutError" in names:
        return "AI service timed out"
    if "APIConnectionError" in names:
        return "could not connect to the AI service"
    if "RateLimitError" in names:
        return "AI service is rate limited; try again later"
    if names & {"InternalServerError", "OverloadedError"}:
        return "AI service is temporarily unavailable"
    if "BadRequestError" in names:
        return "the AI service rejected the request"
    if "APIStatusError" in names or "AnthropicError" in names:
        return "AI service error"
    return "unexpected error while generating the summary"


def with_ai_summary(package: dict, stored: AISummary | None, model: str | None = None) -> dict:
    """Package copy with `ai_summary` set. A stored summary is used only if its fingerprint matches
    the current package, model and prompt version; anything older is never exported."""
    model = model or configured_model()
    out = dict(package)
    if stored is None:
        out["ai_summary"] = {"value": None, "reason": "not requested for the current inputs"}
    elif stored.fingerprint != fingerprint(package, model):
        out["ai_summary"] = {"value": None, "reason": "inputs changed since the summary was generated"}
    else:
        out["ai_summary"] = stored.to_export()
    return out


def sdk_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("anthropic") is not None


def summary_unavailable(package: dict, reason: str, model: str | None = None) -> AISummary:
    model = model or configured_model()
    return AISummary(False, reason, fingerprint(package, model), model)


def eligibility(package: dict) -> str | None:
    """None when the summary may be requested; otherwise a short reason."""
    if package["status"]["blocked"]:
        return "analysis is blocked; there are no indicators to summarise"
    if package.get("synthetic_data") is not True:
        return ("AI summary is limited to the bundled synthetic samples in this phase; uploaded files "
                "are not sent to an external service")
    return None


def generate_summary(package: dict, model: str | None = None, client: Any = None) -> AISummary:
    """Ask the model for a briefing and validate it. Never raises; failures return available=False."""
    model = model or configured_model()
    fp = fingerprint(package, model)
    blocked = eligibility(package)
    if blocked:
        return AISummary(False, blocked, fp, model)
    facts = build_facts(package)
    limits = tuple(data_limits(package))
    try:
        _, SummaryOutput = _output_models()
        if client is None:
            import anthropic  # optional dependency
    except ImportError:
        return AISummary(False, "AI SDK not installed (pip install -r requirements-ai.txt)", fp, model)
    if client is None:
        try:
            client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS_PER_ATTEMPT, max_retries=MAX_RETRIES)
        except Exception as exc:  # noqa: BLE001 - e.g. invalid configuration; mapped to a sanitised reason
            return AISummary(False, _error_reason(exc), fp, model)

    try:
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "Fact sheet:\n" + _fact_sheet_text(facts)}],
            output_format=SummaryOutput,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to a sanitised reason
        return AISummary(False, _error_reason(exc), fp, model)

    if getattr(response, "stop_reason", None) == "refusal":
        return AISummary(False, "the model declined to write a summary", fp, model)
    if getattr(response, "stop_reason", None) == "max_tokens":
        return AISummary(False, "the summary was cut off before completion", fp, model)
    output = getattr(response, "parsed_output", None)
    if output is None:
        return AISummary(False, "the model returned no structured summary", fp, model)
    try:
        validate_output(output, facts)
    except SummaryRejected as exc:
        return AISummary(False, f"summary rejected by checks: {exc}", fp, model)

    by_id = {f.id: f for f in facts}
    return AISummary(
        available=True, reason=None, fingerprint=fp, requested_model=model,
        model=getattr(response, "model", None) or model,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        headline=render(output.headline, by_id),
        points=tuple(RenderedPoint(p.kind, render(p.text, by_id), tuple(p.fact_ids)) for p in output.points),
        limits=limits,
    )
