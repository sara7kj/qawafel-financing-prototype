# Requirements — SME Financing Package Prototype

> **Status:** v0.4 — Approved baseline — subject to documented implementation changes. (Task 01 of the Qawafel Graduate SE assessment.)
> **Data:** All data in this project is synthetic. No real company or customer data is used.

---

## 1. Problem, goal and users

### 1.1 Problem
An SME wholesaler wants financing. Today they upload 12 months of invoices (Excel) and bank statements (PDF) by hand. Qawafel has to turn these files into a structured package that a partner bank can review. Raw files are inconsistent and hard to trust. Reviewers also can't easily see sales quality, collection behaviour or receivables risk in them.

### 1.2 Goal
Turn 12 months of invoice-level data into a **clear, checked, decision-support package**:
- validate the data and show exactly what was excluded and why;
- compute transparent indicators: sales, collections, receivables aging and concentration;
- present results with explicit assumptions and limitations.

**Non-goal:** the system does **not** approve credit, assign a credit score or predict default probability. The outputs help a human reviewer and do not replace one.

### 1.3 Users and roles

| Role | Who | What they need |
|---|---|---|
| **Wholesaler (applicant)** | Owner or finance staff of the SME | Upload data once in a documented format, see whether it is acceptable, and fix problems before submission. |
| **Qawafel analyst** | Qawafel operations / credit-ops staff | Validate data, review indicators and flags, and prepare the package for the bank. |
| **Bank credit reviewer** | Partner bank's credit officer | Get a consistent, trustworthy summary with clear caveats so they can make their own decision. |

*The Qawafel analyst is the primary user of the prototype.*

---

## 2. Scope

### 2.1 Full proposed system (context only — not built)
1. Applicant onboarding, consent and authentication (role-based access).
2. Invoice upload (Excel) **and** bank-statement upload (PDF) with extraction.
3. Reconciliation of recorded payments against bank-statement transactions.
4. Optional direct data sources: e-invoicing records, accounting software and open banking.
5. Validation, indicators and an analyst review workflow with audit trail.
6. Package submission to the partner bank's API, with status tracking.

### 2.2 Prototype scope (what we will build)
- A single-user local web app (Streamlit) that loads **one synthetic Excel workbook in a documented format** with invoices, payments and credit notes.
- A fixed **reporting period (RP): 2025-10-01 to 2026-09-30, inclusive**, and a fixed **valuation date (VD): 2026-09-30**.
- Validation with explicit handling of record defects, period limits and their effect on linked invoices (§3, §5).
- **Sales are kept separate from collections.** Supports **partial payments** and **credit notes linked to invoices**.
- Indicators (§3.3): monthly sales and credit notes, monthly collections, receivables aging, customer concentration and credit-note ratio.
- An **illustrative financing estimate** that uses declared, adjustable assumptions. It is not a bank policy or credit decision.
- A **JSON package export**.
- **Planned, not yet implemented:** an AI analyst summary (§2.4).
- A sample data generator plus documented assumptions (built in a later phase).

### 2.3 Out of scope for the prototype
- PDF bank-statement extraction and any real bank / open-banking connection.
- Verifying payments against bank statements. **All synthetic payments are marked "unverified".**
- Credit decisions, credit scores and default-probability estimates.
- Authentication, multi-tenant storage, submission to a bank API and production hosting.
- Multi-currency conversion.

### 2.3a Automated analyst summary — rule-based (default)
- This is the default summary: **"Automated analyst summary — rule-based"**. It is not AI and needs no SDK, key or network, so a reviewer can try the whole project for free.
- **Built by fixed rules** from the exported package. It contains:
  - key figures with their basis;
  - warnings (R-20, receivables quality, receivable-unreliable invoices including R-13, quarantine, D-13, R-17, R-18, R-19);
  - the code-built data limits;
  - follow-up questions triggered by those warnings.
