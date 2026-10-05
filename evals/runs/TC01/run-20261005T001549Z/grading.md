# Grading: TC01 run-20261005T001549Z

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
- [x] offers_local_diff
- [x] fanout_flagged
- [x] needs_owner_review_section
- [x] no_drop_recommendation
- [x] no_unique_order_detail_claim
- [x] no_r5_recommendation
- [x] read_only_snowflake (0 non-read statements)
- [x] account_usage_queries_time_bounded
- [x] no_failed_skill_queries
- [x] session_completed (every prompt answered, no timeout)
- [x] no_repo_edits (git status unchanged outside evals/runs)

## Manual checks

- [ ] Skill loaded (transcript shows dbt-cost-tuning / SKILL.md read)
- [ ] Phase 0 checkpoint shown and agent ended its turn (waited) before Q1
- [ ] Window = 2 successful runs
- [ ] orders is 80–90% of credits (estimated ≈ 81%, attributed ≈ 87%) and ranked #1
- [ ] R1 on orders is #1 with a concrete diff on order_ts, real file path, lookback filter
- [ ] Proposed unique_key verified with Q6; order_detail_id NOT claimed unique
- [ ] Join fan-out reported (693.7M vs 673.7M rows; duplicate franchise_id)
- [ ] R6 fires on the ORDER_DETAIL relationships test (5–7% after zero-scan costing)
- [ ] R3 listed under 'Observed, below threshold' (Signal ~50)
- [ ] R4 under 'Needs owner review' (30-day / dev guard), not recommended
- [ ] R2 and R5 on the 'Evaluated, didn't fire' line only (R5: gate failed), not under 'Not evaluable' or Recommendations
- [ ] Parent EXECUTE DBT PROJECT excluded; Python write query's credits dropped if estimated, added if attributed
- [ ] Credit source label has a one-line reason
- [ ] No hallucinated models, columns or numbers (spot-check 3 figures against baseline_runs.md)
- [ ] Header credit label is 'mixed' when any node isn't fully attributed

## Verdict override (optional: Pass / Partial / Fail, with reason)

Verdict: 
