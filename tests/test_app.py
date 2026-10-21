"""Smoke test of the Streamlit page: it runs, switches samples and re-applies the rate."""

from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from conftest import ROOT
from financing import ai_summary as ai

APP = str(ROOT / "app" / "streamlit_app.py")


def metric(at, label_start):
    return next(m.value for m in at.metric if m.label.startswith(label_start))


def test_app_runs_switches_samples_and_rate():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert metric(at, "Gross sales") == "SAR 7,597,838.04"
    assert metric(at, "Illustrative financing at 70%") == "SAR 1,040,901.47"

    at.radio[0].set_value("Sample with defects").run()
    assert not at.exception
    assert metric(at, "Gross sales") == "SAR 7,600,838.04"
    assert any("R-13" in str(df.value.to_csv()) for df in at.dataframe)

    at.slider[0].set_value(100).run()
    assert metric(at, "Illustrative financing at 100%") == "SAR 1,488,152.11"
    at.slider[0].set_value(0).run()
    assert metric(at, "Illustrative financing at 0%") == "SAR 0.00"


def test_upload_mode_does_not_claim_synthetic_or_use_file_name_as_applicant():
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["source"] = "upload"
    at.run()
    assert not at.exception
    assert [t.label for t in at.text_input] == ["Applicant ID (optional)"]
    assert not any("Synthetic sample" in w.value for w in at.warning)


def test_app_runs_without_the_ai_sdk(monkeypatch):
    monkeypatch.setattr(ai, "sdk_available", lambda: False)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert metric(at, "Gross sales") == "SAR 7,597,838.04"
    assert any("optional AI SDK is not installed" in i.value for i in at.info)
    assert not [b for b in at.button if b.label == "Generate analyst summary"]
    # the default summary is rule-based and needs neither the SDK nor a key
    assert any(s.value == "Automated analyst summary — rule-based" for s in at.subheader)
    assert any("Net sales of SAR 7,548,825.74" in m.value for m in at.markdown)


def test_summary_is_shown_for_current_inputs_only(monkeypatch):
    """Fake generation (no network): the summary appears, then disappears when the rate changes."""
    def fake_generate(package, model=None, client=None):
        return ai.AISummary(True, None, ai.fingerprint(package, model or ai.configured_model()),
                            model or ai.configured_model(), model="claude-sonnet-5-5",
                            generated_at="2026-10-20T10:00:00+00:00", headline="Fake headline for the test.",
                            points=(ai.RenderedPoint("risk", "Fake risk point.", ("F01",)),),
                            limits=tuple(ai.data_limits(package)))

    monkeypatch.setattr(ai, "sdk_available", lambda: True)
    monkeypatch.setattr(ai, "generate_summary", fake_generate)
    at = AppTest.from_file(APP, default_timeout=60).run()
    next(b for b in at.button if b.label == "Generate analyst summary").click().run()
    assert not at.exception
    assert any("Fake headline for the test." in m.value for m in at.markdown)

    at.slider[0].set_value(60).run()  # new inputs -> new fingerprint -> old summary not shown
    assert not any("Fake headline for the test." in m.value for m in at.markdown)
    assert any("No summary for the current inputs" in c.value for c in at.caption)