- **No new financial calculations and no approval or rejection.**
- **BLOCK:** the summary shows the block reasons and no figures.
- **N/A:** an N/A value stays N/A with its reason, and a true zero is shown as zero.
- **Where it appears:** in the UI's first tab, on the BLOCK screen, and as `automated_summary` in the JSON package.
- **Code:** `src/financing/rule_summary.py`. **Tests:** `tests/test_rule_summary.py`.

### 2.4 AI analyst summary (optional — requires API key; offline-tested, not live-verified)
- It uses **only computed indicators and flags**, through a fact sheet. It never reads raw records.
- The core app works fully without it.
- In this phase it is limited to the bundled synthetic samples. The model is `claude-sonnet-5-5`, which is configurable.
- Design, checks and limits: `docs/ai-summary.md`.

---

## 3. Core definitions

### 3.1 Record handling classes
| Class | Meaning | Effect |
|---|---|---|
| **BLOCK** | A structural problem stops computation. | No indicators are produced. |
| **QUARANTINE** | The record itself is defective. | The record is excluded and listed with its reason. Its effect on linked invoices follows §3.4. |
| **OUT OF PERIOD** | The record is valid but dated outside the period an indicator uses. | It is excluded from that indicator only. It is counted and shown with its reason. **It is not a defect.** |
| **WARN** | A suspicious but usable value. | The record stays in calculations, and the issue is shown in the review and the package. A WARN never excludes or changes a record; exclusions come only from QUARANTINE, OUT OF PERIOD or §3.4. |
| **N/A indicator** | An indicator can't be computed reliably, e.g. its input scope is empty or its denominator is 0. | That indicator shows N/A with a reason (`null` + reason in JSON). Other indicators are still shown. |

Two related ideas are **not** problem classes:
- **Dependent exclusion.** A sound payment or credit note excluded only because its invoice is quarantined. It keeps a reference to the invoice and that invoice's rules (D-13).
- **Receivable unreliable.** A state of an invoice (§3.4, R-13). The invoice stays in sales if it is sound, but it is removed from receivables. Each cause is kept with its rule.

### 3.2 Period rules
- **Reporting period:** the period is declared, not inferred. The app does **not** infer completeness from the first and last invoice, or from every month having invoices.
- **All 12 months always appear.** A month with no invoices shows 0 with a WARN: *"No invoices recorded. The data alone cannot show whether this means no activity or an incomplete export."* An empty month never blocks analysis.
- **The app does not claim the history is complete.** Date checks cannot prove an export is complete, so the package always sets `"source_completeness_verified": false` in this prototype.
- **Out-of-period records:**
  - Invoices issued outside RP are excluded from all indicators.
  - Payments and credit notes dated after VD are excluded from all VD calculations.
- **Limitation:** receivables cover only invoices issued within RP. Older open invoices are not in the data.

### 3.3 Indicator definitions
| Indicator | Definition | Amount basis | Grouped by | Record scope |
|---|---|---|---|---|
| **Gross sales** | Sum of invoice net amounts | Excl. VAT | Invoice issue month | Sales scope |
| **Credit notes** | Sum of credit-note net amounts | Excl. VAT | Credit-note date month | Sales scope (credit notes dated in RP) |
| **Net sales** | Gross sales − credit notes | Excl. VAT | Per month and RP total | Sales scope |
| **Credit-note ratio** | Credit notes ÷ gross sales over RP. N/A if gross sales = 0. | Excl. VAT (both) | RP total | Sales scope |
| **Collections from accepted records only** | Sum of recorded payment amounts | Amount as recorded (cash incl. VAT) | Payment date month | Collections scope |
| **Open balance** | Invoice total − credit-note totals − payments, using only links dated ≤ VD | Incl. VAT | At VD | Receivables scope |
| **Days overdue** | VD − due date in days. **≤ 0 → Current**: an invoice due on VD is Current, and overdue starts the day after the due date. | — | At VD | Receivables scope |
| **Aging buckets** | Current, 1–30, 31–60, 61–90, > 90 days overdue | Incl. VAT | At VD | Receivables scope |
| **Sales concentration** | Top 5 customers by **net sales**. Share = customer net sales ÷ total net sales. N/A if total ≤ 0. | Excl. VAT | RP total | Sales scope |
| **Receivables concentration** | Top 5 customers by **open balance**. Share = customer open balance ÷ total open balance. N/A if total = 0. | Incl. VAT | At VD | Receivables scope |
| **Financing estimate** | Advance rate × eligible receivables, where eligible receivables = reliable open balances (receivables scope) ≤ 90 days overdue (D-09) | Incl. VAT | At VD | Receivables scope |

