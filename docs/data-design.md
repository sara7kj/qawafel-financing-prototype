# Data Design — SME Financing Package Prototype

> **Status:** v0.2 — Approved. Resolves **D-02** (workbook schema) and **D-12** (synthetic data profile).
> **Baseline:** `docs/requirements.md` v0.4. Rule IDs (S-xx, R-xx), scopes and indicators refer to that document.
> **Data:** All names, IDs and amounts are fictional. Any resemblance to a real business is unintended.

---

## 1. Workbook schema (D-02, approved)

**General rules**
- One `.xlsx` file holds four sheets: `customers`, `invoices`, `payments` and `credit_notes`.
- Row 1 of each sheet is the header row. Column names are lower-case and must match exactly.
- Extra (unknown) columns are ignored, even if repeated.
- A **known** column that appears more than once in a sheet, after trimming header spaces, → **BLOCK** (S-02). The intended values would be ambiguous. The message names the sheet, the column and the column letters.
- A missing **required** sheet or column → **BLOCK** (S-02). A missing **optional** column is not an error.
- `payments` and `credit_notes` may contain only the header row.

**IDs**
- IDs are read as text and trimmed of leading and trailing spaces. Matching is exact and case-sensitive.
- A cell that is empty after trimming counts as a **missing** ID.
- A numeric cell is converted to text without decimals, so `1001` becomes `"1001"`. The generator always writes text IDs.

**Dates**
- Accepted inputs are an Excel date cell or text in ISO format `YYYY-MM-DD`.
- Any time part of a date cell is ignored.
- Anything else is **invalid** (R-06, R-11).

### 1.1 `customers`
| Column | Type | Required | Rules | Example |
|---|---|---|---|---|
| `customer_id` | text | Yes | Primary key, unique (R-01, R-02) | `CUST-007` |
| `customer_name` | text | Yes | Fictional name, used for display and R-18 | `Waha Grocery 07` |
| `city` | text | No | Informational only | `Riyadh` |
| `segment` | text | No | `grocery`, `minimarket` or `horeca`; informational only | `minimarket` |
| `payment_terms_days` | integer | Yes | 30, 60 or 90 (A-04). Used by the generator; validation does not recompute due dates. | `30` |

### 1.2 `invoices`
| Column | Type | Required | Rules | Example |
|---|---|---|---|---|
| `invoice_id` | text | Yes | Primary key, unique (R-03) | `INV-00042` |
| `customer_id` | text | Yes | FK → `customers.customer_id` (R-04) | `CUST-007` |
| `issue_date` | date | Yes | Valid date (R-06). RP check (R-14). | `2026-03-04` |
| `due_date` | date | Yes | Valid date, ≥ `issue_date` (R-06) | `2026-04-03` |
| `currency` | text | Yes | Must be `SAR` (R-07) | `SAR` |
| `net_amount` | amount | Yes | > 0 (R-05) | `12450.00` |
| `vat_amount` | amount | Yes | > 0 (R-05) | `1867.50` |
| `total_amount` | amount | Yes | > 0 (R-05). \|net + VAT − total\| ≤ 0.01 (R-16). | `14317.50` |

### 1.3 `payments`
| Column | Type | Required | Rules | Example |
|---|---|---|---|---|
| `payment_id` | text | Yes | Primary key, unique (R-08, R-09) | `PAY-00118` |
| `invoice_id` | text | Yes | FK → `invoices.invoice_id`, exactly one invoice (D-04, R-12) | `INV-00042` |
| `payment_date` | date | Yes | Valid date (R-11). VD check (R-15). | `2026-04-01` |
| `amount` | amount | Yes | > 0 (R-10). SAR, VAT-inclusive cash. | `14317.50` |

### 1.4 `credit_notes`
| Column | Type | Required | Rules | Example |
|---|---|---|---|---|
| `credit_note_id` | text | Yes | Primary key, unique (R-08, R-09) | `CN-00009` |
| `invoice_id` | text | Yes | FK → `invoices.invoice_id`, exactly one invoice (D-05, R-12) | `INV-00042` |
| `credit_date` | date | Yes | Valid date (R-11). VD check (R-15). | `2026-03-15` |
| `net_amount` | amount | Yes | > 0 (R-10) | `1000.00` |
| `vat_amount` | amount | Yes | > 0 (R-10) | `150.00` |
| `total_amount` | amount | Yes | > 0 (R-10). \|net + VAT − total\| ≤ 0.01 (R-16). | `1150.00` |
| `reason` | text | No | `return`, `damaged` or `price_adjustment`; informational only | `return` |

Payments and credit notes have no currency column. They are in the currency of their invoice, and non-SAR invoices are quarantined (R-07).

### 1.5 Relationships
```
customers 1 ──< invoices 1 ──< payments
                         1 ──< credit_notes
```
- Every payment and credit note references exactly one invoice.
- An invoice can have zero or more payments and zero or more credit notes.

---

