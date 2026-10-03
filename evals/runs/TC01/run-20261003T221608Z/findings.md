# Findings: TC01 run-20261003T221608Z

First run on the step A skill (`skill_sha` f43d60e0f1f8, commits `1d2a9ab` + `9cbd156`), on the same 2-run baseline as
[run-20261002T211606Z](../run-20261002T211606Z/findings.md) (`6d3d6a04dd64`). Graded by Claude from the transcript,
screenshots and telemetry; reviewed with the user.

**Result: Partial (19/20 auto, 13/14 manual), up from 20/20 auto and 12/14 manual.** The two run-2 misses are fixed. Two new
misses: R5 placement and a missing "Not evaluable" heading.

## Run-2 misses: before → after

| Run-2 miss | Run 2 | This run | Evidence |
|---|---|---|---|
| Q6 on the `delete+insert` key | `order_id` proposed without a Q6 on it | ✅ `orders`: 248,201,269 distinct `order_id`, 0 nulls; `ORDER_HEADER`: 248,201,269 rows, unique; "model distinct = source rows: pass" | `05a-plan-r1-r6.png` |
| R2 Signal / placement | Signal 45 (not a valid sum) under "Observed, below threshold" | ✅ Signal 35, on the "Evaluated, didn't fire" line ("only 464 partitions, < 1,000") | `05c-plan-details.png` |
| R3 near-miss text | "would fire at 60" | ✅ "Remote spill (+50) would bring Signal to 100" (30 + 20 = 50) | `05c` |
| Signal as a visible sum | R1/R6 only | ✅ Evidence tables with weight + counted for R1 (30+30+0+20 = 80) and R6 (40+30+30 = 100) | `04-plan-top.png`, `05b` |

## Expected vs observed

| Area | Expected (baseline_runs.md) | Observed | Result |
|---|---|---|---|
| Checkpoint | Ends turn before Q1 | Ended turn; Q1 ran after the reply | ✅ `02-checkpoint.png` |
| Window | 2 successful runs | 2 of 2 via Q3, both in `ACCOUNT_USAGE` (Q3a) | ✅ `03-run-in-progress.png` |
| Credit source | Labelled with reason | `attributed` for all models (100%); tests mixed (50% attributed, cache hits costed at last scanning run) | ✅ (see "Baseline drift") |
| Cost table | `orders` ≈ 80% | `orders` 0.12848 (87.9%) of 0.14625/run; Python model one row (0.00060), no separate write row | ✅ |
| R1 | #1 on `orders`, real path, lookback, `--full-refresh` | `models/marts/orders.sql`, `delete+insert` on `order_id`, 3-day lookback on `order_ts`, `--full-refresh`; Signal 80, Impact 70.3%, Priority 23.4 | ✅ |
| Fan-out | Reported | 693,681,576 vs 673,655,465 rows; FRANCHISE 335 rows / 325 ids; under "Data-quality findings" | ✅ `05b`, `05c` |
| R6 | Fires on the relationships test | #2, Signal 100, 6.3% share, Impact 4.4%, `where` filter on recent `ORDER_ID`s | ✅ |
| R4 | "Needs owner review" | `customer_loyalty_metrics`, `sales_metrics_by_location`: no reads in 2 days, capped by history guard | ✅ |
| R5 | "Evaluated, didn't fire" line | **#3 under Recommendations**: `raw_pos_order_header`, Signal 70 (40 + 0 + 30), Impact ~0%, Priority 0, text says "Not recommended at this time" | ❌ |
| "Not evaluable" section | Present (empty → "None") | **Heading missing** (nothing was unavailable) | ❌ auto `section_not_evaluable` |
| Read-only / cost | 0 violations, bounded `ACCOUNT_USAGE` | 0 violations; 7 `ACCOUNT_USAGE` queries, all bounded; 52 queries; ~0.0123 analysis credits; 33.5 GB scanned | ✅ |

Spot-checked figures against baseline_runs.md: 693,681,576 rows written, 248,201,269 `ORDER_HEADER` rows, 12.45 GB
local spill. All match.

## Surprises

- **R5 follows the rubric into a contradiction.** The rubric places any Signal ≥ 60 under Recommendations regardless of
  Impact. A passthrough staging view with 3 heavy dependents scores 40 (dependents ≥ 10% of credits) + 30 (≥ 2
  dependents) = 70 even though "does real work" is 0, so there's nothing to save. The skill listed it and then said
  "not recommended". Run 2 put R5 on the "didn't fire" line, so the outcome isn't stable either.
- **Baseline drift: credits are now `attributed`.** `QUERY_ATTRIBUTION_HISTORY` caught up (latest Oct 2), so this is
  the first run on attributed credits: total 0.14625/run (estimated was 0.1171), `orders` 87.9% (was ~81%), R6 test
  6.3% (was 5.2%). Ranking and rule outcomes are unchanged.
- **Q6 guessed a schema.** The first Q6 on `FRANCHISE` used `TASTY_BYTES_DBT_DB.RAW_POS` and failed twice before the
  agent looked up the real source location. The manifest has the source `relation_name`; it should be used. 2 more
  failures against `CORTEX_CODE` came from Cortex Code itself, as in runs 1–2.
- Q4 still shows 1 outside read of `ORDERS` (scan ratio 1.00), the manual baseline check from 2026-10-02. It didn't move
  R2 (35).

## Skill change needed (file + section)

Hold until TC01 runs 2–3 are done on this `skill_sha`, so the pass rate is from one version.

1. scoring-rubric.md R5: make "the view does real work (joins or aggregations)" a **gate**, not a 30-point row. A
   passthrough view has nothing to save by materializing. Rebalance the remaining rows (e.g. 60 / 40).
2. scoring-rubric.md §3 placement (and SKILL.md Phase 3): a rule with Impact < 1% goes on the "Evaluated, didn't fire"
   line even at Signal ≥ 60 ("fires, but nothing to save").
3. SKILL.md Phase 4 template: every section heading is always printed; an empty section says "None".
4. SKILL.md Phase 3 (Q6 on a joined dimension): take the table name from the manifest source's `relation_name`, never a
   guessed schema.

## Test-plan change needed

- baseline_runs.md: add the attributed figures (total 0.14625/run, `orders` 87.9%, R6 test 6.3%) next to the estimated
  ones; cases.json TC01 manual check "orders is ~80%" → "orders is 80–90% (estimated ~81%, attributed ~88%)".
