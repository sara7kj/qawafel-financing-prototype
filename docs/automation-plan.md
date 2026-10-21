# Automation Plan — Replacing Manual Uploads

> **Status:** Approved v0.2 (Task 05, bonus, of the Qawafel Graduate SE assessment).
> **This is a future plan. Nothing in it is implemented.** No connector, schedule or integration exists in the prototype.
> **Brief:** today the customer gathers and uploads 12 months of files by hand for every financing request. How would we automate it, what first and why, where could the data come from, where does a human stay, and how do we know it is better for the customer and the bank?
> **Builds on:** `technical-specification.md` (Approved v0.4: file versions, ingestion attempts, validation runs, package versions and reviews) and `bank-integration-plan.md` (Approved v0.2: confirming bank statuses).
> **Labels:** **PROPOSED** = target or threshold not yet agreed · **MEASURED** = a result we have actually measured (none yet).

---

## 1. Starting point and principles
**Today:** a manual upload of an invoice workbook and of PDF bank statements. The prototype validates one workbook; PDF extraction is designed, not built.

**Principles:**
1. **No API is assumed.** Neither the merchant's system nor the bank is assumed to offer one. Each source moves up the ladder in §2 only when a real, authorised route exists.
2. **Arriving automatically is not the same as correct or complete.** Every automated pull goes through the same validation as a manual upload. `source_completeness_verified` stays `false` unless an agreed, authoritative completeness check exists.
3. **Same pipeline, new inputs.** Automation only changes how a `source_file` version is created. Validation, packages, reviews and submissions stay as specified.
4. **The manual upload always remains available** as the fallback (§7).

---

## 2. Where the data can come from (a ladder, per source)

| Level | Invoices, payments, credit notes | Bank data | Notes |
|---|---|---|---|
| L0 — today | Manual upload of the workbook | Manual upload of PDF statements | Baseline |
| L1 — guided export | A saved **export profile** for the merchant's accounting or invoicing system: the column mapping to our schema, checked once and reused | A bank-provided statement export (CSV or PDF), if the bank offers one | The merchant still clicks "export", but no reformatting is needed |
| L2 — scheduled export | The merchant's system drops the export on a schedule to an agreed channel, e.g. SFTP or a secure upload link | Scheduled statement export, if the bank supports it | No credentials to the merchant's system are stored by us |
| L3 — authorised API connector | A connector to the accounting or e-invoicing platform, **only if** the vendor offers an API and the merchant authorises access (e.g. OAuth, revocable) | An **authorised account-information channel** (e.g. open-banking consent), **only if** it is available and permitted for this use | Highest automation; depends on third parties, consent and regulation |

A merchant may stay at L0 or L1 indefinitely. That is a valid outcome.

---

## 3. What to automate first, and why

| Option | Impact | Effort | Risk | Priority |
|---|---|---|---|---|
| **L1 export profile for invoices** | High (assumed): removes reformatting, which we expect to be a common cause of BLOCK / S-02. To be checked against the phase-0 baseline | Low: mapping and a pre-check before upload; reuses `loader` and `validation` | Low: no stored credentials; the merchant still chooses what to send | **1st** |
| L2 scheduled invoice export | Medium–high: removes the gathering step | Medium: a channel, a schedule, failure handling | Medium: incomplete drops, stale files | 2nd |
| PDF statement extraction (already designed) | High for trust: needed to verify collections | Medium–high | Medium: extraction errors | In parallel with the 2nd |
| L3 accounting / e-invoicing connector | High, but only for merchants on supported systems | High: one connector per vendor, consent, upkeep | Medium–high: vendor changes, access scope | 3rd, for the most common system only |
| L3 bank account-information channel | High: replaces PDFs and allows reconciliation | High; depends on bank and regulator | High: consent and regulation | Last, only where an authorised route exists |

**First practical step (limited):**
- **What:** an **L1 export profile for one accounting system**, used by a small pilot group of merchants (**PROPOSED:** 3–5).
  - We define and test the column mapping to `data-design.md` §1 once.
  - The merchant exports the 12 months with that profile and uploads the file as today.
  - A pre-check shows S-01 / S-02 problems **before** the formal run.
- **Why first:** it targets what we expect to be the most frequent manual error, the wrong format (to confirm in phase 0), with almost no new risk. It keeps the merchant in control of what is sent, and it reuses the existing validation unchanged.

---

## 4. Handling new, changed, duplicate and failed data

**Provenance and versions:**
- Every pull, manual or automated, that **produces a file** creates a **new immutable `source_file` version**, linked to its `ingestion_attempt` (see below). Each version records:
  - the source type, connector and profile version;
  - the account or system identifier (masked);
  - the requested date range and the pull time;
  - a checksum and the record counts per sheet.
- **A full 12-month snapshot is taken per financing request**, not a stream of small updates, so each package can be traced to exactly one snapshot.

**New and changed records:**
- Between two versions we compare records by business ID, **within the same merchant and source**, and **only for IDs that are present and unique** in both versions.
- **Missing or duplicated IDs** are listed as **ambiguous**, with their rows from both versions. We do **not** infer that a record was added or removed from them.
- A change never edits an earlier version.
- If data changes after a package was reviewed, a **new package version** is built, and it needs a **new review** (`technical-specification.md` §3.3).

**Duplicates:**
- Duplicates follow the existing rules: R-03 / R-09 quarantine every copy.
- They are **never merged automatically**. The same record appearing in two pulls is handled by comparing versions, not by de-duplicating inside one snapshot.