Collections (incl. VAT) and sales (excl. VAT) use different bases. They are shown side by side but never subtracted or divided.

Collections are always labelled **"Collections from accepted records only"** and are never presented as total cash collected. Next to the indicator, the app shows how many payments were excluded because their invoice was quarantined, together with their known amount (§3.5). These exclusions do **not** make any month N/A (D-13).

Defective payments and credit notes can turn some of these indicators, or some of their months, to N/A. See §3.4.

### 3.4 Record scopes and the effect of defects on linked invoices
| Scope | Includes |
|---|---|
| **Sales scope** | Non-quarantined invoices issued in RP, and their non-quarantined credit notes dated in RP. |
| **Collections scope** | Non-quarantined payments dated in RP that are linked to a sales-scope invoice. Valid payments on quarantined invoices are excluded as dependents (D-13). |
| **Receivables scope** | Sales-scope invoices that are **not** marked "receivable unreliable", with their non-quarantined links dated ≤ VD. |

Each indicator states which scope it used, together with the record counts in that scope.

**How defects propagate:**

A *defective* record here means a record quarantined for its **own** defect (§5.2). Records excluded only as dependents of a quarantined invoice, and OUT OF PERIOD records, do not propagate. A defective record with a valid date after VD has no effect on VD calculations or on RP series.

**Affected-month rule** (for defective payments and credit notes):
- **Month known** (the date is valid and in RP): that month of each affected series is N/A, with the reason.
- **Month unknown** (the date is missing or invalid): the whole affected series is N/A.
- **Totals and ratios:** any RP total, or any ratio that depends on the unknown value, is N/A. The app never sums the unaffected months and presents the result as a full-period total.

- **Defective invoice** (any invoice rule, including net + VAT ≠ total).
  - The invoice is quarantined and excluded from **all** scopes.
  - Its payments and credit notes are excluded as dependents.
- **Defective payment.**
  - **Collections:** the affected months, or the whole series, are N/A (affected-month rule), and so is the RP total.
  - **Receivables, invoice identifiable:** the linked invoice is marked **"receivable unreliable"**. It and all its links are removed from open balance, aging, receivables concentration and the financing estimate. The app shows the reason and the excluded open amount, where it can be computed.
  - **Receivables, invoice not identifiable** (missing or unknown reference): `receivables_quality_status` becomes `unresolved_links`, and the financing estimate is `null` with the reason.
  - Sales indicators are not affected.
- **Defective credit note.** Its amount checks cover net, VAT **and** total.
  - **Gross sales stays available**, because the invoices themselves are sound.
  - **Credit notes and net sales:** the affected months, or the whole series, are N/A (affected-month rule), and so are their RP totals.
  - **Credit-note ratio and sales concentration** are N/A for RP, with the reason.
  - **Receivables:** same as for a defective payment, depending on whether the invoice is identifiable.
- The app **never** drops a defective record and then shows a result that looks complete.

**Receivables quality status** (`receivables_quality_status`): one value, plus the list of reasons behind it. The most severe applicable value is used.

| Value | Meaning |
|---|---|
| `unresolved_links` | At least one defective payment or credit note cannot be linked to an invoice, so open balances may be overstated. The financing estimate is `null`. Aging and receivables concentration are still shown, with this status beside them. |
| `invoices_excluded` | Some invoices were quarantined or marked receivable unreliable. Receivables figures exclude them, and the reasons are listed. |
| `no_known_issues` | No known defect affects the receivables calculation. |