## 2. Amounts and precision

**Internal representation**
- Every amount is converted once at read time to an **integer number of halalas**, where 1 SAR = 100 halalas.
- All sums and comparisons use integers. Floats are never used in calculations.

**Reading from Excel**
- openpyxl returns numeric cells as Python floats. Each value is converted with `Decimal(str(value))`, which recovers the shortest decimal form, so `2000.33` stays `2000.33`.
- If the value is within 0.000001 of a 2-decimal number, it is accepted at that 2-decimal value. This absorbs float noise such as `17.2500000001`.
- Otherwise the amount is **invalid**: R-05 for invoices, R-10 for payments and credit notes. It is never silently rounded.
- Text cells are accepted only if they are plain decimals: an optional sign, digits, an optional `.` and at most 2 decimals. Thousands separators or currency symbols make the amount **non-numeric**.

**Writing to Excel (generator)**
- Amounts are written as numeric cells with number format `0.00`. Each value is `halalas / 100` in Python, which gives the binary double **nearest** to the decimal amount.
- Excel stores numbers as binary doubles. Most 2-decimal amounts, such as `0.10` or `2000.33`, have **no exact binary representation**. Staying below 2^53 halalas does not change that.
- Precision is therefore not assumed. It comes from this pair of steps:
  1. **Write:** the nearest double.
  2. **Read:** `Decimal(str(value))`, which returns the shortest decimal that maps to that double, i.e. the original 2-decimal amount.
- A test writes edge-case and random halala values to a real `.xlsx` file, reads them back, and checks that **every value returns the same number of halalas**. This applies to all amounts up to SAR 10 million per record.

**Rounding**
- Rounding is **ROUND_HALF_UP** to the nearest halala, and it is applied in only three places:
  1. **VAT (generator only):** `vat = round_half_up(net × 15 / 100)`, computed once per invoice or credit note, and `total = net + vat`.
  2. **Financing estimate:** `round_half_up(eligible_halalas × rate_percent / 100)`. The rate is a whole percent from 0 to 100.
  3. **Display of ratios and shares:** values are computed exactly with `Decimal`/`Fraction` and rounded to 1 decimal place only for display. Threshold checks such as the 30% in R-20 use the **unrounded** value.
- **Validation tolerance (R-16):** a difference of 1 halala is accepted, and 2 or more → QUARANTINE. When the difference is accepted, `net_amount` is used for sales and `total_amount` for balances. Amounts are never adjusted.

**JSON export (D-11)**
- Amounts are written as **decimal strings** with 2 decimals, e.g. `"2300.38"`, so no consumer parses them as floats. Ratios use 4 decimals.
- Unavailable values are `null`, with a `reason`.

---

## 3. Synthetic merchant profile (D-12, approved)

> Every figure in this section is a **generation assumption** chosen to look plausible. None of it is a statistical finding about real wholesalers.

| Item | Value |
|---|---|
| Applicant | **Demo Foodstuff Wholesale Co.** (fictional), `APP-DEMO-001` |
| Activity | Dry food, beverages and household consumables wholesaler in Riyadh |
| Customers | **25**: 3 key accounts, 7 medium, 15 small. Segments: minimarkets, groceries, HoReCa. |
| Invoices | About **600** in RP (≈ 50 a month): Poisson(monthly rate × seasonal factor) per customer |
| Issue days | Sunday to Thursday only |
| Random seed | **20260930** (fixed; see *Reproducibility* below) |
| Export date | **2026-10-15**. No record is dated after it. |

**Reproducibility.**
- Same seed + same Python and package versions (`requirements.txt`) → **identical data content**: the same sheets, the same header and the same cell values in the same order.
- `.xlsx` files are **not** expected to match byte for byte, because the zip container and workbook metadata carry timestamps.
- Tests compare cell content, never file bytes.
- The generator uses only Python's `random.Random(seed)`. It does not use NumPy.

**Customer plan** (fixed, not random)
| Customers | Tier | Segment | Terms | Payment profile |
|---|---|---|---|---|
| CUST-001 | Key | minimarket | 60 | Prompt |
| CUST-002 | Key | minimarket | 60 | Late |
| CUST-003 | Key | minimarket | 90 | Partial |
| CUST-004, 006 | Medium | minimarket | 30 | Prompt |
| CUST-005 | Medium | minimarket | 30 | Late |
| CUST-007 | Medium | minimarket | 60 | Late |
| CUST-008 | Medium | horeca | 60 | Prompt |
| CUST-009 | Medium | horeca | 60 | Late |
| CUST-010 | Medium | horeca | 60 | Partial |
| CUST-011 – 016 | Small | grocery | 30 | Prompt |
| CUST-017 – 020 | Small | grocery | 30 | Late |
| CUST-021 – 023 | Small | horeca | 30 | Partial |
| CUST-024 – 025 | Small | horeca | 30 | Slow |

