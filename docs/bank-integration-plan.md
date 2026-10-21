# Bank Integration Plan — Wrong Statuses, No Sandbox

> **Status:** Approved v0.2 (Task 04 of the Qawafel Graduate SE assessment). Documentation only.
> **Brief:** the bank's API returns the wrong status 3% of the time, and there is no sandbox. Plan a safe integration, and say what we would tell the bank on a call.
> **Builds on:** `docs/technical-specification.md` (§3.3 review/submission binding, §4.2 states, §5 bank guarantees). Nothing here is implemented.
> **Labels:** **PROPOSED** = our suggestion, not yet agreed with the bank · **AGREED** = confirmed with the bank (none yet).

---

## 1. Principles
1. **The bank decides; we report.** No status in our system means credit approval unless the bank's decision is **confirmed** (§4).
2. **A reported status is a claim, not a fact.**
   - **If** "3%" means 3% of status reads, about one read in 33 is expected to be wrong. If it means 3% of applications, or something else, the risk is different (Q1, Q3).
   - A repeated GET, or a webhook that agrees with a GET, can repeat the same error, so it is **not** proof.
3. **No lost sends, no silent duplicates.**
   - We store the intent to send.
   - We resend only with the same key and body, and only within the agreed idempotency guarantee.
   - Delivery is at-least-once, so we always look the submission up before any resend.
4. **Fail closed.** When something is unclear, we pause and ask a human. We never guess.
5. **No sandbox does not mean testing in production with fake data.** Synthetic data goes to the bank's production only if the bank explicitly agrees in writing.

---

## 2. Questions about the 3% (to answer before building)

| # | Question | Why it matters |
|---|---|---|
| Q1 | **What counts as "wrong"?** A stale status (correct but late), a wrong value (e.g. `declined` instead of `in_review`), the status of a different application, or a missing / unknown value? | Each needs a different defence: waiting vs confirming vs rejecting |
| Q2 | **Which endpoints and statuses are affected?** Status GET only, webhooks, the submit response, or all of them? Are decision statuses affected? | Decides where confirmation is mandatory |
| Q3 | **How was 3% measured?** Over what period and volume, per call or per application, and against which source of truth? | 3% of calls and 3% of applications are very different risks |
| Q4 | **Is the error sticky?** If a read is wrong, are the next GETs for the same application also wrong (cached or replicated state), or independent? Does the webhook come from the same source as the GET? | If sticky, retrying proves nothing; if independent, it still proves little at this rate |
| Q5 | **Can a status go backwards?** E.g. from `declined` to `in_review`. Is there a status history with event IDs or sequence numbers? | Lets us detect stale and out-of-order reports |
| Q6 | **What is the authoritative source** we may use to confirm a status? Examples: a status-history endpoint, a daily reconciliation report, or a named operations contact. | This becomes our confirmation evidence (§4) |
| Q7 | **Known causes and a fix timeline?** Is the rate stable, improving or rising? | Sets how long the manual confirmation must stay |

**Idempotency, timeouts and limits:**

| # | Question |
|---|---|
| Q8 | Does submit accept an `Idempotency-Key`? Does the same key always return the same bank reference without creating a second application, and for how long is the key remembered? |
| Q9 | Can we search for an application by **our** reference or key after a timeout? |
| Q10 | After a timeout, can the bank still have accepted the request? What are the timeout, rate limit and retry guidance? |
| Q11 | What are the authentication method, certificate rotation and allowed IPs? Is there any test tenant or test applicant in production? |

---

## 3. Sending safely: timeouts, duplicates, idempotency, conflicts

**Sending (as in the specification, §3.3):**
- A submission is accepted only for a reviewed package version and its fingerprint.
- The submission row and an outbox message are committed together.
- A sender worker delivers the package with a fixed `Idempotency-Key` = `submission_id`, and sends **the same key and the same body on every retry**.

**Read errors and send errors are handled differently.** A failed read changes nothing at the bank. A failed POST may have happened **after** the bank accepted the package.