This status describes **known** issues only. It is separate from `source_completeness_verified` (§3.2), which is always `false`.

### 3.5 Excluded-records summary
For each sheet and each reason, the app shows:
- the number of excluded records;
- the **"Known recorded amount excluded"**:
  - **Amount used:** the recorded `total_amount` for invoices and credit notes, and `amount` for payments.
  - **Counted only when** the value is readable and > 0, the record's ID is not duplicated, and, for invoices, the currency is SAR.
  - It is a sum of recorded values, **not** a verified financial amount, and may understate.
  - A broken field elsewhere in the record, such as an unreadable `net_amount`, does not stop a readable total from counting.
- separate counts of records whose amount can't be counted: `duplicate_id` (every copy), `unknown_currency`, `non_sar` and `unreadable_or_not_positive`. Each record is counted once. Duplicates are never summed.
- Payments and credit notes inherit their invoice's currency. A missing, unknown or duplicated invoice reference means the currency is unknown, and SAR is never assumed (data-design §5.0).

*(Implementation change v0.4 → data-design v0.2: the amount basis is now defined per sheet, and the label is renamed.)*

---

## 4. User stories and acceptance criteria

### Wholesaler (applicant)

**US-01 — Upload one workbook in the documented format**
As a wholesaler, I want to upload one Excel workbook that follows a documented template so that Qawafel can process my data consistently.
- AC1: The app accepts `.xlsx` only. Any other type is rejected with a message, and nothing is analysed.
- AC2: If a required sheet or column is missing, the app returns **BLOCK** and names the missing sheet or column.
- AC3: After a successful load, the app shows a record count per sheet and the declared RP and VD.

**US-02 — See data problems before submission**
As a wholesaler, I want to see which records have problems so that I can correct them before Qawafel reviews my application.
- AC1: Each issue lists its sheet, row number, record ID, rule broken and class: BLOCK, QUARANTINE, OUT OF PERIOD or WARN.
- AC2: For a QUARANTINE that marks an invoice "receivable unreliable", the issue also names the affected invoice.
- AC3: The issue list can be downloaded as CSV.
- AC4: When there are no issues, the app shows an explicit "no issues found" state.

### Qawafel analyst

**US-03 — Validate records and see what was excluded**
As an analyst, I want defective and out-of-period records isolated and explained so that indicators rest on reliable data and I know what they leave out.
- AC1: Every validation rule in §5.2 produces the stated class and the stated effect on linked records.
- AC2: The excluded-records summary follows §3.5. Duplicated records are never added into a known amount.
- AC3: **BLOCK** happens only for the structural cases in §5.1. A high share of excluded records does not block analysis.
- AC4: Every indicator shows its record scope and the number of records in it.

**US-04 — Monthly sales and collections over the reporting period**
As an analyst, I want sales and collections shown as separate monthly series so that I can see whether revenue turns into cash.
- AC1: Gross sales, credit notes and net sales are grouped by issue month or credit-note month. Collections are grouped by payment month. All of them follow §3.3.
- AC2: All 12 months from Oct 2025 to Sep 2026 appear. A month with no invoices shows 0 and the §3.2 warning.
- AC3: The credit-note ratio is shown, or N/A with a reason when gross sales = 0.
- AC4: When there is a defective credit note whose month is known, that month of credit notes and net sales shows N/A. When its month is unknown, both series are N/A. In both cases their RP totals and the credit-note ratio are N/A with the reason (§3.4). Gross sales stays available.
- AC5: When there is a defective payment whose month is known, that collections month shows N/A. When its month is unknown, the whole collections series is N/A. In both cases the RP collections total is N/A with the reason (§3.4).
- AC6: Collections are labelled "Collections from accepted records only". The app shows the count and known amount of payments excluded because their invoice was quarantined. No month becomes N/A because of these exclusions alone (D-13).
- AC7: For a known test dataset, every monthly value matches the expected values exactly (to the halala).