- **Names** follow the pattern `<word> <segment label> NN`. Word *n* comes from a fixed list, so CUST-007 is always `Reem Mini Market 07`.
- **Segment labels:** minimarket → `Mini Market`, grocery → `Grocery`, horeca → `Cafe`.
- **City:** `Riyadh` for every customer.

**Customer tiers**
| Tier | Count | Invoices / month (base) | Net amount per invoice (log-uniform) | Payment terms |
|---|---|---|---|---|
| Key | 3 | 4 | SAR 10,000 – 45,000 | 60 days (one key account: 90) |
| Medium | 7 | 2 | SAR 6,000 – 25,000 | 30 or 60 days |
| Small | 15 | 1.5 | SAR 2,000 – 9,000 | 30 days |

**Concentration targets (clean sample)**
- The top customer should hold about 15–22% of net sales, and the top 5 about 50–66%.
- No customer should exceed 30%, so the clean sample raises no R-20 warning. R-20 is tested in a separate case (§5.2).

**Seasonal factors (declared assumption)**
| Month | Factor | Reason assumed |
|---|---|---|
| Jan 2026 | 1.15 | Stocking before Ramadan |
| Feb 2026 | 1.30 | Ramadan begins (around mid-Feb 2026) |
| Mar 2026 | 1.10 | Ramadan / Eid al-Fitr |
| Jul–Aug 2026 | 0.85 | Summer slowdown |
| Other months | 1.00 | — |

**Payment behaviour profiles** (each customer gets one)
| Profile | Customers | Behaviour (days relative to due date) |
|---|---|---|
| Prompt | 10 | One full payment, −10 to +3 days |
| Late | 8 | One full payment, +10 to +45 days |
| Partial | 5 | 2–3 instalments. The first (40–60%) falls at −5 to +10; the rest at +15 to +60. |
| Slow | 2 | 50% of invoices paid in full at +30 to +120; 50% never paid |

**Credit notes**
- About **4% of invoices** get one credit note, and never more than one in the clean sample.
- The credit note's net amount is 5–30% of the invoice's net amount.
- It is dated 3–20 days after the invoice is issued.
- The expected credit-note ratio is about 0.5–1%. That is 4% of invoices × an average credit of about 17.5% of net ≈ 0.7%.

**Output-level targets.** After generation, a summary report checks these ranges. They are not hard-coded.
| Measure | Target range |
|---|---|
| Gross sales (excl. VAT), RP | SAR 6.5 – 8.5 million |
| Invoices in RP | 550 – 650 |
| Top customer share of net sales | 15 – 22% |
| Top 5 customers share of net sales | 50 – 66% |
| Credit-note ratio | 0.5 – 1% |
| Open balance at VD | about 1.5 – 2.5 months of sales |
| Share of open balance > 90 days overdue | 3 – 10% |
| Months with no invoices | 0 |

---

## 4. Clean sample generation rules

The generator works in this order, with one seeded random generator:

1. **Customers.** Create 25 rows with tier, terms and behaviour profile. Fictional names follow the pattern `<word> <type> NN`, e.g. `Waha Grocery 07`. All names are distinct after normalisation.
2. **Invoices.** For each customer and each RP month, draw how many invoices and on which issue days.
   - `due_date = issue_date + payment_terms_days`.
   - Draw `net`, then compute `vat` and `total` (§2). Currency is `SAR`.
   - All invoices are issued inside RP.
3. **Credit notes.** Pick about 4% of invoices and create one credit note each (§3).
4. **Payments.** For each invoice, the amount payable = invoice total − credit-note totals. Payment dates come from the customer's profile.
   - **Full:** one payment for the whole amount payable.
   - **Partial:** instalments in whole riyals, except the last, which takes the remaining halalas.
   - **Unpaid:** no payment.
5. **Export horizon.** Payments and credit notes dated **after 2026-10-15** are dropped. Their invoices stay open or partly open.
6. **Consistency checks.** The generator fails, rather than writing a file, if any check breaks:
   - Payments + credit notes ≤ invoice total, for **every** invoice, over **all** dates and not only those ≤ VD.
   - No payment or credit note is dated before its invoice's issue date.
   - Every ID is unique, and every reference resolves.
   - net + VAT = total exactly.

**Settlements after VD (R-15).**
- Payments and credit notes dated from **2026-10-01 to 2026-10-15** are valid and stay in the clean file.
- They are **OUT OF PERIOD** for every VD calculation and are reported with their count. They are not defects.
- A clean run therefore shows a non-zero OUT OF PERIOD count, and `receivables_quality_status = no_known_issues`.

---

## 5. Defect datasets

Combined defects can mask each other: a single defective payment turns the whole RP collections total to N/A. So defects are split into two kinds of dataset.

**A. `sample_defects.xlsx`**
- Contains the clean sample plus **added** defect records. Clean records are not altered.
- Holds only defects whose effect stays local, so every series and total is still computable.
- **B. Isolated test cases** (tiny workbooks) cover every defect that turns a shared series, an RP total, a ratio, a ranking or the financing estimate to N/A, plus the BLOCK cases.

