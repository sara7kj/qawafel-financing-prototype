"""AI analyst summary - offline tests only (a fake client; no network, no API key, no cost).

The feature has NOT been verified against the live API; these tests cover the facts, the
checks, the failure paths, the fingerprint and the export.
"""

import sys
from types import SimpleNamespace

import pytest

from conftest import FIXTURES, SAMPLE
from financing import ai_summary as ai

# The model-output schema needs pydantic, which comes with the optional AI SDK (requirements-ai.txt).
# Without it these tests are skipped; tests/test_app.py still checks the app runs without the SDK.
pytest.importorskip("pydantic")
from financing.financing import estimate_financing
from financing.package import analyse, build_package


def make_package(path, rate=70, known=True):
    validation, metrics = analyse(path)
    return build_package(validation, metrics, estimate_financing(metrics, rate), applicant_id="APP-DEMO-001",
                         source_name=path.name, known_synthetic_sample=known)


@pytest.fixture(scope="module")
def clean():
    return make_package(SAMPLE / "sample_clean.xlsx")


@pytest.fixture(scope="module")
def facts(clean):
    return ai.build_facts(clean)


def fid(facts, label_start, value=None):
    return next(f.id for f in facts if f.label.startswith(label_start) and (value is None or f.value == value))


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(response=None, error=None):
    return SimpleNamespace(messages=FakeMessages(response, error))


def output(headline, headline_ids, *points):
    Point, Summary = ai._output_models()
    return Summary(headline=headline, headline_fact_ids=headline_ids,
                   points=[Point(kind=k, text=t, fact_ids=ids) for k, t, ids in points])


def response(out, stop_reason="end_turn", model="claude-sonnet-5-5"):
    return SimpleNamespace(parsed_output=out, stop_reason=stop_reason, model=model)


def good_output(facts):
    gross, coll = fid(facts, "Gross sales"), fid(facts, "Collections")
    c3 = fid(facts, "Customer ranked 1 by open receivables")
    c3_share = fid(facts, "Share of open receivables held by CUST-003")
    r20 = fid(facts, "Rule R-20")
    return output(
        "Gross sales of {%s} with collections of {%s} recorded by the applicant." % (gross, coll), [gross, coll],
        ("strength", "Gross sales reached {%s} over the period." % gross, [gross]),
        ("risk", "Customer {%s} holds {%s} of open receivables, flagged by {%s}." % (c3, c3_share, r20),
         [c3, c3_share, r20]),
        ("question", "Can the applicant share bank statements to confirm collections?", []),
    )


# ---------------------------------------------------------------- facts and limits

def test_facts_carry_package_values_ids_and_rules(clean, facts):
    by_label = {f.label: f for f in facts}
    assert by_label["Gross sales over the reporting period (excl. VAT)"].value == "SAR 7,597,838.04"
    assert by_label["Open receivables at the valuation date (incl. VAT, reliable invoices only)"].value == \
        "SAR 1,577,900.74"
    assert any(f.value == "CUST-003" and f.topic == "customer" for f in facts)
    assert any(f.value == "R-21" and f.topic == "rule" for f in facts)
    share = next(f for f in facts if f.label.startswith("Share of open receivables held by CUST-003"))
    assert share.risk_only and share.value == "32.31%"
    assert len({f.id for f in facts}) == len(facts)


def test_data_limits_are_built_by_code_from_the_results():
    pkg = make_package(FIXTURES / "cases" / "TC-08.xlsx")
    limits = ai.data_limits(pkg)
    assert limits[0].startswith("Collections are payments recorded by the applicant; they are not reconciled")
    assert any("unresolved_links" in line for line in limits)
    assert any(line.startswith("Illustrative financing estimate: N/A") for line in limits)
    assert any(line.startswith("Collections 2026-08: N/A") for line in limits)


# ---------------------------------------------------------------- eligibility and unavailability

def test_uploaded_files_are_not_sent(clean):
    pkg = make_package(SAMPLE / "sample_clean.xlsx", known=False)
    client = fake_client(response(good_output(ai.build_facts(pkg))))
    result = ai.generate_summary(pkg, client=client)
    assert not result.available and "limited to the bundled synthetic samples" in result.reason
    assert client.messages.calls == []


def test_blocked_analysis_is_not_summarised():
    pkg = make_package(FIXTURES / "cases" / "TC-02.xlsx")
    client = fake_client()
    assert "blocked" in ai.generate_summary(pkg, client=client).reason and client.messages.calls == []