**US-05 — Receivables aging at the valuation date**
As an analyst, I want open receivables grouped by overdue age at VD so that I can judge collection risk.
- AC1: An invoice due on 2026-09-30 is **Current**. An invoice due on 2026-09-29 falls in **1–30**.
- AC2: The bucket totals equal the total open balance of receivables scope.
- AC3: A partially paid invoice shows only its remaining balance. A fully paid or fully credited invoice does not appear.
- AC4: Payments and credit notes dated after VD are excluded from balances, and the screen shows how many there are and why.
- AC5: If receivables scope is empty, aging shows N/A with a reason.

**US-06 — Customer concentration**
As an analyst, I want two separate top-5 rankings so that I can see dependence on a few buyers in both sales and receivables.
- AC1: Ranking A lists the top 5 customers by net sales. Ranking B lists the top 5 by open balance. Each share uses that indicator's own total and scope (§3.3).
- AC2: When a ranking's total is ≤ 0, it shows N/A with a reason. Ranking A is also N/A when there is a defective credit note (§3.4).
- AC3: A WARN appears when one customer's share exceeds the threshold (D-08). The threshold is labelled *"experimental — not a bank policy"*.

**US-07 — Correct settlement of partial payments and credit notes**
As an analyst, I want payments and credit notes applied only to their linked invoice so that balances are neither overstated nor double-counted.
- AC1: An invoice with several valid payments shows the correct remaining balance.
- AC2: A credit note reduces only the invoice it references.
- AC3: If valid payments plus credit notes dated ≤ VD exceed the invoice total, the invoice is marked "receivable unreliable (over-settled)". It stays in sales scope if its sales data is valid.
- AC4: A defective payment or credit note linked to a known invoice marks that invoice "receivable unreliable" (§3.4). The defective record is never just dropped while its invoice stays in receivables.
- AC5: Credit-note checks cover net, VAT and total. If net + VAT differs from total by more than 0.01 SAR, the invoice or credit note is quarantined. Amounts are never auto-corrected, and no amount is silently chosen.

**US-08 — Illustrative financing estimate**
As an analyst, I want an indicative amount based on declared assumptions so that I can frame the conversation with the bank.
- AC1: Estimate = advance rate × eligible receivables (§3.3). The rule and the rate are both shown on screen.
- AC2: The advance rate can be changed, but only within **0% to 100%**. Values outside that range are rejected. The default is 70% (D-09).
- AC3: The estimate is always labelled *"Illustrative assumption — not a bank policy or credit decision."*
- AC4: If `receivables_quality_status` is `unresolved_links` (§3.4), the estimate is `null` (N/A on screen) with the reason. Aging and receivables concentration are still shown, with the status beside them.
- AC5: If receivables scope is empty, the estimate is N/A with a reason.

### Bank credit reviewer

**US-09 — Receive a structured JSON package**
As a bank reviewer, I want a single JSON package so that I can review every application in the same format.
- AC1: The package includes the applicant (synthetic) ID, RP, VD, the indicators from US-04 to US-08 with their record scopes, the excluded-records summary, `source_completeness_verified`, `receivables_quality_status` with its reasons, all WARNs and the disclaimers.
- AC2: Any unavailable value is `null` with a `reason` field. It is never 0 or omitted.
- AC3: Every number in the package matches the screen exactly.

**US-10 — Understand limitations**
As a bank reviewer, I want limitations stated clearly so that I don't treat unverified or partial data as complete.
- AC1: Every collection figure is labelled "Unverified — not reconciled to bank statements".
- AC2: The package always sets `"source_completeness_verified": false` and states that completeness cannot be proven from dates alone (§3.2).
- AC3: The package contains a fixed disclaimer: the outputs support review and are **not** a credit approval or a proven default probability.

### Planned (in scope, not implemented)

**US-11 — AI analyst summary**
As an analyst, I want a short narrative of the key indicators so that I can draft my review faster.
- AC1: The summary is generated only from computed indicators and flags, and every number it cites matches the dashboard.
- AC2: It is clearly labelled as AI-generated, and it states any N/A indicators, `receivables_quality_status` and `source_completeness_verified`.
- AC3: If the summary is disabled or generation fails, all other features still work.