All defect records use reserved IDs (`…-D..` in A, `…-T..` in B), so expected outcomes never depend on random draws.

### 5.0 Excluded-amount reporting (both datasets)
For each sheet, the excluded-records summary reports a **"Known recorded amount excluded"**.
- **Amount used:**
  - invoices and credit notes → the recorded `total_amount`;
  - payments → `amount`.
- **The amount is counted only if all of these hold:**
  - it is readable (§2);
  - it is > 0;
  - the record's ID is not duplicated (a missing ID is not a duplicate);
  - its currency is known and is `SAR`, so non-SAR values are never added to SAR. Invoices use their own `currency`. Payments and credit notes inherit the currency of their linked invoice.
- **When the currency is unknown, SAR is never assumed.** That happens when:
  - an invoice's own `currency` is missing;
  - a link's `invoice_id` is missing or matches no invoice;
  - a link's `invoice_id` is a duplicated invoice ID (an ambiguous link).
- **What the figure means:** it is the sum of values **as recorded**. It is **not** a verified financial amount, and it is shown with that label.
- **A broken field elsewhere** in the record does not stop its total from counting. For example, an unreadable `net_amount` with a readable `total_amount` still counts the total.
- **Records that cannot be counted** are reported as separate counts. Each record is counted **once**, in the first bucket that applies:
  1. `duplicate_id` (every copy);
  2. `unknown_currency`;
  3. `non_sar`;
  4. `unreadable_or_not_positive`.
- Counted records are `known_records`. `known_records` plus the four counts always equals the number of excluded records.
- The same summary is used for payments excluded as dependents (D-13).

### 5.1 Dataset A — local-effect defects
Added customer: `CUST-D20` (`Defect Test Store`, Riyadh, grocery, 30 days).

Unless stated otherwise, every added invoice:
- belongs to `CUST-D20`;
- is issued **2026-06-10** and due **2026-07-10**;
- is in currency `SAR`;
- has amounts **1,000.00 / 150.00 / 1,150.00**.

Every added customer row is in Riyadh, segment grocery, with 30-day terms.

| # | Defect added (complete record values) | Affected records | Rule | Expected result |
|---|---|---|---|---|
| A-01 | Customer: empty `customer_id`, name `Missing ID Store` | that row | R-01 | Row quarantined. Nothing else changes. |
| A-02 | Customers: `CUST-D21` `Duplicate Store A` and `CUST-D21` `Duplicate Store B` | both rows | R-02 | Both quarantined; `CUST-D21` is ambiguous. |
| A-03 | `INV-D01` with customer `CUST-D21` | `INV-D01` | R-04 | Invoice quarantined (ambiguous customer). |
| A-04 | `INV-D02` with customer `CUST-999` | `INV-D02` | R-04 | Invoice quarantined (unknown customer). |
| A-05 | Invoice with empty `invoice_id` | that row | R-03 | Quarantined. Its total of 1,150.00 is counted. |
| A-06 | Two rows `INV-D03`: 1,000.00/150.00/1,150.00 and 2,000.00/300.00/2,300.00. Plus `PAY-D03` → `INV-D03`, 2026-06-20, 1,150.00. | both rows, `PAY-D03` | R-03 | Both copies quarantined and counted under `duplicate_id` (2). `PAY-D03` is excluded as a dependent (of `INV-D03`, R-03): D-13 count 1. Its invoice ID is duplicated, so its currency is unknown: known amount 0.00, `unknown_currency` 1. No collections month becomes N/A. |
| A-07 | `INV-D04` 0.00 / 0.00 / 0.00 | `INV-D04` | R-05 | Quarantined; counted under `unreadable_or_not_positive`. |
| A-08 | `INV-D05` with `net_amount = "abc"`, VAT 150.00, total 1,150.00 | `INV-D05` | R-05 | Quarantined. Its total is readable, so **1,150.00 is counted**. |
| A-09 | `INV-D06` due date before issue date | `INV-D06` | R-06 | Quarantined. |
| A-10 | `INV-D07` currency `USD` | `INV-D07` | R-07 | Quarantined; counted under `non_sar`. |
| A-11 | `INV-D08` 1,000.00 / 150.00 / 1,150.02 | `INV-D08` | R-16 | Quarantined (difference 0.02). |
| A-12 | `INV-D09` 1,000.00 / 150.00 / 1,150.01 | `INV-D09` | R-16 boundary | **Accepted** (difference 0.01). Sales +1,000.00; open balance +1,150.01. |
| A-13 | `INV-D10` issued 2025-09-20, due 2025-10-20. Plus `PAY-D10` → `INV-D10`, 2025-10-15, 1,150.00. | both | R-14 | Both OUT OF PERIOD. Not counted in any indicator, and not a defect. |
| A-14 | `INV-D11`; `PAY-D11A` 1,150.00 (2026-06-20) and `PAY-D11B` 100.00 (2026-06-25) | `INV-D11` | R-13 | Invoice receivable unreliable (over-settled). Stays in sales (+1,000.00). Both payments stay in collections. |
| A-15 | `INV-D12`; `PAY-D12` 1,150.00 dated 2026-06-05 (before issue) | `PAY-D12` | R-17 | WARN. The payment is applied, so the balance is 0. |
| A-16 | Customer `CUST-D22` named `Reem Mini Market 07`, the same name as `CUST-007` | both customers | R-18 | WARN only. |