def test_missing_sdk_reports_unavailable(clean, monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import anthropic -> ImportError
    result = ai.generate_summary(clean)
    assert not result.available and "not installed" in result.reason


def test_missing_credentials_and_secrets_never_leak(clean):
    secret = "sk-ant-api03-SECRET"
    result = ai.generate_summary(clean, client=fake_client(error=TypeError(
        f"Could not resolve authentication method. key={secret}")))
    assert result.reason == "AI service not configured (no API key or credentials found)"
    assert secret not in str(result.to_export())


@pytest.mark.parametrize("error, reason", [
    (RuntimeError("bad proxy config, key=sk-ant-api03-SECRET"), "unexpected error while generating the summary"),
    (type("AnthropicError", (Exception,), {})("base_url invalid, key=sk-ant-api03-SECRET"), "AI service error"),
    (TypeError("Could not resolve authentication method, key=sk-ant-api03-SECRET"),
     "AI service not configured (no API key or credentials found)"),
])
def test_client_construction_failure_is_contained(clean, monkeypatch, error, reason):
    """anthropic.Anthropic(...) itself raises: no exception escapes and no detail leaks."""
    calls = []

    def failing_constructor(**kwargs):
        calls.append(kwargs)
        raise error

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=failing_constructor))
    result = ai.generate_summary(clean)  # must not raise
    assert calls == [{"timeout": 30.0, "max_retries": 2}]
    assert not result.available and result.reason == reason
    assert "SECRET" not in str(result.to_export()) and "SECRET" not in str(result)


def _error(name, *bases):
    return type(name, bases or (Exception,), {})


@pytest.mark.parametrize("names, reason", [
    (("APITimeoutError", "APIConnectionError"), "AI service timed out"),
    (("APIConnectionError",), "could not connect to the AI service"),
    (("RateLimitError", "APIStatusError"), "AI service is rate limited; try again later"),
    (("InternalServerError", "APIStatusError"), "AI service is temporarily unavailable"),
    (("AuthenticationError", "APIStatusError"), "AI service not configured (credentials were rejected)"),
    (("BadRequestError", "APIStatusError"), "the AI service rejected the request"),
    (("SomethingElse",), "unexpected error while generating the summary"),
])
def test_errors_map_to_short_reasons(clean, names, reason):
    cls = Exception
    for name in reversed(names):  # build a class hierarchy with the SDK's class names
        cls = _error(name, cls)
    result = ai.generate_summary(clean, client=fake_client(error=cls("raw details with x-api-key: abc")))
    assert result.reason == reason and "abc" not in result.reason


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_or_truncation_is_unavailable(clean, facts, stop_reason):
    result = ai.generate_summary(clean, client=fake_client(response(good_output(facts), stop_reason)))
    assert not result.available and result.headline is None


def test_no_fallback_and_client_options(clean, facts):
    client = fake_client(response(good_output(facts)))
    ai.generate_summary(clean, client=client)
    [call] = client.messages.calls
    assert call["model"] == "claude-sonnet-5-5" and "fallbacks" not in call and "betas" not in call
    assert ai.TIMEOUT_SECONDS_PER_ATTEMPT == 30.0 and ai.MAX_RETRIES == 2


def test_model_is_configurable(monkeypatch):
    monkeypatch.setenv(ai.MODEL_ENV_VAR, "claude-opus-5-5")
    assert ai.configured_model() == "claude-opus-5-5"
    monkeypatch.delenv(ai.MODEL_ENV_VAR)
    assert ai.configured_model() == "claude-sonnet-5-5"


# ---------------------------------------------------------------- a valid summary

def test_valid_summary_renders_package_values(clean, facts):
    result = ai.generate_summary(clean, client=fake_client(response(good_output(facts), model="claude-sonnet-5-5")))
    assert result.available, result.reason
    assert result.headline.startswith("Gross sales of SAR 7,597,838.04 with collections of SAR 7,103,249.10")
    risk = next(p for p in result.points if p.kind == "risk")
    assert risk.text == "Customer CUST-003 holds 32.31% of open receivables, flagged by R-20."
    exported = result.to_export()
    assert exported["model"] == "claude-sonnet-5-5" and exported["generated_at"]
    assert exported["fingerprint"] == ai.fingerprint(clean, "claude-sonnet-5-5")
    assert exported["value"]["data_limits"] == ai.data_limits(clean)


# ---------------------------------------------------------------- checks

def _check(facts, *points, headline="Overview.", headline_ids=()):
    ai.validate_output(output(headline, list(headline_ids), *points), facts)


def test_rejects_unknown_fact(facts):
    with pytest.raises(ai.SummaryRejected, match="unknown fact"):
        _check(facts, ("risk", "See {F99}.", ["F99"]))


def test_rejects_placeholder_missing_from_fact_ids(facts):
    gross, net = fid(facts, "Gross sales"), fid(facts, "Net sales")
    with pytest.raises(ai.SummaryRejected, match="not listed in fact_ids"):
        _check(facts, ("strength", "Gross sales {%s} and net sales {%s}." % (gross, net), [gross]))


@pytest.mark.parametrize("text", ["The top 5 customers hold most sales {%s}.", "Sales {%s} rose 12%% in Q4.",
                                  "Customer CUST-003 has sales {%s}.", "Rule R-13 applies to sales {%s}."])