---

## 5. Data risks and validation rules

### 5.1 Structural — BLOCK
| # | Condition |
|---|---|
| S-01 | The file is unreadable or is not `.xlsx`. |
| S-02 | A required sheet or column is missing. |
| S-03 | No valid invoice remains in RP after validation. |

### 5.2 Record rules
Applies to the customers (C), invoices (I), payments (P) and credit notes (N) sheets.

| # | Rule | Class | Effect on linked records |
|---|---|---|---|
| R-01 | Customer ID missing | QUARANTINE (customer row) | Invoices cannot reference it, so they fall under R-04. |
| R-02 | Customer ID duplicated | QUARANTINE (all copies) | The ID is treated as **ambiguous** (R-04). |
| R-03 | Invoice ID missing, or duplicated | QUARANTINE (all copies) | All payments and credit notes referencing that ID are excluded as dependents. No copy is chosen arbitrarily. |
| R-04 | Invoice customer ID is unknown or ambiguous | QUARANTINE (invoice) | Its links are excluded as dependents. |
| R-05 | Invoice net, VAT or total is missing, non-numeric, zero or negative | QUARANTINE (invoice) | Its links are excluded as dependents. |
| R-06 | Invoice issue or due date is missing or invalid, or the due date is before the issue date | QUARANTINE (invoice) | Its links are excluded as dependents. |
| R-07 | Invoice currency is not SAR | QUARANTINE (invoice) | Its links are excluded as dependents. |
| R-08 | Payment or credit-note ID is missing | QUARANTINE | Defective payment or credit note (§3.4). |
| R-09 | Payment or credit-note ID is duplicated, on the same invoice or on different invoices | QUARANTINE (all copies) | Defective payment or credit note (§3.4) for each copy. **Every** linked invoice becomes receivable unreliable. Nothing is counted twice. |
| R-10 | Payment amount, or a credit note's net, VAT or total, is missing, non-numeric, zero or negative | QUARANTINE | Defective payment or credit note (§3.4). |
| R-11 | Payment or credit-note date is missing or invalid | QUARANTINE | Defective payment or credit note (§3.4). Its month is unknown, so the whole affected series is N/A. |
| R-12 | Invoice reference on a payment or credit note is missing or unknown | QUARANTINE | Defective payment or credit note (§3.4). `receivables_quality_status` = `unresolved_links`, and the financing estimate is `null`. |
| R-13 | Payments + credit notes dated ≤ VD exceed the invoice total | Invoice marked receivable unreliable (over-settled) | The invoice stays in sales scope. |
| R-14 | Invoice issued outside RP | OUT OF PERIOD | The invoice and its links are excluded. |
| R-15 | Payment or credit note dated after VD | OUT OF PERIOD | Excluded from balances, collections and credit notes. |
| R-16 | net + VAT differs from total by more than 0.01 SAR, on an invoice or a credit note | QUARANTINE | Defective invoice or defective credit note (§3.4). No auto-correction, and no amount is silently chosen. |
| R-17 | Payment or credit note dated before its invoice's issue date | WARN | None (still applied). |
| R-18 | Customer name variants under one ID, or one name under several IDs | WARN | None. Concentration may be split or merged incorrectly. |
| R-19 | A month in RP has no invoices | WARN | None (§3.2). |
| R-20 | One customer's share exceeds the concentration threshold | WARN (business risk, experimental threshold) | None. |
| R-21 | Payments are not verified against bank statements | WARN (always shown) | None. |
| R-22 | Fabricated or related-party invoices | Not detectable from this data | Stated as a limitation. |

---

## 6. Questions for the customer and provisional assumptions

### 6.1 Questions (before building)
**Bank / product**
1. Which financing product is this: invoice financing, a working-capital line or something else? The answer changes which indicators matter.
2. Which indicators and format does the bank expect in the package? Is there a template or schema?
3. What are the bank's eligibility rules: invoice age, buyer type, concentration limits or minimum history?
4. What advance rate or limit logic does the bank use? We only use an illustrative rate.

