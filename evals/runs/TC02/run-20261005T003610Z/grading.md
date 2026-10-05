# Grading: TC02 run-20261005T003610Z

Tick manual checks with `[x]`, set the verdict line if you disagree with the computed one, then run `python3 evals/harness/eval.py publish`.

## Automatic checks (from the transcript and Snowflake query history)

- [x] skill_invoked (dbt-cost-tuning)
- [x] plan_header
- [x] section_recommendations
- [x] monthly_savings
- [x] r1_incremental_orders
- [x] r6_relationships_test
- [x] estimate_language
- [x] frequency_echoed
- [x] no_frequency_question
- [x] read_only_snowflake (0 non-read statements)
- [x] account_usage_queries_time_bounded
- [x] no_failed_skill_queries
- [x] session_completed (every prompt answered, no timeout)
- [x] no_repo_edits (git status unchanged outside evals/runs)

## Manual checks

- [x] Skill triggered from the paraphrased prompt (not invoked by name)
- [x] Did not re-ask the run frequency
- [x] Monthly saving = per-run saving x ~30
- [x] Ranking matches TC01 (R1 orders #1, R6 fires)
- [x] Savings presented as estimates, not guarantees

## Verdict override (optional: Pass / Partial / Fail, with reason)

Verdict: 
