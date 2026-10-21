# AI Analyst Summary — Design and Limits

> **Status:** Optional — requires an API key. Implemented and tested **offline only**, with a fake client and no network calls. The AI connection has **not** been tested live, so it is **not live-verified**.
> **The default summary is not this one.** It is the rule-based "Automated analyst summary — rule-based" (`src/financing/rule_summary.py`, requirements §2.3a), which needs no SDK, key or network.
> **Live trial attempt (2026-10-20):** no API key or credentials were configured on this machine, so the SDK stopped before any network call. No request was sent and nothing was charged. The paid trial is paused by decision. The report of that attempt is in `reports/` and shows "AI service not configured".
> **Baseline:** requirements US-11 and D-10. Code: `src/financing/ai_summary.py`. Tests: `tests/test_ai_summary.py` and `tests/test_app.py`.

## 1. Purpose
- A short briefing helps the analyst see what to look at: a headline, strengths, risks, data-quality points and questions to ask.
- It is **not** a credit decision. It never approves, rejects, scores or recommends financing, and it never mentions default probability.
- The core app, the JSON package and the CSV export all work without it.

## 2. Scope in this phase
| Item | Decision |
|---|---|
| Data sent | **The bundled synthetic samples only.** For uploaded files the feature is disabled, and the UI explains that they are not sent to an external service. |
| Blocked analysis | Disabled: there are no indicators to summarise. |
| Model | `claude-sonnet-5-5` by default. It can be changed with the `FINANCING_AI_MODEL` environment variable. |
| Fallback | **No** server-side fallback and no model switching. A refusal is shown as "unavailable". |
| Trigger | On demand only (button). Nothing is sent automatically. |

## 3. Inputs
- **Source:** a numbered **fact sheet** built by code from the exported package, so it holds the same values as the UI and the JSON.
- **Each fact has:** an ID such as `F07`, a label that states its meaning and basis, and a display value or `N/A` with its reason. Some facts carry a `risk` flag.
- **Fact topics:** totals and aging, the illustrative estimate, customer rankings and shares, the receivables quality status, quarantine / out-of-period / D-13 counts, and every rule code in the issue table.
- **Customer IDs and rule codes are facts too**, so the model never has to type them.
- **Not sent:** raw records, customer names and the full issue table.

## 4. Output and checks
**Output shape:** a structured response (`client.messages.parse(output_format=...)`) with `headline`, `headline_fact_ids`, and `points[]`. Each point has a `kind`, `text` and `fact_ids`.

**The model writes placeholders such as `{F07}`, never digits.** Before any placeholder is replaced, the code checks:
1. Every placeholder exists and is listed in the **same** point's `fact_ids`. Every listed ID exists.
2. There are no digits anywhere outside the placeholders. Amounts, percentages, counts, customer IDs, rule codes and dates must all come from facts.
3. There is no decision or recommendation wording (approve, reject, creditworthy, credit score, default probability, recommend, …).
4. A fact flagged `risk` is never cited in a **strength** point. Examples: a concentration share above 30%, balances 61–90 or over 90 days overdue, quarantine counts, a quality status other than `no_known_issues`.
5. **Wording heuristics:**
   - a collections figure must be described as collections, and a sales figure as sales;
   - the words just before a placeholder must not mislabel it (e.g. "sales of {collections}").

**If any check fails, the whole summary is rejected and shown as unavailable.** No partial text is shown.

## 5. What the checks do — and do not — guarantee
- **Guaranteed:** every number shown equals the package value, and it is attached to a fact the model chose and listed.
- **Not guaranteed:** that the conclusion is correct, or that every possible wording describes the number correctly. The wording checks are heuristics.
  - A wrong but plausible inference still passes. An example in the tests is "net sales … suggest the business is shrinking" (`test_known_limit_reasoning_is_not_verified`).
- **Therefore** the summary is labelled *"AI-generated from computed indicators – verify before use. Not a credit decision."*, and an analyst must read it before using it.

## 6. Data limits are built by code
**Shown with every summary**, in a "Data limits" section that `data_limits()` builds from the results, not from the model:
- collections are unverified;
- source completeness is not verified;
- the receivables quality status with its reasons;
- every N/A month or indicator with its reason;
- an N/A financing estimate.

The model's wording is **not** relied on to cover these.