def test_rejects_digits_outside_placeholders(facts, text):
    gross = fid(facts, "Gross sales")
    with pytest.raises(ai.SummaryRejected, match="number was written outside"):
        _check(facts, ("risk", text % gross, [gross]))


@pytest.mark.parametrize("text", ["We recommend approving financing given sales {%s}.",
                                  "Sales {%s} make the applicant creditworthy.",
                                  "Low probability of default with sales {%s}."])
def test_rejects_decision_wording(facts, text):
    gross = fid(facts, "Gross sales")
    with pytest.raises(ai.SummaryRejected, match="decision or recommendation"):
        _check(facts, ("strength", text % gross, [gross]))


def test_rejects_collections_described_as_sales(facts):
    coll = fid(facts, "Collections")
    with pytest.raises(ai.SummaryRejected, match="conflicting wording"):
        _check(facts, ("strength", "Sales of {%s} were collected during the period." % coll, [coll]))


def test_rejects_sales_described_as_collections(facts):
    gross = fid(facts, "Gross sales")
    with pytest.raises(ai.SummaryRejected, match="conflicting wording"):
        _check(facts, ("strength", "The applicant collected {%s} in sales." % gross, [gross]))


def test_rejects_high_concentration_as_strength(facts):
    share = fid(facts, "Share of open receivables held by CUST-003")
    with pytest.raises(ai.SummaryRejected, match="risk fact and cannot support a strength"):
        _check(facts, ("strength", "A strong anchor client holds {%s} of receivables." % share, [share]))


def test_rejects_unlabelled_collections_figure(facts):
    coll = fid(facts, "Collections")
    with pytest.raises(ai.SummaryRejected, match="without saying what it measures"):
        _check(facts, ("risk", "Cash recorded totalled {%s}." % coll, [coll]))


def test_known_limit_reasoning_is_not_verified(facts):
    """Documented limit: values are guaranteed, the conclusion is not. A wrong inference that is
    worded plausibly still passes the checks - an analyst must read the note."""
    net = fid(facts, "Net sales")
    _check(facts, ("risk", "Net sales of {%s} suggest the business is shrinking." % net, [net]))


def test_failed_check_returns_unavailable_without_partial_text(clean, facts):
    coll = fid(facts, "Collections")
    bad = output("Sales of {%s}." % coll, [coll], ("strength", "Sales of {%s}." % coll, [coll]))
    result = ai.generate_summary(clean, client=fake_client(response(bad)))
    assert not result.available and result.reason.startswith("summary rejected by checks")
    assert result.points == () and result.to_export() == {"value": None, "reason": result.reason}


# ---------------------------------------------------------------- fingerprint and export

def test_fingerprint_tracks_inputs_rate_model_and_prompt(clean, monkeypatch):
    base = ai.fingerprint(clean, "claude-sonnet-5-5")
    assert ai.fingerprint(make_package(SAMPLE / "sample_clean.xlsx", rate=60), "claude-sonnet-5-5") != base
    assert ai.fingerprint(make_package(SAMPLE / "sample_defects.xlsx"), "claude-sonnet-5-5") != base
    assert ai.fingerprint(clean, "claude-opus-5-5") != base
    monkeypatch.setattr(ai, "PROMPT_VERSION", "analyst-summary-v2")
    assert ai.fingerprint(clean, "claude-sonnet-5-5") != base


def test_fingerprint_ignores_the_ai_summary_field(clean, facts):
    summary = ai.generate_summary(clean, client=fake_client(response(good_output(facts))))
    exported = ai.with_ai_summary(clean, summary, "claude-sonnet-5-5")
    assert exported["ai_summary"]["value"] is not None
    assert ai.fingerprint(exported, "claude-sonnet-5-5") == ai.fingerprint(clean, "claude-sonnet-5-5")


def test_stale_summary_is_never_exported(clean, facts):
    summary = ai.generate_summary(clean, client=fake_client(response(good_output(facts))))
    other_rate = make_package(SAMPLE / "sample_clean.xlsx", rate=50)
    assert ai.with_ai_summary(other_rate, summary, "claude-sonnet-5-5")["ai_summary"] == \
        {"value": None, "reason": "inputs changed since the summary was generated"}
    assert ai.with_ai_summary(clean, summary, "claude-opus-5-5")["ai_summary"]["value"] is None
    assert ai.with_ai_summary(clean, None)["ai_summary"]["value"] is None


def test_live_trial_helpers_offline(clean, facts):
    """The trial script's cost and recording helpers, without any network call."""
    from scripts import live_ai_trial as trial

    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    assert trial.cost_usd("claude-sonnet-5-5", usage) == 3.0  # 1M x $2 + 0.1M x $10
    assert trial.cost_usd("unknown-model", usage) is None

    recorder = trial.RecordingClient(fake_client(response(good_output(facts))))
    assert ai.generate_summary(clean, client=recorder).available
    assert recorder.response is not None and recorder.seconds is not None


def test_default_package_has_null_ai_summary(clean):
    assert clean["ai_summary"] == {"value": None, "reason": "not requested"}