Dataset A adds no credit notes.

**Expected excluded-records summary (Dataset A additions only)**
| Sheet | Quarantined rows | Known recorded amount excluded | Not countable |
|---|---|---|---|
| customers | 3 (A-01, A-02 ×2) | — (no amounts) | — |
| invoices | 10: `INV-D01`, `INV-D02`, blank ID, `INV-D03` ×2, `INV-D04` – `INV-D08` | **6,900.02**: `INV-D01`, `INV-D02`, blank ID, `INV-D05` and `INV-D06` at 1,150.00 each, plus `INV-D08` 1,150.02 | duplicate_id 2 · unreadable_or_not_positive 1 · non_sar 1 |
| payments | 0 quarantined; 1 dependent (`PAY-D03`, D-13) | D-13: 0.00 | unknown_currency 1 |

OUT OF PERIOD: `INV-D10` (1,150.00) and `PAY-D10` (1,150.00).

**Expected aggregate for Dataset A**
- Every series and total is available.
- `receivables_quality_status = invoices_excluded`, with reasons R-03, R-04, R-05, R-06, R-07, R-13 and R-16.
- The financing estimate is computed.
- Exact differences from the clean sample:

| Indicator | Difference vs clean |
|---|---|
| Gross and net sales, Jun 2026 and RP | **+3,000.00** (`INV-D09`, `INV-D11`, `INV-D12`) |
| Collections, Jun 2026 and RP | **+2,400.00** (`PAY-D11A`, `PAY-D11B`, `PAY-D12`) |
| D-13 excluded payments | **1** payment, known amount **1,150.00** (`PAY-D03`) |
| Open balance, 61–90 bucket (82 days), eligible receivables | **+1,150.01** (`INV-D09`) |
| Financing estimate (70%) | +805.007 before rounding. The exact value is recomputed on the total. |
| OUT OF PERIOD | +1 invoice (`INV-D10`), +1 payment (`PAY-D10`) |

### 5.2 Isolated test cases (Dataset B)
Files: `tests/fixtures/cases/TC-xx.xlsx` (TC-01 is `.csv`). The complete expected values for every case are in `tests/fixtures/expected/cases_expected.json`. That file is **hand-written**, not computed by code.

**Base workbook (every case starts from it)**
| Sheet | Rows |
|---|---|
| customers | `CUST-T1`, `Test Customer One`, Riyadh, grocery, 30 |
| invoices | `INV-T1`, `CUST-T1`, issued 2026-08-01, due 2026-08-31, SAR, 1,000.00 / 150.00 / 1,150.00 |
| | `INV-T2`, `CUST-T1`, issued 2026-09-10, due 2026-10-10, SAR, 2,000.00 / 300.00 / 2,300.00 |
| payments | header only |
| credit_notes | header only |

**Base results**
- Gross sales: Aug 1,000.00, Sep 2,000.00, total 3,000.00.
- `INV-T1`: 30 days overdue → bucket 1–30.
- `INV-T2`: −10 days → Current.
- Financing (70%): 3,450.00 → **2,415.00**.

**Background WARNs in every non-BLOCK case**
- R-19: 10 empty months.
- R-20: `CUST-T1` holds 100% of every ranking that is available.
- R-21.
- These never change calculations.

**Record conventions**
- Payments are written as (`payment_id`, `invoice_id`, `payment_date`, `amount`).
- Credit notes are written as (`credit_note_id`, `invoice_id`, `credit_date`, net / VAT / total, `reason`), and `reason` is `return` in every case.
- "Empty" means a blank cell. A quoted value is written as text.