**Pull attempts and transfer completeness (`transfer_complete`):**
- Every pull, successful or not, is recorded as an **`ingestion_attempt`** (`technical-specification.md` §2.2). It holds:
  - the source and the requested date range;
  - start and end times;
  - the status and the failure reason;
  - an optional `source_file_id`.
  A failed attempt is recorded **even when no file was produced**.
- **`transfer_complete`** is the check that the **transfer** worked. It is *not* proof that every business record arrived:
  - **Structure:** all expected sheets and columns are present and the file is readable. This proves the structure is sound **only**.
  - **Records:** when the source reports reference counts or totals for the requested range, we compare against them. When it does not, the attempt says **"record completeness not proven"**.
- **Partial files:** a partial file, if one exists, is kept **for diagnosis only**. It is linked to its failed attempt and never enters validation runs, indicators or packages.
- **Retries:** a failed attempt is retried within limits (**PROPOSED:** 3 attempts over 24 h). After that, the merchant and the analyst are notified, and the manual upload is offered.
- `source_completeness_verified` **stays `false`**: neither a complete transfer nor matching reference counts prove that the merchant's export holds all of its business activity.

**Freshness:**
Three separate dates are shown with every package:
1. **Last successful update:** the end time of the latest attempt that passed `transfer_complete`.
2. **Coverage end:** the latest date the source states the pull covers, if it states one.
3. **Latest transaction date:** the newest invoice, payment or credit note in the data.

How they are used:
- An old *latest transaction date* may simply mean **no activity**. On its own it is **not** a freshness failure.
- Freshness is judged on the **last successful update**, and on the **coverage lag** (requested end date minus coverage end).
- Empty months still raise R-19.

---

## 5. Where a human stays
| Step | Who | Why it stays manual |
|---|---|---|
| Approving a new export profile or connector mapping | Qawafel analyst, with the merchant | A wrong mapping would silently mislabel every future pull |
| Correcting ambiguous or defective data (quarantine, R-18 name clashes, unresolved links) | The merchant corrects; the analyst checks | Only the merchant knows which value is right; every correction is audited |
| Reviewing each package version before submission | Qawafel analyst | Required by `technical-specification.md` §3.3 |
| Confirming consequential bank statuses | Qawafel operations | `bank-integration-plan.md` §4: a reported status is not proof |
| The credit decision | The bank | Never automated or implied by this system |

---

## 6. Measuring success

The **baseline** is measured on the current manual flow **before** any automation is switched on. Every result is reported per level (L0–L3) and per pilot cohort.

| Metric | Definition | Baseline | Target | Result |
|---|---|---|---|---|
| Package preparation time | From request start to `ready_to_submit`. Median and p90, in working hours | to measure | **PROPOSED:** −50% median for L1 pilots | not yet measured |
| Manual interventions | Manual actions per application: uploads, re-uploads, data corrections, reformatting, support contacts | to measure | **PROPOSED:** −50% | not yet measured |
| First-run error rate | Share of applications whose first validation run is BLOCK, and the share of quarantined records | to measure | **PROPOSED:** BLOCK on first run < 5% | not yet measured |
| Bank clarification rate | Share of submitted packages where the bank asks for corrections or more data | to measure | **PROPOSED:** no increase vs baseline | not yet measured |
| Transfer completeness | Share of `ingestion_attempt`s that pass `transfer_complete`, reported separately: (a) structure only; (b) also matched to the source's reference counts or totals. Attempts without reference figures count as "record completeness not proven" | n/a (new) | **PROPOSED:** ≥ 98% pass; the share in (b) reported, no target until sources are known | not yet measured |
| Update freshness | When a package is built: (a) time since the **last successful update**; (b) **coverage lag** = requested end date minus the source's coverage end. The age of the latest transaction is reported but **not** used, because it may mean no activity | n/a (new) | **PROPOSED:** (a) ≤ 24 h and (b) ≤ 1 day, for L2/L3 | not yet measured |
| Pull failure rate | Failed `ingestion_attempt`s / all attempts; time to recover or fall back to manual upload | n/a (new) | **PROPOSED:** < 2% | not yet measured |

- **Better for the customer:** less time and fewer manual steps, with no rise in corrections.
- **Better for the bank:** fewer clarification requests, and fresher data with clear provenance.
- **Quality first:** automation is judged better only if quality is the same or better. Speed alone is not enough.

---

## 7. Rollout and fallback

| Phase | What | Exit criteria (PROPOSED) |
|---|---|---|
| 0. Baseline | Measure the manual flow (§6) | Enough applications for a stable median |
| 1. L1 pilot | Export profile for one accounting system, 3–5 merchants; the manual path stays open | Preparation time and interventions improve; error rate does not rise |
| 2. L2 shadow | The scheduled export runs **alongside** the manual upload; both files are validated and the packages compared | Automated and manual packages match, or every difference is explained |
| 3. L2 default | The scheduled export becomes the default for the pilot merchants | Transfer, freshness and failure metrics within target for 4 weeks |
| 4. L3 connectors | Only for a supported system or bank channel with authorised access; same shadow-then-default pattern | Same criteria, plus a consent and access review |

**Stop and fall back:**
- Each merchant and each connector has an **on/off switch**.
- **Switching off** stops new pulls and leaves all earlier versions intact. The application continues with the **manual upload**, with no loss of work.
- **Automatic switch-off (PROPOSED):** a mapping change detected, repeated attempts failing `transfer_complete`, or a rise in the first-run error rate.
- **Turning back on** needs a named person and a short reason, as with the bank kill switch.
