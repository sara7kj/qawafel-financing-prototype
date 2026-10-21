# SME Financing Package — Prototype

Qawafel Graduate Software Engineer assessment. This repository holds the **Task 03 prototype** and the written answers for Tasks 01, 02, 04 and 05 (see [Documents](#documents)).

> **All bundled sample and test data is synthetic.** No real company or customer data is used. There is no public deployment; the app runs locally.

---

## The problem and what the prototype does
An SME wholesaler wants financing. They provide 12 months of invoices (Excel) and bank statements (PDF), and Qawafel prepares a package for a partner bank to review.

The prototype covers the **invoice side** and helps an analyst decide what to look at. It:
- **Loads** an invoice workbook (customers, invoices, payments, credit notes) and **validates** it. Defective records are quarantined with a reason, never silently dropped.
- **Computes indicators** at a fixed valuation date:
  - monthly sales, credit notes and net sales;
  - collections from accepted records only;
  - receivables aging;
  - customer concentration, with a warning above an experimental 30% threshold.
- **Shows an illustrative financing estimate** at an adjustable advance rate. This is a declared assumption, **not** a bank policy or a credit decision.
- **Produces a rule-based analyst summary**: key figures, warnings, data limits and follow-up questions.
- **Exports** a JSON package and a CSV of issues.

Unknown values are shown as **N/A with a reason**, never as zero. A blocked workbook shows its block reasons and no financial figures.

---

## Run it (fresh copy)
Run the commands from the **repository root**, after downloading and unzipping the repository or cloning it.

Requires **Python 3.13**. The commands below are for Windows PowerShell; on macOS/Linux use `python3` and `.venv/bin/python` instead.
- These steps were verified on **Windows with Python 3.13**, in a new environment without the AI SDK or an API key.
- The macOS/Linux variants have **not** been tested.

```bash
py -3.13 -m venv .venv
```
```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```
```bash
.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py
```

- `requirements.txt` installs the pinned dependencies **and** this project's own package (`src/financing`, the `-e .` line), so no separate install step is needed. The app imports `financing` as an installed package; it does not change `sys.path`.
- Then open http://localhost:8501. **No API key, no AI SDK and no payment are needed.**

To regenerate the sample data (optional; the files are already in `data/sample/`):
```bash
.venv\Scripts\python.exe scripts/generate_invoices.py
```

### Streamlit Community Cloud (prepared, not deployed)
The repository is set up for Streamlit Community Cloud, but **no public deployment exists yet**. When deploying:
- **Main file path:** `app/streamlit_app.py`.
- **Python version:** choose **3.13** in the deployment's advanced settings. The project requires Python ≥ 3.13, and the dependencies are pinned for 3.13.
- **Dependencies:** the Cloud installs `requirements.txt` from the repository root, and that also installs `src/financing`.
- **Secrets:** none needed. Do **not** add `requirements-ai.txt` or an API key; without them, the AI tab simply shows "unavailable".

---

## Quick tour (about 5 minutes)
1. **Clean sample** (selected by default):
   - Read the **Automated summary** tab, which is the first tab.
   - Look at the **Sales & collections**, **Receivables aging** and **Customer concentration** tabs.
2. **Sample with defects:** switch to it in the sidebar.
   - **Data quality** lists quarantined records, payments excluded because their invoice was quarantined (D-13), and invoice `INV-D11` marked receivable-unreliable (R-13).
3. **Upload Excel:** choose *Upload .xlsx* in the sidebar.
   - Try `tests/fixtures/cases/TC-02.xlsx`: it is **blocked**, because a required column is missing.
   - Try `tests/fixtures/cases/TC-09.xlsx`: a defective credit note makes some figures **N/A**, each with its reason.
   - Uploaded files are **not** marked as synthetic, and the AI summary is disabled for them.
4. **Financing rate:** move the advance-rate slider (0–100%). Only the illustrative estimate changes.
5. **Export tab:** download the **JSON package** and the **issues CSV**.

---

## Reference figures
Every amount is in SAR; the valuation date is **2026-09-30**.

| Clean sample (`data/sample/sample_clean.xlsx`, seed 20260930) | Value |
|---|---|
| Records | 25 customers · 605 invoices · 686 payments (657 accepted, 29 after the valuation date) · 20 credit notes |
| Gross sales (excl. VAT) | 7,597,838.04 |
| Net sales (excl. VAT) | 7,548,825.74 |
| Collections from accepted records only (incl. VAT, unverified) | 7,103,249.10 |
| Open receivables at the valuation date (incl. VAT) | 1,577,900.74 (of which > 90 days overdue: 90,898.64) |
| Eligible receivables (≤ 90 days overdue) | 1,487,002.10 |
| Illustrative financing at 70% | **1,040,901.47** |
| Concentration warning (R-20) | CUST-003 holds 32.31% of open receivables. No sales warning |

| Hand-worked example (`tests/fixtures/worked_example.xlsx`) | Value |
|---|---|
| Gross / net sales (excl. VAT) | 25,000.33 / 24,600.33 |
| Collections | 12,000.00 |
| Open receivables | 16,290.38 |
| Eligible receivables | 12,840.38 |
| Illustrative financing at 70% | **8,988.27** |

The expected results are hand-written in `tests/fixtures/expected/` and checked by the tests.

---

## Tests
```bash
.venv\Scripts\python.exe -m pytest -q
```
- **Core environment** (`requirements.txt` only): the AI-summary tests are **skipped**, because they need the optional packages. All other tests pass, including the app and export tests run without the AI SDK.
- **With the optional AI dependencies** (`pip install -r requirements-ai.txt`): the AI-summary tests also run. They use a fake client and make **no network calls**.

---

## Summaries: rule-based by default, AI optional
- **Default — "Automated analyst summary — rule-based":** built by fixed rules from the computed results. It needs no SDK, key or network.
- **Optional — "AI summary (optional — requires API key)":**
  - Needs `requirements-ai.txt` and an Anthropic API key.
  - It is available only for the two bundled synthetic samples.
  - The model writes fact placeholders, and checks reject unknown facts, stray numbers and decision wording.
  - It has been tested **offline only**. It was **not** run against the live service, because no credentials were configured.
  - Design and limits: [docs/ai-summary.md](docs/ai-summary.md).

---

## Assumptions and limits
- **Collections are not bank-reconciled.** They are payments recorded by the applicant, so they are labelled unverified. PDF bank-statement extraction and reconciliation are designed, not built.
- **Receivables scope:** only invoices issued in the reporting period (2025-10-01 to 2026-09-30); older open invoices are not in the data. Invoices with unreliable settlements are excluded from receivables.
- **The financing estimate is illustrative:** 70% by default of reliable open balances at most 90 days overdue. It is not a bank policy or a credit decision.
- **Completeness is not proven:** the source export cannot be shown to be complete from the data alone.
- **Input:** one workbook format ([docs/data-design.md](docs/data-design.md) §1), SAR only, a fixed reporting period and valuation date.
- **Prototype only:** no storage, login, API or bank connection. Those are designed in the documents below.

---

## Documents
| Task | Document |
|---|---|
| 01 Requirements | [docs/requirements.md](docs/requirements.md) |
| 02 Technical specification (data model, API, process flow) | [docs/technical-specification.md](docs/technical-specification.md) · data design: [docs/data-design.md](docs/data-design.md) |
| 03 Prototype | This repository · AI summary design: [docs/ai-summary.md](docs/ai-summary.md) |
| 04 Bank integration plan | [docs/bank-integration-plan.md](docs/bank-integration-plan.md) |
| 05 Automating the request (bonus) | [docs/automation-plan.md](docs/automation-plan.md) |

Screenshots are in `docs/screenshots/`. The two earlier screenshots, `ui-clean-sample.png` and `ui-defect-sample.png`, predate the Automated summary and AI tabs.

---

## AI tools used
- **Development:** I used **Claude Code** (Anthropic's Claude) as a coding and writing assistant throughout. It drafted the code, the tests and the documents from my instructions, ran the tests, and proposed fixes.
- **My role:** I defined the requirements and decisions, and reviewed every phase before moving on.
- **Technical review:** I also used ChatGPT/Codex for technical review of the design, selected code, and outputs. It directly executed selected test cases; it did not run the full pytest suite.
- **Expected results:** the files in `tests/fixtures/expected/` were manually specified from the documented worked examples, independently of the implementation under test.
- **Inside the app:** the optional AI summary calls the Anthropic API (`claude-sonnet-5-5`, configurable). It has been tested only with a fake client; no live call has been made.