| Case | Exact change to base | Rule | Key expected results |
|---|---|---|---|
| TC-01 | Base `invoices` rows saved as `TC-01.csv`: header plus 2 rows, ISO dates, amounts as `1000.00` | S-01 | BLOCK |
| TC-02 | Base, but the `invoices` sheet has no `due_date` column | S-02 | BLOCK, naming `invoices.due_date` |
| TC-03 | `INV-T1` issued 2025-09-01, due 2025-10-01. `INV-T2` issued 2025-09-10, due 2025-10-10. Other values unchanged. | S-03 | BLOCK (both invoices OUT OF PERIOD) |
| TC-04 | Payment (`PAY-T1`, `INV-T1`, 2026-08-20, 0.00) | R-10 | Collections Aug N/A, RP total N/A. `INV-T1` receivable unreliable. Aging Current 2,300.00. Financing **1,610.00**. Status `invoices_excluded`. Known recorded amount excluded 0.00 (not positive: 1). |
| TC-05 | Payment (`PAY-T1`, `INV-T1`, text `"2026-13-01"`, 1,150.00) | R-11 | **Whole** collections series N/A. `INV-T1` receivable unreliable. Financing **1,610.00**. Known recorded amount excluded 1,150.00. |
| TC-06 | Payment (empty, `INV-T1`, 2026-08-20, 500.00) | R-08 | As TC-04, but the known recorded amount excluded is 500.00. |
| TC-07 | Payments (`PAY-T1`, `INV-T1`, 2026-08-20, 500.00) and (`PAY-T1`, `INV-T2`, 2026-09-15, 500.00) | R-09 | Collections Aug and Sep N/A, total N/A. Both invoices are receivable unreliable, so receivables scope is empty: aging, receivables ranking and financing are N/A. duplicate_id 2. |
| TC-08 | Payment (`PAY-T1`, `INV-X99`, 2026-08-20, 500.00) | R-12 | Collections Aug N/A, total N/A. Status `unresolved_links`; **financing `null`**. Aging Current 2,300.00, 1–30 1,150.00. The invoice is unknown, so the currency is unknown: known recorded amount excluded 0.00, `unknown_currency` 1. |
| TC-09 | Credit note (`CN-T1`, `INV-T1`, 2026-08-10, 100.00 / text `"x"` / 115.00) | R-10 | Credit notes Aug N/A and total N/A. Net sales Aug N/A, Sep 2,000.00, total N/A. Ratio and sales ranking N/A. **Gross 3,000.00.** `INV-T1` receivable unreliable; financing **1,610.00**. Known recorded amount excluded 115.00. |
| TC-10 | Credit note (`CN-T1`, `INV-T1`, 2026-08-10, 100.00 / 15.00 / 115.05) | R-16 | As TC-09; known recorded amount excluded 115.05. |
| TC-11 | Credit note (`CN-T1`, `INV-T1`, empty date, 100.00 / 15.00 / 115.00) | R-11 | Credit-note and net-sales series **entirely** N/A, with their totals, the ratio and the sales ranking. Gross 3,000.00. Financing **1,610.00**. |
| TC-12 | Credit note (`CN-T1`, `INV-X99`, 2026-08-10, 100.00 / 15.00 / 115.00) | R-12 | Sales indicators as TC-09. Status `unresolved_links`; financing `null`. Aging Current 2,300.00, 1–30 1,150.00. Currency unknown: known recorded amount excluded 0.00, `unknown_currency` 1. |
| TC-13 | Payment (`PAY-T1`, `INV-T1`, 2026-10-05, 1,150.00) | R-15 | Payment OUT OF PERIOD. Collections 0.00. Aging as base. Financing **2,415.00**. Status `no_known_issues`. |
| TC-14 | No change (the base) | R-19 | 10 empty months shown as 0 with WARN. Financing **2,415.00**. |
| TC-15 | Payments (`PAY-T1`, `INV-T1`, 2026-08-25, 1,150.00) and (`PAY-T2`, `INV-T2`, 2026-09-20, 2,300.00) | N/A rule | Collections Aug 1,150.00, Sep 2,300.00, total 3,450.00. All buckets 0.00. Receivables ranking N/A (total 0). Financing **0.00**. |
| TC-16 | Customer (`CUST-T2`, `Test Customer Two`, Riyadh, grocery, 30). Invoice (`INV-T3`, `CUST-T2`, 2026-09-01, due 2026-10-01, SAR, 500.00 / 75.00 / 575.00). | R-20 | Sales: `CUST-T1` 0.8571 (WARN), `CUST-T2` 0.1429. Receivables: 3,450.00 vs 575.00 → same shares (WARN for `CUST-T1`). Financing 4,025.00 × 70% = **2,817.50**. |
| TC-17 | Invoices (`INV-T4`, `CUST-T1`, 2026-08-31, due 2026-09-30, SAR, 100.00 / 15.00 / 115.00) and (`INV-T5`, `CUST-T1`, 2026-08-30, due 2026-09-29, SAR, 100.00 / 15.00 / 115.00) | Aging | `INV-T4` 0 days → Current. `INV-T5` 1 day → 1–30. Current 2,415.00, 1–30 1,265.00. Financing **2,576.00**. |
| TC-18 | Credit note (`CN-T1`, `INV-T1`, 2026-10-05, 100.00 / 15.00 / 115.00), valid and dated after VD | R-15 | Credit note OUT OF PERIOD, not a defect. It does **not** reduce `INV-T1`'s balance at VD (still 1,150.00 in 1–30) and does not change RP sales: credit notes 0.00, net sales 3,000.00. Financing **2,415.00**. Status `no_known_issues`. |

---

