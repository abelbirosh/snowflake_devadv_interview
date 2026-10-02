---
name: dbt-cost-tuning
description: "Find the most expensive and slowest models in a dbt project running on Snowflake (dbt Projects on Snowflake / EXECUTE DBT PROJECT), and produce a scored impact-vs-effort tuning plan: incrementalization, clustering, spill fixes, unused models, view materialization, test cost. Read-only. Use when: a user wants to cut dbt cost or runtime, find expensive dbt models, or get a dbt tuning plan. Triggers: dbt cost, dbt credits, expensive dbt models, slow dbt models, dbt performance, optimize dbt, dbt tuning plan, which dbt models cost the most."
---

# dbt Cost & Performance Tuning

Turns a dbt project's run history into a ranked **tuning plan**: which models cost the most, why, what to change,
and the expected savings against the effort.

**Audience:** analytics engineers who know dbt and Snowflake. Be concise and show the evidence. Don't explain dbt basics.

**References. Load these before Phase 2:**
- `references/scoring-rubric.md`: credit sourcing, attribution, rule weights, thresholds and priority formula. **This is the source of truth for every number in the report.**
- `references/queries.md`: SQL templates Q0–Q5.

---

## Guardrails (apply in every phase)

1. **Read-only.** Run only `SELECT`, `SHOW`, `DESCRIBE`, `LIST`, and `GET` into a local temp directory.
   Never run `CREATE`, `ALTER`, `DROP`, `GRANT`, `EXECUTE DBT PROJECT`, `snow dbt execute`, `dbt run` or `dbt build`.
   Recommendations are proposed diffs for the user to apply. Never apply them.
2. **Discover, never assume.** Don't hard-code database, schema, project, warehouse or role names. Find them (Q0)
   and confirm with the user. If several dbt projects exist, ask which one.
3. **Keep the analysis cheap.** Every `ACCOUNT_USAGE` query must have a time bound. Use the user's current warehouse;
   if none is set, list warehouses and ask. Never resize or resume one yourself.
4. **Degrade gracefully.** A missing view or privilege disables specific rules. It doesn't stop the run. Say exactly
   which rule is affected and what would enable it (see "Failure handling").
5. **Be honest about numbers.** Every credit figure carries its source label (`attributed` / `estimated` / `mixed`).
   Savings are estimates from the rubric's savings factors. Never present them as guarantees.

---

## Phase 0: Environment discovery

**Goal:** know what's being analyzed and what data is available, before spending effort.

1. Run the **Q0** context query: role, warehouse, database, account.
2. Find the project: `SHOW DBT PROJECTS IN ACCOUNT`.
   - **None found:** say this skill currently supports dbt Projects on Snowflake (`EXECUTE DBT PROJECT`), and stop.
     Offer the general `cost-intelligence` skill for warehouse-level spend instead.
   - **One found:** propose it. **Several:** list them as `DB.SCHEMA.NAME` and ask.
3. Run the **Q0 capability probes** one at a time, and build this matrix:

   | Capability | Probe result | Enables |
   |---|---|---|
   | `ACCOUNT_USAGE.QUERY_HISTORY` | latest timestamp / error | Spill and pruning (R2, R3); history > 7 days |
   | `QUERY_ATTRIBUTION_HISTORY` | latest timestamp / NULL / error | `attributed` credits |
   | `ACCESS_HISTORY` | latest timestamp / error | R4 (unused models), R2 downstream reads |
   | History depth | earliest `QUERY_HISTORY` timestamp in the last 31 days | R4 false-positive guard (< 30 days) |

4. **Checkpoint (ask the user):** show the project, analysis window (default: last 5 successful runs), capability
   matrix, and which rules will be evaluated. Ask for the run frequency (e.g. "daily") to project monthly savings;
   if they skip it, report per run. Wait for confirmation before continuing.

## Phase 1: Run discovery and artifacts

1. **Q1:** list recent runs. Keep `SUCCESS` runs of `build`/`run`, up to the window size.
   - **No successful runs:** stop and say so. Show the latest failure's `ERROR_MESSAGE`. Don't analyze partial runs.
   - **Only one run:** continue, but say that recurrence-based evidence (R3 "spills in ≥ 2 runs") can't be scored.
2. **Q2:** locate and `GET` `manifest.json` and `run_results.json` for the **most recent** run into a local temp directory.
3. From the manifest, build the model inventory: materialization, `relation_name`, `unique_key`, `cluster_by`,
   compiled SQL, upstream/downstream dependencies (`child_map`), exposures and tests.
   - If artifacts can't be retrieved, continue with query-history metrics only. Materialization-based rules
     (R1, R5) then rely on parsing the query text, at reduced confidence. Say so.

## Phase 2: Attribute cost to models