| Situation | Handling |
|---|---|
| **Read error** (GET status: timeout, 429, 5xx) | Retry the read with backoff and jitter (**PROPOSED:** up to 5 attempts over about 30 min). The submission state does not change; the last stored report stays. Then alert operations |
| **Send error that may have happened after acceptance** (POST: timeout, dropped connection, and 5xx unless the bank guarantees the request was not processed) | Treat it as an **unknown delivery** and set the state to `delivery_unknown`. Then: |
| | 1. **Look up first** by our reference or key (Q9). If the bank has the package, record the bank reference |
| | 2. If it is not found, **and** the idempotency guarantee (Q8) is agreed **and** the key is still within its agreed lifetime, resend with the **same key and the same body** |
| | 3. If there is no such guarantee, or the key lifetime has passed, send it to **human escalation**. No automatic resend, and **never create a new submission** to "try again" |
| **POST 429, or a 4xx the bank defines as "not processed"** | Only if the bank confirms that these responses mean nothing was created (Q10): retry later with the same key and body (429), or mark `send_failed` with the bank's error code and show it to the analyst (4xx validation). Without that confirmation, treat them as unknown delivery (row above) |
| **Duplicate detected** (two bank references for one key, or the bank reports two applications) | **Stop sending (kill switch, §6)**, open an incident, and ask the bank to void one. Never resolve it silently |
| **Out-of-order or old status** | Store every report as an event (source, bank event time or sequence, received time). Apply only reports that are newer by bank time or sequence (Q5). Keep older ones as history |
| **Contradictory status** (a regression, or GET and webhook disagree) | Do not apply it automatically. Mark the application `status_conflict` and send it to the human queue (§4) |
| **Unknown status value** | Store it and flag it; never map it to a known status by guessing |

---

## 4. Reported vs confirmed status

We keep two fields per submission. They are never merged.

| Field | Source | Shown as |
|---|---|---|
| `reported_status` | The latest valid report from a GET or webhook, with its source and time | "Reported by bank — unconfirmed" |
| `confirmed_status` | Set only with **evidence**: who confirmed it, when, how, and a reference | "Confirmed" |

**Evidence that can confirm a status** (to agree on the call, Q6). A source counts only if **all** of these hold:
- The bank **designates it in writing** as a reference source suitable for confirming statuses. A name such as "status history" or "reconciliation report" is not enough on its own.
- The evidence **identifies the application** (the bank reference and our reference) and the **time or event of the status** it confirms.
- The bank has stated whether the 3% fault can affect that source. A source fed by the same faulty component is not independent evidence.

Possible sources, if they meet these conditions:
1. a bank record such as a status-history entry or a reconciliation report;
2. a human confirmation through an agreed bank channel, e.g. the bank's operations contact, recorded with name, time and ticket or e-mail reference.

**Not evidence:** a second GET, a webhook matching a GET, or the same value seen repeatedly.

**A newer report contradicts a confirmed status:**
- Open `status_conflict` and **stop acting on the confirmed status** until it is re-verified with evidence. Examples: no applicant notification, no closing.
- Keep the earlier confirmation, its evidence and the full report history. Nothing is overwritten.

**Which statuses need confirmation (PROPOSED):**
- **Must be confirmed before anyone acts on them:** decision statuses (approved, declined), information requests sent to the applicant, and any status that closes the application.
- **May be shown as "reported" without confirmation:** progress statuses such as `received` or `in_review`. They do not wait for a final confirmation, and they **never** close an application.

**Human role (Qawafel operations):**
- Works a queue of statuses to confirm and of conflicts.
- Confirms each one through the agreed channel and records the evidence.
- Never edits what the bank reported.
- The analyst and the applicant see both fields with clear labels.

---

## 5. Closing an application
An application is closed **only** when all of the following are true:
1. A **final status** (a decision or a withdrawal) is **confirmed** with evidence (§4).
2. No submission is in `delivery_unknown`, `send_failed` or `status_conflict`.
3. No duplicate is open with the bank.
4. The result was recorded and communicated to the applicant.

- **Unconfirmed final status:** the application stays in `awaiting_confirmation`. If it is not confirmed in time, it is **escalated**, not closed (§6).
- **Progress statuses** never move an application towards closing.
- **Specification:** these rules match `technical-specification.md` §4.2 (v0.3). There is no direct path from a reported status to `closed`.

---

## 6. Monitoring, alerts, kill switch and escalation

**Metrics:**
- send success rate and latency;
- `delivery_unknown` count and age;
- unconfirmed consequential statuses and their age;
- **mismatch rate**: reported status ≠ confirmed status, over confirmed items;
- conflicts, unknown status values and duplicates;
- 4xx / 5xx / 429 rates;
- authentication failures.

| Signal | Threshold | Action | Agreed? |
|---|---|---|---|
| Duplicate bank application for one key | any | **Automatic pause of sending**; page on-call; incident with the bank | PROPOSED |
| Authentication or certificate failures | 3 in a row | Automatic pause; page on-call | PROPOSED |
| Mismatch rate (rolling last 100 confirmations) | > 5% | Alert; review with the bank; consider a pause | PROPOSED |
| `delivery_unknown` older than | 30 min | Alert operations; run the lookup | PROPOSED |
| Consequential status unconfirmed for | 1 business day | Escalate to the bank operations contact | PROPOSED |
| 5xx / timeout rate | > 10% over 15 min | Alert; the sender backs off | PROPOSED |