## 6. Hand-worked reference example

Small enough to check by hand. Use it as an independent oracle for calculation tests. VD = 2026-09-30, RP = 2025-10-01 … 2026-09-30.

**Customers**: `CUST-A` (30 days), `CUST-B` (60 days).

**Invoices**
| Invoice | Customer | Issue | Due | Net | VAT | Total |
|---|---|---|---|---|---|---|
| W1 | A | 2026-06-15 | 2026-07-15 | 10,000.00 | 1,500.00 | 11,500.00 |
| W2 | A | 2026-08-01 | 2026-08-31 | 4,000.00 | 600.00 | 4,600.00 |
| W3 | B | 2026-08-01 | 2026-09-30 | 6,000.00 | 900.00 | 6,900.00 |
| W4 | B | 2026-09-10 | 2026-11-09 | 2,000.33 | 300.05 ¹ | 2,300.38 |
| W5 | A | 2026-05-10 | 2026-06-09 | 3,000.00 | 450.00 | 3,450.00 |

¹ 2,000.33 × 0.15 = 300.0495, which rounds half-up to 300.05.

**Payments and credit notes**
| Record | Invoice | Date | Amount | Note |
|---|---|---|---|---|
| P1 | W1 | 2026-07-10 | 5,000.00 | partial |
| P2 | W1 | 2026-08-20 | 3,000.00 | partial |
| CN1 | W2 | 2026-08-10 | 400.00 / 60.00 / 460.00 | credit note |
| P3 | W2 | 2026-09-05 | 4,000.00 | leaves 140.00 open |
| P4 | W3 | 2026-10-05 | 6,900.00 | **after VD → OUT OF PERIOD** |

**Expected results**

*Monthly series* (all 12 months appear; every month not listed shows 0.00):
| Month | Gross sales | Credit notes | Net sales | Collections (accepted records only) |
|---|---|---|---|---|
| 2026-05 | 3,000.00 | 0.00 | 3,000.00 | 0.00 |
| 2026-06 | 10,000.00 | 0.00 | 10,000.00 | 0.00 |
| 2026-07 | 0.00 | 0.00 | 0.00 | 5,000.00 |
| 2026-08 | 10,000.00 | 400.00 | 9,600.00 | 3,000.00 |
| 2026-09 | 2,000.33 | 0.00 | 2,000.33 | 4,000.00 |
| **RP total** | **25,000.33** | **400.00** | **24,600.33** | **12,000.00** |

- Credit-note ratio = 400.00 / 25,000.33 = 0.0159998, displayed as **1.6%**.
- R-19 WARN for the 8 months with no invoices: 2025-10 to 2026-04, and 2026-07.

*Open balances at VD*
| Invoice | Open balance | Days overdue | Bucket |
|---|---|---|---|
| W1 | 11,500.00 − 8,000.00 = **3,500.00** | 77 | 61–90 |
| W2 | 4,600.00 − 460.00 − 4,000.00 = **140.00** | 30 | 1–30 |
| W3 | **6,900.00** (P4 ignored) | 0 | Current |
| W4 | **2,300.38** | −40 | Current |
| W5 | **3,450.00** | 113 | > 90 |
| **Total** | **16,290.38** | | |

*Aging*: Current 9,200.38 · 1–30 140.00 · 31–60 0.00 · 61–90 3,500.00 · > 90 3,450.00. The buckets sum to 16,290.38.

*Financing estimate* (70%)
- Eligible = 16,290.38 − 3,450.00 = **12,840.38**.
- 12,840.38 × 70% = 8,988.266 → **8,988.27**.

*Concentration*
| Ranking | CUST-A | CUST-B | Total | WARN (> 30%) |
|---|---|---|---|---|
| Net sales | 16,600.00 (**67.5%**) | 8,000.33 (**32.5%**) | 24,600.33 | A and B |
| Open balance | 7,090.00 (**43.5%**) | 9,200.38 (**56.5%**) | 16,290.38 | A and B |

*Status*
- `receivables_quality_status = no_known_issues`.
- `source_completeness_verified = false`.
- OUT OF PERIOD: 1 payment (P4, 6,900.00).
- QUARANTINE: none.

---

## 7. Processing order

The order matters because later steps depend on the results of earlier ones.

1. **File check (S-01)** — is the file a readable `.xlsx`?
2. **Structure (S-02)** — are all required sheets and columns present?
3. **Parse cells** — IDs, dates and amounts (to halalas), per §1–§2. Cells that fail to parse are recorded as such; nothing is guessed.
4. **Record rules** — the record's own fields only:
   - customers: R-01, R-02;
   - invoices: R-03, R-05, R-06, R-07, R-16;
   - payments and credit notes: R-08, R-09, R-10, R-11, R-16.
5. **Reference rules:**
   - invoice → customer (R-04);
   - payment or credit note → invoice (R-12). A reference counts as "unknown" only if **no invoice row has that ID**. A reference to a quarantined, duplicated or out-of-period invoice is a dependency, not R-12.
