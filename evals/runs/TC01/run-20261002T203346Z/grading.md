# Grading: TC01 run-20261002T203346Z

Tick manual checks with `[x]`, set the verdict line if you disagree with the computed one, then run `python3 evals/harness/eval.py publish`.

## Automatic checks (from the transcript and Snowflake query history)

- [x] skill_invoked (dbt-cost-tuning)
- [x] plan_header
- [x] section_where_cost
- [x] section_recommendations
- [x] section_below_threshold
- [x] section_not_evaluable
- [x] section_assumptions
- [x] credit_source_label
- [x] r1_incremental_orders
- [x] r1_is_incremental_filter
- [x] r1_unique_key
- [x] r1_full_refresh_reminder
- [x] r6_relationships_test
- [ ] r4_owner_review
- [x] offers_local_diff
- [x] no_drop_recommendation
- [x] read_only_snowflake (0 non-read statements)
- [x] account_usage_queries_time_bounded

## Manual checks

- [ ] Skill loaded (transcript shows dbt-cost-tuning / SKILL.md read)
- [ ] Phase 0 checkpoint shown and agent waited before Q1–Q5
- [ ] Window = 2 successful runs; says R3 recurrence evidence is limited / only 2 runs
- [ ] orders is ~80% of credits and ranked #1
- [ ] R1 on orders is recommendation #1 with a concrete config diff on order_ts
- [ ] R6 fires on the ORDER_DETAIL relationships test
- [ ] R3 listed under 'Observed, below threshold' (Signal ~50)
- [ ] R4 under 'Needs owner review' (30-day / dev guard), not recommended
- [ ] R2 and R5 do not fire
- [ ] Parent EXECUTE DBT PROJECT excluded from totals
- [ ] Credit source label has a one-line reason
- [ ] No hallucinated models, columns or numbers (spot-check 3 figures against baseline_runs.md)

## Verdict override (optional: Pass / Partial / Fail, with reason)

Verdict: 