**Data**
5. What does a wholesaler's export look like today: which system, columns and one file or many?
6. How are payments recorded? Are they linked to specific invoices, or only to the customer account?
7. How are returns and discounts recorded: credit notes, negative invoices or ignored?
8. Are net, VAT and total all available per invoice and per credit note? Is everything in SAR?
9. Are there stable customer identifiers, such as a CR or VAT number, or only names?
10. Should the analysis use a fixed reporting period, as we assume, or the submission date?
11. Should open invoices issued before the reporting period be included in receivables?

**Process / trust**
12. Who confirms the package before it goes to the bank — the wholesaler, Qawafel or both?
13. What consent, retention and access rules apply to the data?
14. How should unverifiable data, such as payments without bank evidence, be presented?

### 6.2 Provisional assumptions (until answered)
| # | Assumption |
|---|---|
| A-01 | All amounts are in SAR. Invoices and credit notes carry net, VAT (15%) and total. Payments carry one recorded amount. |
| A-02 | One workbook holds four required sheets: customers, invoices, payments, credit_notes. The payments and credit_notes sheets may be empty. |
| A-03 | Every payment and credit note references exactly one invoice ID (approved as D-04 and D-05). |
| A-04 | Payment terms are 30, 60 or 90 days per customer, so due date = issue date + terms. |
| A-05 | RP is 2025-10-01 to 2026-09-30 inclusive, and VD is 2026-09-30. |
| A-06 | Receivables include only invoices issued within RP. |
| A-07 | Payments are recorded by the wholesaler and are unverified. |
| A-08 | Customers are identified by a synthetic customer ID, not by name. |
| A-09 | The advance rate and the eligibility rule are illustrative, declared assumptions, not a credit decision (D-09). |
| A-10 | Concentration thresholds are experimental and not a bank policy (D-08). |

---

## 7. Decisions

### 7.1 Approved
| # | Decision |
|---|---|
| D-01 | RP is 2025-10-01 to 2026-09-30 inclusive. VD is 2026-09-30. |
| D-03 | There is no BLOCK for empty months or inferred coverage. Months with no invoices are shown as 0 with a WARN (§3.2). |
| D-04 | Each payment is linked to exactly one invoice. There is no automatic allocation. |
| D-05 | Each credit note is linked to exactly one invoice. |
| D-06 | Aging buckets are Current, 1–30, 31–60, 61–90 and > 90 days overdue. |
| D-08 | A WARN is raised when one customer's share exceeds 30%. This is an experimental threshold, not a bank policy. |
| D-09 | Financing estimate = advance rate × reliable receivables (receivables scope, §3.4) that are ≤ 90 days overdue. The default rate is 70%, adjustable from 0% to 100%. This is an illustrative assumption only. |
| D-10 | The AI summary uses computed indicators and flags only. It is implemented and tested offline, not yet live-verified. Details: model `claude-sonnet-5-5` (configurable); no server-side fallback; 30 s timeout per attempt; at most 2 retries for transient errors; bundled synthetic samples only; summary stored per session and keyed by an input fingerprint. See `docs/ai-summary.md`. |
| D-11 | The package is exported as JSON. Unavailable values are `null` with a reason. |
| D-14 | The default summary is rule-based ("Automated analyst summary — rule-based", §2.3a). The AI summary is a separate optional feature that requires an API key. The AI connection has not been tested live. |
| D-13 | Valid payments on quarantined invoices are excluded from collections. The indicator is labelled "Collections from accepted records only" and shows the count and known amount of excluded payments. These exclusions alone never make a month N/A. |
| D-02 | Workbook schema: as defined in `docs/data-design.md` §1–§2 (v0.2). |
| D-12 | Synthetic data profile and defect datasets: as defined in `docs/data-design.md` §3–§5 (v0.2), with seed 20260930. |

### 7.2 Open
None.