6. **Period rules** — applied to non-quarantined records only:
   - invoices outside RP (R-14);
   - payments and credit notes dated after VD (R-15).
   - A record that is both defective and out of period is reported as QUARANTINE.
7. **Dependencies** — links of quarantined or out-of-period invoices are excluded as dependents.
   - Links of **quarantined** invoices get record status `DEPENDENT`. This is an exclusion **reason**, not a problem class: the record has no issue of its own. It keeps the invoice ID and that invoice's rules (`dependent_on`, `dependency_rules`). Dependent payments feed the D-13 count.
   - Links of **out-of-period** invoices are reported under OUT OF PERIOD (R-14), with the same invoice reference.
8. **S-03** — if no invoice is left in sales scope → BLOCK.
9. **Propagation (§3.4)** — only own-defect payments and credit notes propagate, and only when their invoice is in sales scope or cannot be identified:
   - mark invoices receivable unreliable;
   - mark affected months, series, totals, ratios and rankings N/A;
   - set `unresolved_links`.
   - A defective record with a valid date outside RP affects no month.
10. **Over-settlement (R-13)** — on the remaining valid links dated ≤ VD.
    - **"Receivable unreliable"** is an **invoice state**, not a problem class. The invoice stays in sales if its own data is sound, but it is removed from receivables.
    - Each cause is kept with the rule and the record behind it: a defective link from step 9, or R-13 here.
11. **WARN rules** — R-17, R-18, R-19 and R-21 run at validation; R-20 needs net sales and runs with the indicators. WARN rules never change data.
12. **Build scopes and compute indicators.** Apply the N/A rules for empty scopes and zero denominators.

---

## 8. Ambiguities and limits that affect results

| # | Issue | Handling in this design |
|---|---|---|
| X-01 | R-18 "name variants under one ID" cannot occur, because names live only in `customers` and each ID has one row there (duplicates fall under R-02). | Only "one normalised name under several IDs" is checked. Normalisation is case-fold, trim, collapse spaces and remove punctuation. Fuzzy matching is out of scope. |
| X-02 | Over-settlement (R-13) is checked only on links dated ≤ VD. Links after VD could over-settle unnoticed. | Accepted. The clean generator prevents it over all dates. |
| X-03 | A defective payment with a valid date before RP, linked to an RP invoice, affects no collections month. It still marks the invoice receivable unreliable. | As stated in §7, step 9. |
| X-04 | A credit note dated before RP and linked to an RP invoice reduces the balance but appears in no credit-note month. | Not generated. It gets an R-17 WARN, because it is dated before the invoice. |
| X-05 | Collections include payments on receivable-unreliable invoices: the payment itself is valid and its invoice is in sales scope. | Per the requirements' scope definitions. |
| X-06 | Validation does not check VAT = 15% of net or due date = issue + terms. | Not required by the baseline. Not added. |
| X-07 | Amounts with more than 2 decimals beyond float noise are invalid rather than rounded. | Strict, per "no silent correction". |
| X-08 | Background WARNs (R-19, R-20) appear in every tiny test case. | Expected, and listed in §5.2. |
| X-09 | A payment dated inside RP on an invoice issued before RP (e.g. `PAY-D10`) is real cash, but it is outside "Collections from accepted records only". | Reported under OUT OF PERIOD with the invoice, not in the D-13 count. |
| X-10 | A non-SAR invoice's recorded total cannot be added to SAR amounts. | Counted under `non_sar` instead of the known recorded amount (§5.0). |
| X-11 | A link to a duplicated invoice ID, where every copy says SAR. | **Decided:** the currency stays unknown (`unknown_currency`). It is not inferred from the copies. |
| X-12 | R-13 is an invoice state (receivable unreliable), not an entry in the general issue list. | **Decided:** the UI and the JSON package must show receivable-unreliable invoices with their causes, including R-13, so they are not lost. |

---

## 9. Files

| Path | Produced by | Content |
|---|---|---|
| `scripts/generate_invoices.py` | — | The generator. Run: `.venv\Scripts\python.exe scripts\generate_invoices.py` |
| `data/sample/sample_clean.xlsx` | generator | Clean 12-month sample |
| `data/sample/sample_defects.xlsx` | generator | Clean sample + Dataset A records, appended after the clean rows |
| `data/sample/generation_report.json` | generator | Record counts and the actual profile vs §3 targets. This is a generator self-check, not the analysis engine. |
| `tests/fixtures/cases/TC-01.csv`, `TC-02.xlsx` … `TC-18.xlsx` | generator | Dataset B workbooks |
| `tests/fixtures/worked_example.xlsx` | generator | The §6 example |
| `tests/fixtures/expected/*.json` | **hand-written** | Expected results for Dataset A, Dataset B and the §6 example |
| `tests/test_generator.py` | — | Generator integrity, amount round trip and content stability |
