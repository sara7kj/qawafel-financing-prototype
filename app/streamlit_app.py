"""Streamlit review screen for the SME financing package prototype.

Display only: every number comes from financing.package.analyse (loader -> validation ->
metrics) and financing.financing.estimate_financing. Nothing is recomputed here.
Run: .venv\\Scripts\\python.exe -m streamlit run app/streamlit_app.py
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from financing.ai_summary import (
    AI_LABEL,
    configured_model,
    data_limits,
    eligibility,
    fingerprint,
    generate_summary,
    sdk_available,
    with_ai_summary,
)
from financing.financing import DEFAULT_RATE_PERCENT, estimate_financing
from financing.metrics import BUCKETS, display_ratio
from financing.package import analyse, build_package, issue_rows, issues_csv, package_json
from financing.schema import REPORTING_PERIOD_END, REPORTING_PERIOD_START, VALUATION_DATE

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = {
    "Clean sample": ROOT / "data" / "sample" / "sample_clean.xlsx",
    "Sample with defects": ROOT / "data" / "sample" / "sample_defects.xlsx",
}
UPLOAD = "Upload .xlsx"
DEMO_APPLICANT_ID = "APP-DEMO-001"
DEMO_APPLICANT_NAME = "Demo Foodstuff Wholesale Co., fictional"
NA = "N/A"
COLORS = {"gross": "#2563eb", "credit": "#d97706", "net": "#059669", "collections": "#7c3aed", "na": "#9ca3af"}


# ---------------------------------------------------------------- formatting (display only)

def sar(halalas: int | None) -> str:
    if halalas is None:
        return NA
    return f"SAR {Decimal(halalas) / 100:,.2f}"


def pct(share: Fraction | None) -> str:
    """A share as a percentage, ROUND_HALF_UP to 2 places; negative and >100% are shown as they are."""
    return NA if share is None else f"{display_ratio(share * 100, 2)}%"


def sar_or_none(halalas: int | None) -> float | None:
    return None if halalas is None else halalas / 100  # chart coordinates only


@st.cache_data(show_spinner="Validating and computing indicators...")
def run_analysis(data: bytes, name: str):
    return analyse(data, name)


# ---------------------------------------------------------------- page

st.set_page_config(page_title="SME Financing Review", page_icon="📊", layout="wide")

with st.sidebar:
    st.header("Data source")
    options = [*SAMPLES, UPLOAD]
    start = {"clean": 0, "defects": 1, "upload": 2}.get(st.query_params.get("source", ""), 0)  # e.g. ?source=defects
    choice = st.radio("Workbook", options, index=start, label_visibility="collapsed")
    if choice == UPLOAD:
        uploaded = st.file_uploader("Invoice workbook (.xlsx)", type=None,
                                    help="One workbook with sheets customers, invoices, payments and credit_notes "
                                         "(docs/data-design.md §1).")
        applicant_id = st.text_input("Applicant ID (optional)", help="Left empty, the package records null.")
        if uploaded is None:
            st.info("Upload a workbook to start.")
            st.stop()
        data, source_name = uploaded.getvalue(), uploaded.name
        applicant_id, applicant_label, known_sample = applicant_id.strip() or None, None, False
    else:
        data, source_name = SAMPLES[choice].read_bytes(), SAMPLES[choice].name
        applicant_id, applicant_label, known_sample = DEMO_APPLICANT_ID, DEMO_APPLICANT_NAME, True

    st.header("Illustrative financing")
    rate = st.slider("Advance rate (%)", min_value=0, max_value=100, value=DEFAULT_RATE_PERCENT, step=1,
                     help="Applied to reliable open balances at most 90 days overdue. "
                          "An illustrative assumption, not a bank policy.")
    st.caption("Changing the rate only re-applies it to the eligible receivables already computed.")

validation, metrics = run_analysis(data, source_name)
estimate = estimate_financing(metrics, rate)
base_package = build_package(validation, metrics, estimate, applicant_id=applicant_id, source_name=source_name,
                             known_synthetic_sample=known_sample)
ai_model = configured_model()
ai_fingerprint = fingerprint(base_package, ai_model)  # computed before ai_summary is attached
ai_store = st.session_state.setdefault("ai_summaries", {})  # per user session, keyed by fingerprint
ai_current = ai_store.get(ai_fingerprint)
package = with_ai_summary(base_package, ai_current, ai_model)
rows = issue_rows(validation, metrics)

st.title("SME financing review")
applicant_text = applicant_id or "not provided"
if applicant_label:
    applicant_text += f" ({applicant_label})"
st.caption(f"Applicant ID: {applicant_text} · Source file: `{source_name}`")
st.markdown(f"**Reporting period:** {REPORTING_PERIOD_START:%d %b %Y} – {REPORTING_PERIOD_END:%d %b %Y}"
            f" &nbsp;·&nbsp; **Valuation date:** {VALUATION_DATE:%d %b %Y}"
            " &nbsp;·&nbsp; **Source completeness verified:** No (cannot be proven from dates alone)")
origin = ("**Synthetic sample.** All data in this sample is synthetic." if known_sample else
          "**Origin not verified.** This uploaded file cannot be proven synthetic; the prototype is intended "
          "for synthetic data only.")
st.warning(f"{origin} These results support an analyst's review. They are not a credit approval, a credit "
           "score or a default probability. Payments are not reconciled to bank statements.")


def downloads(key: str) -> None:
    d1, d2 = st.columns(2)
    d1.download_button("Download JSON package", package_json(package), file_name="financing_package.json",
                       mime="application/json", key=f"json-{key}", width="stretch")
    d2.download_button("Download issues (CSV)", issues_csv(validation, metrics), file_name="issues.csv",
                       mime="text/csv", key=f"csv-{key}", width="stretch")


def issues_table() -> None:
    if not rows:
        st.success("No issues found.")
        return
    frame = pd.DataFrame(rows)
    classes = sorted(frame["class"].unique())
    shown = st.multiselect("Show classes", classes, default=classes)
    st.dataframe(frame[frame["class"].isin(shown)], hide_index=True, width="stretch",
                 column_config={"row": st.column_config.NumberColumn("Excel row", format="%d")})


def show_rule_summary(summary: dict) -> None:
    """The default summary: built by fixed rules from the computed results (not AI)."""
    st.subheader(summary["title"])
    st.caption(summary["note"])
    st.markdown(f"**{summary['headline']}**")
    if summary["key_figures"]:
        st.dataframe(pd.DataFrame([{"Figure": f["label"], "Value": f["value"],
                                    "Note / reason": f["reason"] or f["note"] or ""}
                                   for f in summary["key_figures"]]), hide_index=True, width="stretch")
    for title, key in (("Warnings", "warnings"), ("Data limits", "data_limits"),
                       ("Follow-up questions", "follow_up_questions")):
        if summary[key]:
            st.markdown(f"**{title}**")
            for line in summary[key]:
                st.markdown(f"- {line}")


# ---------------------------------------------------------------- BLOCK

if metrics.blocked:
    st.error("**Analysis blocked.** No financial indicators are produced until these problems are fixed:")
    for reason in metrics.block_reasons:
        st.markdown(f"- {reason}")
    show_rule_summary(package["automated_summary"])
    st.subheader("Issues")
    issues_table()
    downloads("blocked")
    st.stop()


# ---------------------------------------------------------------- headline cards

def card(column, label: str, value: str, note: str, reason: str | None = None) -> None:
    with column.container(border=True):
        st.metric(label, value)
        st.caption(note if reason is None else f"{note}  \n**N/A:** {reason}")


gross, net, collections, receivables = metrics.gross_sales, metrics.net_sales, metrics.collections, metrics.receivables
k1, k2 = st.columns(2)
k3, k4 = st.columns(2)  # two per row so full SAR amounts are never truncated
card(k1, "Gross sales (RP)", sar(gross.total), "Excl. VAT, before credit notes.")
card(k2, "Net sales (RP)", sar(net.total), "Excl. VAT, after credit notes.", net.na_reasons.get("total"))
card(k3, "Collections (RP)", sar(collections.total),
     "Accepted records only · incl. VAT as recorded · unverified.", collections.na_reasons.get("total"))
card(k4, "Open receivables at VD", sar(receivables.total),
     f"Incl. VAT · reliable invoices only · quality: `{receivables.quality_status}`.", receivables.reason)

with st.container(border=True):
    f1, f2 = st.columns([1, 2])
    f1.metric(f"Illustrative financing at {rate}%", sar(estimate.amount_halalas))
    f2.markdown(f"**Eligible receivables:** {sar(estimate.eligible_halalas)} ({estimate.eligibility_rule}).  \n"
                f"**{estimate.label}**")
    if estimate.amount_halalas is None:
        f2.error(f"N/A: {estimate.reason}")

tab_summary, tab_quality, tab_sales, tab_aging, tab_conc, tab_ai, tab_export = st.tabs(
    ["Automated summary", "Data quality", "Sales & collections", "Receivables aging", "Customer concentration",
     "AI summary (optional — requires API key)", "Export"])

with tab_summary:
    show_rule_summary(package["automated_summary"])


# ---------------------------------------------------------------- data quality

with tab_quality:
    q1, q2 = st.columns([1, 1])
    with q1:
        st.subheader("Records by status")
        counts = pd.DataFrame(validation.status_counts()).T
        st.dataframe(counts, width="stretch")
        st.caption("Each record is counted once, whatever its number of issues.")
    with q2:
        st.subheader("Receivables quality")
        st.markdown(f"Status: **`{validation.receivables_quality.value}`**")
        if validation.receivables_quality_reasons:
            st.markdown("Reasons: " + ", ".join(f"`{r}`" for r in validation.receivables_quality_reasons))
        unreliable = [{"invoice": inv, "rule": r.rule, "cause": f"{r.sheet} row {r.row} ({r.record_id})",
                       "detail": r.message}
                      for inv, reasons in validation.receivable_unreliable.items() for r in reasons]
        st.markdown("**Receivable-unreliable invoices** (kept in sales, removed from receivables)")
        if unreliable:
            st.dataframe(pd.DataFrame(unreliable), hide_index=True, width="stretch")
        else:
            st.caption("None.")

    st.subheader("Excluded amounts")
    excluded = [{"set": f"{sheet} (quarantined)", **vars(s)} for sheet, s in validation.excluded_amounts.items()]
    excluded.append({"set": "payments (dependent, D-13)", **vars(validation.dependent_payments)})
    frame = pd.DataFrame(excluded)
    frame["known_recorded_halalas"] = frame["known_recorded_halalas"].map(sar)
    st.dataframe(frame.rename(columns={"known_recorded_halalas": "known recorded amount excluded"}),
                 hide_index=True, width="stretch")
    st.caption("Known recorded amount excluded: recorded SAR values, not verified, may understate. "
               "Duplicates, unknown or non-SAR currencies and unreadable amounts are counted, not summed.")

    st.subheader("Issues, dependents and invoice states")
    issues_table()


# ---------------------------------------------------------------- sales & collections

def na_markers(fig: go.Figure, series, name: str) -> None:
    months = [m for m, v in series.monthly.items() if v is None]
    if months:
        fig.add_trace(go.Scatter(x=months, y=[0] * len(months), mode="markers+text", text=[NA] * len(months),
                                 textposition="top center", marker={"symbol": "x", "size": 11, "color": COLORS["na"]},
                                 name=f"{name}: N/A", hovertext=[series.na_reasons[m] for m in months],
                                 hoverinfo="text"))


with tab_sales:
    st.caption("Sales and credit notes **exclude VAT**; collections are payment amounts **as recorded (incl. VAT)**. "
               "The two bases are shown side by side and are never subtracted or divided.")
    months = list(gross.monthly)
    sales_fig = go.Figure()
    for series, key, label in ((gross, "gross", "Gross sales"), (metrics.credit_notes, "credit", "Credit notes"),
                               (net, "net", "Net sales")):
        sales_fig.add_trace(go.Bar(x=months, y=[sar_or_none(series.monthly[m]) for m in months], name=label,
                                   marker_color=COLORS[key]))
        na_markers(sales_fig, series, label)
    sales_fig.update_layout(barmode="group", title="Monthly sales (SAR, excl. VAT)", yaxis_title="SAR",
                            legend={"orientation": "h"}, height=380, margin={"t": 50})
    st.plotly_chart(sales_fig, width="stretch")

    col_fig = go.Figure(go.Scatter(x=months, y=[sar_or_none(collections.monthly[m]) for m in months],
                                   mode="lines+markers", connectgaps=False, name="Collections",
                                   line={"color": COLORS["collections"]}))
    na_markers(col_fig, collections, "Collections")
    col_fig.update_layout(title="Collections from accepted records only (SAR, incl. VAT, unverified)",
                          yaxis_title="SAR", height=340, margin={"t": 50}, legend={"orientation": "h"})
    st.plotly_chart(col_fig, width="stretch")

    table = pd.DataFrame({"Gross sales": [sar(gross.monthly[m]) for m in months],
                          "Credit notes": [sar(metrics.credit_notes.monthly[m]) for m in months],
                          "Net sales": [sar(net.monthly[m]) for m in months],
                          "Collections": [sar(collections.monthly[m]) for m in months]}, index=months)
    table.loc["RP total"] = [sar(s.total) for s in (gross, metrics.credit_notes, net, collections)]
    st.dataframe(table, width="stretch")
    ratio = metrics.credit_note_ratio
    st.markdown(f"**Credit-note ratio** (credit notes ÷ gross sales, excl. VAT): "
                f"{pct(ratio.value) if ratio.value is not None else NA}"
                + (f" — N/A: {ratio.reason}" if ratio.value is None else ""))
    if metrics.dependent_payments.records:
        st.info(f"{metrics.dependent_payments.records} payment(s) excluded because their invoice is quarantined "
                f"(known recorded amount {sar(metrics.dependent_payments.known_recorded_halalas)}).")
    for na_reason in sorted({r for s in (net, collections) for r in s.na_reasons.values()}):
        st.caption(f"N/A: {na_reason}")


# ---------------------------------------------------------------- aging

with tab_aging:
    if receivables.buckets is None:
        st.error(f"Aging N/A: {receivables.reason}")
    else:
        if receivables.quality_status == "unresolved_links":
            st.warning("Some defective payments or credit notes cannot be linked to an invoice: "
                       "open balances may be overstated.")
        aging_fig = go.Figure(go.Bar(x=list(BUCKETS), y=[receivables.buckets[b] / 100 for b in BUCKETS],
                                     text=[sar(receivables.buckets[b]) for b in BUCKETS], textposition="outside",
                                     marker_color=["#059669", "#84cc16", "#eab308", "#f97316", "#dc2626"]))
        aging_fig.update_layout(title=f"Open receivables at {VALUATION_DATE:%d %b %Y} by days overdue (SAR, incl. VAT)",
                                xaxis_title="Days overdue", yaxis_title="SAR", height=380, margin={"t": 50})
        st.plotly_chart(aging_fig, width="stretch")
        st.caption(f"Scope: {receivables.scope.description}. Records: {receivables.scope.record_counts}. "
                   "An invoice due on the valuation date is Current.")


# ---------------------------------------------------------------- concentration

def concentration(conc, title: str) -> None:
    st.subheader(title)
    if conc.top is None:
        st.error(f"N/A: {conc.reason}")
        return
    shares = [float(e.share) * 100 for e in conc.top]  # chart coordinates only
    fig = go.Figure(go.Bar(y=[e.customer_id for e in conc.top][::-1], x=shares[::-1], orientation="h",
                           text=[pct(e.share) for e in conc.top][::-1], textposition="outside", cliponaxis=False,
                           marker_color=["#dc2626" if e.customer_id in conc.warnings else "#2563eb"
                                         for e in conc.top][::-1]))
    fig.add_vline(x=30, line_dash="dash", line_color="#dc2626", annotation_text="30% (experimental)")
    fig.update_layout(xaxis_title="Share of total (%)", height=300, margin={"t": 30, "r": 60})
    st.plotly_chart(fig, width="stretch")
    st.dataframe(pd.DataFrame([{"customer": e.customer_id, "amount": sar(e.amount), "share": pct(e.share),
                                "R-20": "WARN" if e.customer_id in conc.warnings else ""} for e in conc.top]),
                 hide_index=True, width="stretch")
    st.caption(f"Total {conc.basis}: {sar(conc.total)}. Shares can be negative or above 100%; they are not clipped.")


with tab_conc:
    for w in metrics.warnings:
        st.warning(f"R-20: {w.message}")
    st.caption("The 30% threshold is experimental, not a bank policy. A warning does not exclude records "
               "or change any calculation.")
    left, right = st.columns(2)
    with left:
        concentration(metrics.sales_concentration, "Top 5 by net sales (excl. VAT)")
    with right:
        concentration(metrics.receivables_concentration, "Top 5 by open receivables (incl. VAT)")


# ---------------------------------------------------------------- AI analyst summary (optional)

with tab_ai:
    st.caption(f"Optional — requires the AI SDK and an API key. Model: `{ai_model}` · {AI_LABEL} "
               "The AI connection has been tested offline only (not verified against the live service). "
               "The default summary is the rule-based one in the first tab; nothing else depends on this tab.")
    blocked_reason = eligibility(base_package)
    if blocked_reason:
        st.info(f"AI summary unavailable: {blocked_reason}.")
    elif not sdk_available():
        st.info("AI summary unavailable: the optional AI SDK is not installed "
                "(`pip install -r requirements-ai.txt`).")
    else:
        if st.button("Generate analyst summary", type="primary"):
            with st.spinner("Asking the AI service and checking the result..."):
                ai_store[ai_fingerprint] = generate_summary(base_package, ai_model)
            st.rerun()  # rebuild the export with the new summary
        if ai_current is None:
            st.caption("No summary for the current inputs. Changing the sample or the rate needs a new one.")
        elif not ai_current.available:
            st.warning(f"AI summary unavailable: {ai_current.reason}.")
        else:
            st.markdown(f"#### {ai_current.headline}")
            for kind, title in (("strength", "Strengths"), ("risk", "Risks"),
                                ("data_quality", "Data quality"), ("question", "Questions to ask")):
                points = [p for p in ai_current.points if p.kind == kind]
                if points:
                    st.markdown(f"**{title}**")
                    for p in points:
                        st.markdown(f"- {p.text}")
            st.caption(f"{AI_LABEL} Model: {ai_current.model} · generated {ai_current.generated_at} UTC.")
    st.markdown("**Data limits** (built from the computed results, not by the AI)")
    if not metrics.blocked:
        for line in data_limits(base_package):
            st.markdown(f"- {line}")


# ---------------------------------------------------------------- export

with tab_export:
    st.markdown("The JSON package contains the same results shown on this page. Amounts are decimal strings "
                "with 2 places; shares and ratios are fractions (e.g. `\"0.3231\"` = 32.31%); unavailable "
                "values are `null` with a reason.")
    downloads("main")
    with st.expander("Preview JSON package"):
        st.json(package, expanded=False)
