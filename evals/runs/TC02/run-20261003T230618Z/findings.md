# Findings: TC02 run-20261003T230618Z

TC02 (reworded trigger, frequency given up front) on `skill_sha` f43d60e0f1f8, same 2-run baseline as the TC01 runs.
Graded by Claude from the transcript, screenshots and telemetry; reviewed with the user.

**Result: Partial (9/9 auto, 4/5 manual).** The plan is correct and has monthly savings, but the checkpoint asked for the
run frequency the user had already given.

## Expected vs observed

| Check | Expected | Observed | Result |
|---|---|---|---|
| Trigger | Skill loads from the paraphrase, not by name | `dbt-cost-tuning` invoked on the first prompt ("Our dbt build runs daily and credits are creeping up…") | ✅ `01-skill-triggered.png` |
| Frequency | Not asked again (prompt says "runs daily") | **Asked:** "How often does this dbt build run? (e.g. daily, twice daily, hourly?)" plus whether the 5-run window is OK. After "Yes, that project." it said "I'll assume daily runs for monthly projections (30x)" | ❌ `02-checkpoint-asks-frequency.png`, `03-checkpoint-answered-q1.png` |
| Monthly savings | per-run × ~30 | R1 0.103 cr/run (3.08 cr/month); R6 0.006 cr/run (0.19 cr/month). 0.1028 × 30 = 3.08, 0.0065 × 30 = 0.195 | ✅ `04-plan-top-monthly.png` |
| Ranking | Same as TC01 | R1 `orders` #1 (Signal 80, Impact 70.3%), R6 relationships test #2 (Signal 100, 4.4%); R3 below threshold (50); R4 needs owner review; R2 35 / R5 on "didn't fire" | ✅ |
| Estimates | Not guarantees | "These are conservative estimates, not guarantees"; "Monthly projections assume daily runs (30×/month). Adjust if your cadence differs." | ✅ `05b-plan-details.png` |
| Read-only / cost | 0 violations, bounded | 0 violations; 7 `ACCOUNT_USAGE` queries, all bounded; 49 queries; ~0.0036 analysis credits; 511 s wall | ✅ |

Other content matches TC01 runs 2–3: `delete+insert` on `ORDER_ID` with Q6 (0 nulls, 248,201,269 = `ORDER_HEADER` rows),
fan-out (693,681,576 vs 673,655,465; FRANCHISE 335 / 325), "Not evaluable: None".

## Surprises

- **The frequency instruction is ignored even though SKILL.md has it** ("Ask for the run frequency … unless the user
  already gave it"). The model asks by template, then recovers by assuming daily. The answer still came out right, but the
  user is asked a question they already answered.
- **No failed source-schema guesses this run** (TC01 failed on this 3/3 times). Q6 on `ORDER_HEADER` and FRANCHISE ran
  first time. The only 2 failed queries came from Cortex Code itself (`CORTEX_CODE` database).
- **R6 `where` filter hard-codes `TASTY_BYTES_DBT_DB.RAW.ORDER_HEADER`** instead of `{{ source('tb_101', 'ORDER_HEADER') }}`
  (TC01 runs used `source()`). It works, but it breaks if the source moves.
- **Q4 contamination is growing.** Q4 now returns 8 objects, including the staging views `RAW_POS_ORDER_HEADER` (4 reads),
  `RAW_POS_LOCATION` (3), `RAW_POS_TRUCK` (3), all from the earlier eval runs' own Q6 checks. No outcome changed (R4
  candidates are still the two marts), but each run makes the R2/R4 evidence noisier. This supports skill change #6 below.
- Python model `sales_metrics_by_location` reported at 0.00060 (write credits dropped), as in TC01 runs 1 and 3.

## Skill change needed (file + section)

7. SKILL.md Phase 0 step 4: make the frequency check explicit: "If the user's message states a frequency, show it in the
   checkpoint as `Run frequency: <value> (from your message)` and don't ask." Showing it gives the user a chance to
   correct it without being asked.
8. SKILL.md Phase 3 (R6): the proposed `where` must use `{{ source(...) }}` / `{{ ref(...) }}`, never a hard-coded
   relation name.

Plus #6 from TC01 run 2 (exclude the skill's own analysis queries in Q4), with more evidence here.

## Test-plan change needed

None.