1. Run **Q3** (ACCOUNT_USAGE path). If the most recent runs aren't in `ACCOUNT_USAGE` yet (~45 min delay),
   run **Q3b** for them and label those metrics `estimated`. Pruning and spill aren't available for those runs.
2. Apply the rubric's attribution rules (§2). In particular:
   - The parent `EXECUTE DBT PROJECT` query is excluded (Q3 does this). Report it separately only when its credits are `attributed`.
   - Link `unattributed:<table>` rows to Python models via `relation_name`, for **metrics only, not credits**.
   - Zero-scan executions: cost the model by its most recent execution that scanned data.
3. Produce the **cost table**: node, credits per run, share of total, source label, average seconds, rows written.
   Sum shares only over attributed nodes. Report the unattributed remainder as one line, and don't hide it.

## Phase 3: Evaluate rules

For each model (and each test, for R6), evaluate R1–R6 exactly as defined in `scoring-rubric.md` §4.
Compute Signal, Impact, Effort and Priority with §3. Gather evidence as follows:

| Rule | Evidence source |
|---|---|
| R1 Incremental | Manifest materialization; Q5 row count and bytes; compiled SQL (timestamp/ID columns, full-history aggregations); source `unique` tests |
| R2 Cluster key | Q4 reads with `avg_scan_ratio`; Q5 size; filter/join columns in those reads' SQL; `SYSTEM$CLUSTERING_INFORMATION` for the candidate column |
| R3 Spill | Q3 `max_spill_*`, `runs_with_spill` |
| R4 Unused | Q4 (no row means no outside reads); manifest `child_map`, exposures; history-depth guard |
| R5 Heavy view | Manifest views with ≥ 2 children; dependents' credit share; whether the view's SQL has joins or aggregations |
| R6 Test cost | Q3 rows for `test.*` nodes; size of the tested table; test type |

Rules:
- A rule is reported **only if it fires** (gates pass and Signal ≥ 60). Near misses (Signal 40–59) go in an "Observed, below threshold" list with the evidence that's missing.
- A rule whose data source is unavailable is reported as **not evaluable**, never as passed or failed.
- For R1, write the concrete change: `materialized='incremental'`, the proposed `unique_key`, the
  `is_incremental()` filter on the event column, and `incremental_strategy` (default `merge`; use `append` only if the source is append-only).
  Remind the user that the first deploy needs `--full-refresh`.

## Phase 4: Tuning plan

Output exactly this structure, in Markdown:

```
# dbt Tuning Plan: <DB>.<SCHEMA>.<PROJECT>
Window: <N> successful runs, <first> → <last> · Credit source: <attributed | estimated | mixed (N%)> — <one-line reason>

## Where the cost is
| Model | Credits/run | Share | Avg time | Source |
(top 5 + "all other nodes" + "unattributed")

## Recommendations (ranked by Priority)
| # | Model | Rule | Quadrant | Impact | Signal | Effort | Est. saving/run (and /month) |
For each: 2–4 lines of evidence → the proposed change (config/SQL diff) → how to verify it (query/metric to compare after).

## Observed, below threshold
## Not evaluable (and how to enable)
## Assumptions
(savings factors used, the estimated-credit concurrency caveat, history depth, dev vs prod target)
```

End by offering to (a) write the proposed changes as a local diff for review, or (b) re-run this analysis after
the changes are deployed, to measure actual savings. Do not apply anything without explicit confirmation.

---

## Failure handling

| Situation | What to tell the user | Continue? |
|---|---|---|
| No warehouse in session | List warehouses (`SHOW WAREHOUSES`) and ask which to use | After answer |
| `SHOW DBT PROJECTS` returns nothing | This skill analyzes dbt Projects on Snowflake. Suggest `cost-intelligence` for warehouse-level spend | Stop |
| Can't access `SNOWFLAKE.ACCOUNT_USAGE` | Ask an admin to grant `IMPORTED PRIVILEGES` on the `SNOWFLAKE` database, or the matching `SNOWFLAKE` database role. Falling back to `INFORMATION_SCHEMA` (7 days; no spill/pruning, so R2, R3, R4 not evaluable) | Yes, reduced |
| `QUERY_ATTRIBUTION_HISTORY` empty or behind | Credits are `estimated`; attributed data can take several hours to appear | Yes |
| `ACCESS_HISTORY` unavailable | R4 not evaluable (needs Enterprise edition + access to the view) | Yes |
| No successful runs | Show the last error; analysis needs at least one successful `build`/`run` | Stop |
| Artifacts unavailable | Rules reading the manifest drop to lower confidence | Yes, reduced |
| User asks to apply a fix | Out of scope for this skill. Offer a local diff, and let them deploy it themselves | n/a |
