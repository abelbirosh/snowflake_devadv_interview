# Findings: TC01 run-20261002T203346Z

Skill version `skill_sha` in meta.json (before the post-TC01 fixes). Graded by Claude from the transcript, the
screenshots and the telemetry; reviewed with the user.

## Expected vs observed

| Area | Expected (baseline_runs.md) | Observed | Result |
|---|---|---|---|
| Skill trigger | Loads `dbt-cost-tuning` from a natural prompt | `skill` tool called first with `dbt-cost-tuning` | ✅ |
| Phase 0 checkpoint | Show project, window, capability matrix, ask run frequency, **wait** | Printed the environment summary and capability matrix, then went straight into Phase 1. No frequency question | ❌ |
| Capability probes | ACCOUNT_USAGE ok, QAH empty → estimated, history depth guard | Matches: QAH NULL → `estimated`; history depth ~1 day via the new 31-day-bounded probe | ✅ |
| Runs | 2 SUCCESS builds | Both found (12:19, 12:37) | ✅ |
| Cost table | orders ≈ 80%, total ≈ 0.113/run, parent CALL excluded | orders 0.09439 (83.4%), total ~0.113 (= mean of 0.122 and 0.104), CALL excluded | ✅ |
| R1 orders | Fires, #1, incremental diff on `order_ts` | Fires, #1; Impact 66.7, Effort 2.4, Priority 27.8, Big bet: all recomputed and correct | ✅ |
| R1 unique_key | A verified unique key | Proposed `order_detail_id` as "a natural unique key". **It is not unique**: ORDERS has 693,681,576 rows and 673,655,465 distinct `order_detail_id` (20.0M duplicates) because RAW_POS_FRANCHISE has 335 rows / 325 distinct `franchise_id`. MERGE on it would fail | ❌ |
| R6 tests | Fires on ORDER_DETAIL relationships test | "Not evaluable": tests 4.1% < 5%. Run 2's tests were all zero-scan (cache); the skill averaged them as 0 instead of costing them by the last scanning execution (rubric §2). Costed correctly, tests ≈ 8% of run-1 credits → Signal 100 | ❌ |
| R3 spill | Below threshold, Signal 50 | Below threshold, Signal 50, 12.45 GB local in both runs, correct reason | ✅ |
| R4 unused | "Needs owner review" | Placed under "Not evaluable"; the reasoning itself (history < 30 days caps Signal at 50) is correct | ⚠️ |
| R2 / R5 | Don't fire | Didn't fire, but filed under "Not evaluable" (that section is for missing data or privileges) | ⚠️ |
| Read-only | 0 non-read statements | 0 violations; 33 queries (14 SELECT, 13 ALTER SESSION, 3 SHOW, 2 GET, 1 DESCRIBE); 3 local bash commands, all in a temp dir | ✅ |
| Cost guardrail | ACCOUNT_USAGE queries time-bounded | 6 ACCOUNT_USAGE queries, 0 unbounded; ~0.0036 est. credits for the whole analysis | ✅ |

Minor:
- Model path given as `models/orders.sql`; actual is `models/marts/orders.sql`.
- "tests (49 total)" vs 50 tests in baseline_runs.md.
- Python write query (13,093 rows) reported as an unattributed line instead of linking its rows to `sales_metrics_by_location` (SKILL.md Phase 2.2).
- Incremental filter uses strict `>` on `MAX(order_ts)` with no lookback, so late-arriving rows are missed.
- 2 failed queries against database `CORTEX_CODE` were issued by Cortex Code itself, not by the skill.

## Surprises
- The skill's numbers (credits, shares, Signal/Impact/Effort/Priority) were exactly right; the failures were all in
  judgment steps the rubric describes but SKILL.md doesn't force: the checkpoint wait, the zero-scan costing before thresholds, key verification, and section placement.
- Data-quality bug in the project: duplicate `franchise_id`s in RAW_POS_FRANCHISE inflate ORDERS by 20.0M rows (3%),
  which also inflates revenue in ORDERS and the spill in its join. The skill should catch this, because rows written > rows in the grain table is a cheap signal.

## Skill change needed (file + section)
1. SKILL.md Phase 0.4: make the checkpoint a hard stop. End the turn after the checkpoint; do not run Q1 until the user replies.
2. SKILL.md Phase 2 + scoring-rubric.md §2 / R6: apply zero-scan costing per node before computing shares or thresholds; show the per-test share.
3. SKILL.md Phase 3 R1 + queries.md: verify the proposed `unique_key` (COUNT vs COUNT(DISTINCT) on the model), and flag fan-out when the model writes more rows than its grain source.
4. SKILL.md Phase 4: "Not evaluable" only for missing data/privileges; evaluated non-firing rules are omitted; R4 under the guard goes to "Needs owner review".
5. SKILL.md Phase 3 R1 diff: use the real model path from the manifest (`original_file_path`), and add a lookback to the incremental filter.

## Test-plan change needed
- TC01 manual check 3: drop "says recurrence evidence is limited" (only applies to a 1-run window).
- TC01: add "proposed unique_key verified unique" and "fan-out in orders flagged".
- cases.json: `r6_relationships_test` is a false positive when R6 appears under "Not evaluable"; check for R6 in Recommendations instead.
