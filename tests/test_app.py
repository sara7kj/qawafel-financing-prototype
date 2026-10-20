"""Smoke test of the Streamlit page: it runs, switches samples and re-applies the rate."""

from streamlit.testing.v1 import AppTest

from conftest import ROOT

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