**Reading the mismatch rate.** It is measured over the items we confirmed, and those are not a random sample: they are mostly consequential statuses, and suspicious ones are more likely to be checked.
- So it is **not directly comparable** with the bank's 3% until both sides use the same definition of "wrong" and the same denominator (per read or per application; which statuses; Q1, Q3).
- **PROPOSED:** also confirm a small random sample of progress reports, to estimate the rate without that selection effect.

**Kill switch:**
- **When paused:** the sender stops dispatching. New submissions are still accepted and queued, and the UI shows "sending paused".
- **Reading continues** while paused.
- **Resuming** needs a named person and a short note.

**Escalation path:** on-call engineer → Qawafel operations lead → bank technical contact. Bank response times are to be **AGREED**.

---

## 7. Testing without a sandbox

1. **Contract examples signed off by the bank:**
   - real-shaped request and response bodies, error bodies, the status list, webhook payloads with signatures, and idempotency behaviour (same key → same reference);
   - these become fixtures for automated contract tests.
2. **A local mock bank (simulator)**, driven by those examples, that can produce:
   - success;
   - a timeout **after** the bank accepted the request, and a timeout **before**;
   - 429 / 5xx;
   - duplicate deliveries;
   - wrong statuses at a configurable rate (3%, and much higher), either **random** or **sticky per application**;
   - out-of-order and regressing webhooks;
   - unknown status values.
3. **Tests against the mock:**
   - state-machine rules;
   - one bank application per key;
   - recovery after a crash between "sent" and "recorded";
   - the lookup-before-resend rule, including a 5xx returned **after** acceptance, and escalation instead of a resend when the key lifetime has passed;
   - a newer report that contradicts a confirmed status, which must open `status_conflict` and keep the earlier evidence;
   - no automatic closing on unconfirmed statuses;
   - alerts and the kill switch.

**What this proves — and what it does not:**
- It proves **our** behaviour against **our understanding** of the bank.
- It does **not** prove the bank's real behaviour: the real error pattern (Q1–Q5), latency, authentication and certificates, their validation rules, or failure modes nobody has described.
- Those can only be learned in the controlled rollout below.

---

## 8. Gradual rollout (to agree with the bank)

| Phase | What happens | Exit criteria (PROPOSED) |
|---|---|---|
| 0. Contract | Answers to Q1–Q11; signed contract examples; mock and tests pass | All questions answered or risk accepted in writing |
| 1. Connectivity | Authentication, certificates and network checked in production using **only calls the bank approves** (e.g. a health or status endpoint). **No application data, and no synthetic data unless the bank agrees in writing** | Successful authenticated read; alerts work |
| 2. Read-only shadow | Packages are still delivered through the **current manual channel**. With the bank's permission, we only **read** statuses for those real applications and compare them with the bank's confirmed records | Real mismatch rate measured over an agreed volume; sticky vs random known |
| 3. Limited live | A small, agreed number of real applications (with applicant consent) are submitted through the API. **Every** consequential status is confirmed by a human; daily reconciliation | No duplicates; no unresolved `delivery_unknown`; mismatch within the agreed bound |
| 4. Widen | Volume increases step by step; manual confirmation stays for decision statuses until the bank fixes the 3% | Bound holds at each step; any duplicate → back to phase 3 |

---

## 9. What I would say to the bank on the call

> "We want to integrate safely, and we will not act on a status we cannot confirm.
> First, help us understand the 3%: what 'wrong' means, which statuses it affects, how it was measured, and whether a wrong status can repeat on the next read.
> Second, there is no sandbox, so we propose signed contract examples, which we test against a local mock, then read-only checks in production on applications you approve. We will not send test data to your production without your written agreement.
> Third, we need an idempotency key on submit and a way to look up our reference after a timeout, so a retry never creates a second application.
> Finally, we need an authoritative way to confirm decision statuses and a named contact for escalation. Until then, we confirm every decision with your team by hand."

**Decisions I want to leave the call with:**
1. A written definition of "wrong status", and the measured pattern (per call or per application, sticky or not).
2. The confirmation source for decision statuses: a status history, a reconciliation report, or a named operations contact.
3. Idempotency-Key support and lookup by our reference (or an agreed alternative).
4. Approval of the contract examples, and the format in which they will be provided.
5. Permission and scope for phases 1–3, including whether any synthetic data is ever allowed in production.
6. Rate limits, timeouts, authentication details, and escalation contacts with response times.
7. Who owns fixing the 3%, and when we review the rate together.
