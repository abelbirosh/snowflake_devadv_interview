# Findings: TC01 run-20261002T211606Z

First run on the fixed skill (`skill_sha` 6d3d6a04dd64, commit `4237e6b`), on the same 2-run baseline as
[run-20261002T203346Z](../run-20261002T203346Z/findings.md) (`cf9751ab9530`). Graded by Claude from the transcript,
screenshots and telemetry; reviewed with the user.

**Result: Partial (20/20 auto, 12/14 manual), up from Partial (17/18 auto, 8/12 manual).** All five run-1 failures are
fixed. Two smaller misses remain.

## Run-1 failures: before → after

| Run-1 failure | Run 1 | This run | Evidence |
|---|---|---|---|
| Checkpoint is a hard stop | Went straight into Q1 | ✅ Ended its turn at the checkpoint; Q1 ran only after the reply | `02-checkpoint.png` (input prompt waiting), transcript 14:17 → 14:21 |
| `unique_key` claim | `order_detail_id` called "a natural unique key" (20.0M duplicates) | ✅ Not claimed; uses `delete+insert` on `order_id` and says why | `05a-plan-r1-r6.png` |
| Join fan-out | Not reported | ✅ 693,681,576 vs 673,655,465 rows; FRANCHISE 335 rows / 325 `franchise_id`; "fix before making incremental" | `05b-plan-details.png` |
| R6 test cost | "Not evaluable": tests 4.1% overall, the relationships test 2.7% (cache hits averaged in as 0) | ✅ Fires at #2: 0.00609 credits/run = 5.2% after zero-scan costing, Signal 100 | `03-run-in-progress.png` (Q3: raw 0.00305, `zero_scan_runs` 1), `05a` |
| Section placement | R4, R2, R5 under "Not evaluable" | ✅ R4 candidates under "Needs owner review"; "Not evaluable: None"; R5 on the "Evaluated, didn't fire" line | `05b` |
| R1 diff details | `models/orders.sql`, strict `>` with no lookback | ✅ `models/marts/orders.sql`, 3-day lookback, `--full-refresh`, verify step | `05a` |

## Expected vs observed (other checks)

| Area | Expected (baseline_runs.md) | Observed | Result |
|---|---|---|---|
| Window | 2 successful runs | "Window: 2 successful build runs, 12:19 → 12:40" | ✅ |
| Credit source | Labelled, with reason | `estimated`: QAH not populated; concurrency caveat | ✅ |
| Cost table | orders ≈ 81%, total ≈ 0.1161 | orders 0.09439 (81.2%), total 0.11625; parent CALL excluded; Python write query (0.00101) merged into the model, credits dropped | ✅ |
| R1 scores | Signal 80 (unique-key evidence 0) | 30 + 30 + 0 + 20 = 80; Impact 64.9 (81.2 × 0.8), Effort 2.4, Priority 21.6 | ✅ |
| R3 | Below threshold, Signal 50 | Signal 50, 12.45 GB local in both runs | ✅ |
| Read-only / cost | 0 violations, bounded ACCOUNT_USAGE | 0 violations; 6 ACCOUNT_USAGE queries, all bounded; ~0.004 credits; 39 queries | ✅ |
| Q6 on the proposed key | Q6 run on `ORDERS` before proposing a `unique_key` | Q6 ran only on `RAW.FRANCHISE.franchise_id` (to find the fan-out source). `unique_key='order_id'` was proposed without a Q6 on `ORDERS`; the evidence line says "not verified" | ❌ |
| R2 | On the "Evaluated, didn't fire" line | Under "Observed, below threshold" with **Signal 45**. 45 isn't a valid R2 sum (weights 35/25/25/15). By the plan's own evidence (464 partitions < 1,000, `ORDER_TS` "high-cardinality", 1 read) R2 scores at most 35 (< 40), so it belongs on the "Evaluated, didn't fire" line | ❌ |

Minor:
- R3 note says "would fire at 60 if remote spill appeared". Remote spill is weighted 50, so it would be Signal 100.
- 1 query failed on a syntax error (`rows` used as an alias) and was rerun correctly. 2 more failures against `CORTEX_CODE` came from Cortex Code itself, as in run 1.
- Wall time 1,215 s (run 1: 700 s) includes ~4 min waiting at the checkpoint for the user.

## Surprises

- `delete+insert` on `order_id` is a good adaptation to the fan-out: it doesn't need a row-level unique key, so "not
  verified" is honest. But SKILL.md says to run Q6 before proposing *any* `unique_key`, and it doesn't describe this path.
- The "1 external read" of `ORDERS` behind R2 is most likely the `COUNT(DISTINCT order_detail_id)` check run by hand on
  2026-10-02 to verify the baseline (Q4 excludes only dbt-tagged queries). Eval setup queries leak into
  `ACCESS_HISTORY` and can move R2/R4 evidence.

## Skill change needed (file + section)

1. SKILL.md Phase 3 R1 (line ~105): when Q6 shows fan-out, allow `incremental_strategy='delete+insert'` on the grain key
   of the driving table (`order_id`), and still run Q6 on that key (non-null, distinct count) before proposing it.
2. scoring-rubric.md R2 / SKILL.md Phase 3: show the R2 evidence rows with their weights (as R1 and R6 already do), so
   the Signal is a visible sum. Only Signal 40–59 goes to "Observed, below threshold"; below 40 goes on the
   "Evaluated, didn't fire" line.
3. Minor: the R3 near-miss text should state the weight that's missing (remote spill +50), not a fixed "60".

## Test-plan change needed

- baseline_runs.md R2 basis: "No outside reads of `orders`" is no longer true after the manual baseline checks. Either
  exclude the eval user's ad-hoc queries in Q4 for the eval, or record the read and expect R2 ≤ 35 ("Evaluated, didn't fire").
- RUNBOOK.md screenshot 02: the checkpoint can't show "2-run window" (Q1 runs after it). It says "last 5 (or however
  many exist)"; the 2-run window is checked in the plan header.
- TC08: `cases.json` lists one prompt, but the hard-stop checkpoint needs a second ("Yes, that project. Skip frequency, show per run.").