## 7. Failures and missing setup
**Behaviour:** the function never raises, and the reason shown is a fixed short text. Raw error messages are **never** shown or exported, because they can contain credential details.

| Situation | Shown / exported reason |
|---|---|
| SDK not installed | AI SDK not installed (pip install -r requirements-ai.txt) |
| No credentials (SDK raises before any network call) | AI service not configured (no API key or credentials found) |
| 401 / 403 | AI service not configured (credentials were rejected) |
| Timeout | AI service timed out |
| Connection error | could not connect to the AI service |
| 429 | AI service is rate limited; try again later |
| 5xx | AI service is temporarily unavailable |
| 400 | the AI service rejected the request |
| `stop_reason = refusal` | the model declined to write a summary |
| `stop_reason = max_tokens` | the summary was cut off before completion |
| Check failure | summary rejected by checks: … (the rule that failed, never model text) |

**Timeouts and retries:**
- **Timeout:** **30 seconds per HTTP attempt**, not for the whole operation.
- **Retries:** the SDK retries **only** connection errors, timeouts, 408/409/429 and 5xx, at most **2** times (`max_retries=2`), with backoff. 400/401/403 and refusals are not retried.
- **Worst case:** about 3 × 30 s plus backoff.

## 8. Session storage, fingerprint and export
- **Storage:** summaries are stored in the user's Streamlit session, keyed by a **fingerprint**: a SHA-256 of the package *without* `ai_summary`, plus the model and `PROMPT_VERSION`. The advance rate is inside the package, so changing the sample, the rate, the model or the prompt version gives a new fingerprint.
- **Display and export:** a summary is shown and exported only when its fingerprint matches the current inputs. An older one is never shown, and the export says `"inputs changed since the summary was generated"`.
- **JSON `ai_summary` field:**
  - `{"value": {headline, points, data_limits}, "label", "model" (as reported by the API), "generated_at" (UTC), "fingerprint", "prompt_version"}` for a valid summary;
  - otherwise `{"value": null, "reason": "<short reason>"}`.

## 9. Installation
```bash
.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
```
- **Credentials:** an `ANTHROPIC_API_KEY` environment variable or a saved `ant auth login` profile. Without them the feature reports "not configured".
- **Pin:** `anthropic==1.15.0` is the version checked for `messages.parse(output_format=…)` and `parsed_output`. It is also declared as the optional extra `ai` in `pyproject.toml`.

## 10. Tests (offline)
`tests/test_ai_summary.py` uses a fake client and makes no network calls. It covers:
- the fact values and IDs;
- the code-built limits;
- uploads and BLOCK not being sent;
- a missing SDK, missing credentials and no secret leakage;
- the mapping of each error type;
- refusal and truncation;
- no fallback and the client options;
- the configurable model;
- rendering;
- each check, including collections called sales, sales called collections, a concentration risk used as a strength, and an unlabelled collections figure;
- the documented reasoning limit;
- the fingerprint and stale-export rules.

`tests/test_app.py` checks that the app runs without the SDK, and that a summary is shown only for the current inputs.

Client construction is covered too: if `anthropic.Anthropic(...)` itself raises, the function returns "unavailable" with a mapped reason and never re-raises or shows the exception text.

## 11. Live trial (prepared, not run)
`scripts/live_ai_trial.py` sends **one request per bundled sample** (2 in total).
- **Dry run by default.** Nothing is sent: it prints the model, the fact counts, an estimated input size and an upper cost bound. Paid requests need `--confirm-paid`.
- **One attempt per sample.** The client is created with `max_retries=0` and a 30 s timeout.
- **Report:** `reports/ai_live_trial_<UTC time>.json`. For each sample it records:
  - the requested and actual model, the request ID and the duration;
  - the stop reason and the token usage;
  - the estimated cost, using claude-sonnet-5-5 list prices of $2 / $10 per million input / output tokens;
  - whether the checks passed, and the exported summary or the reason it is unavailable.
- **Never recorded:** the API key and any environment variable.

```bash
.venv\Scripts\python.exe scripts\live_ai_trial.py
```
```bash
.venv\Scripts\python.exe scripts\live_ai_trial.py --confirm-paid
```

**Not done yet:** the live, paid run. It needs explicit approval, then a review of the real outputs, before the feature can be called verified.
