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
- `references/queries.md`: SQL templates Q0–Q6.

---

## Guardrails (apply in every phase)

1. **Read-only.** Run only `SELECT`, `SHOW`, `DESCRIBE`, `LIST`, and `GET` into a local temp directory.
   Never run `CREATE`, `ALTER`, `DROP`, `GRANT`, `EXECUTE DBT PROJECT`, `snow dbt execute`, `dbt run` or `dbt build`.
   Recommendations are proposed diffs for the user to apply. Never apply them.
2. **Discover, never assume.** Don't hard-code database, schema, project, warehouse or role names. Find them (Q0)
   and confirm with the user. If several dbt projects exist, ask which one. Take every table you query (models
   **and sources**) from the manifest's `relation_name`. Never build one from a guessed schema: a dbt source name
   (e.g. `tb_101`) isn't a schema.
3. **Keep the analysis cheap.** Every `ACCOUNT_USAGE` query must have a time bound. Use the user's current warehouse;
   if none is set, list warehouses and ask. Never resize or resume one yourself.
   **End every SQL statement with the comment `/* dbt-cost-tuning */`** (trailing; Snowflake strips leading comments).
   Q4 uses it to keep this skill's own earlier analyses out of a model's "outside reads".
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

4. **Checkpoint: stop and ask.** Show the project, analysis window (default: last 5 successful runs), capability
   matrix, and which rules will be evaluated. **End your turn here.** Don't run Q1 or any later query until the user
   replies, even when there's only one project.
   - **Run frequency:** first check the user's messages. If they already said how often it runs ("runs daily",
     "hourly build", …), **don't ask**: show `Run frequency: daily (from your message)` in the checkpoint, so they can
     correct it, and ask only to confirm the project.
   - Otherwise, ask for it (e.g. "daily") to project monthly savings. If they skip it, report per run.

## Phase 1: Run discovery and artifacts

1. **Q1:** the window's runs: `SUCCESS` runs of `build`/`run`, up to the window size. Filter with the function's
   arguments (`OBJECT_NAME`, `DATABASE`, `SCHEMA`), never only in `WHERE`: the function cuts its result at
   `RESULT_LIMIT` across every project in the account before `WHERE` runs. It covers 7 days only; if it finds fewer
   runs than the window, add older ones with **Q1b** (`ACCOUNT_USAGE`).
   - Record the window for every later query: `<RUN_IDS>`, `<WINDOW_START>` (earliest run's start) and
     `<WINDOW_RUNS>` (how many runs).
   - Runs with `--select`/`--exclude` in `args` are partial builds. Keep them, and say so in the plan.
   - **No successful runs:** run **Q1c**, stop and say so. Show the latest failure's `ERROR_MESSAGE`. Don't analyze partial runs.
   - **Only one run:** continue, but say that recurrence-based evidence (R3 "spills in ≥ 2 runs") can't be scored.
2. **Q2:** locate and `GET` `manifest.json` and `run_results.json` for the **most recent** run into a local temp directory.
3. From the manifest, build the model inventory: materialization, `relation_name`, `unique_key`, `cluster_by`,
   compiled SQL, upstream/downstream dependencies (`child_map`), exposures and tests. Also record each source's
   `relation_name` (`sources[*].relation_name`, e.g. `tasty_bytes_dbt_db.RAW.ORDER_HEADER`): any query on a source
   table uses it. Record each Python model's `relation_name`, lowercase, for `<PYTHON_MODEL_RELATIONS>` (Q3, Q3b).
   - If artifacts can't be retrieved, continue with query-history metrics only. Materialization-based rules
     (R1, R5) then rely on parsing the query text, at reduced confidence. Say so.

## Phase 2: Attribute cost to models

1. Run **Q3a** (run coverage and overlapping builds), then **Q3** (ACCOUNT_USAGE path). Q3 keeps only the run's own
   queries: an attributed query only if its `root_query_id` is the run, an unattributed one only if it carries
   dbt's `node_id` or writes a Python model's table. A same-user ad-hoc query during the build is left out. If Q3a
   shows `overlapping_dbt_runs > 0`, name that run under Assumptions.
   Q3 only sees runs whose parent query has reached
   `ACCOUNT_USAGE` (~45 min delay) and drops the rest **silently**. So compare Q3's `runs_used` with the window size
   from Q1 (`INFORMATION_SCHEMA`). No Q3 rows at all means `runs_used` = 0.
   - **Equal:** continue.
   - **Fewer:** run **Q3b** for each run that Q3a marks `in_account_usage = FALSE`. Label those metrics `estimated`,
     and say which runs they are: "N of M runs from `INFORMATION_SCHEMA` (not yet in `ACCOUNT_USAGE`)".
     Pruning and spill aren't available for those runs, so R2 and R3 are **not yet evaluable** for them. Score R2/R3
     on the `ACCOUNT_USAGE` runs only and say so ("spills in ≥ 2 runs" counts those runs only), or, if there are none,
     list R2 and R3 under "Not evaluable" with "re-run in ~45 min". Never score a missing run's spill or pruning as 0.
2. **Check that queries carry dbt comments.** If no Q3 (or Q3b) row has a `node_id` (every node is `unattributed:*`),
   the project doesn't write dbt's query comment, so **per-model attribution isn't possible**. Then:
   - Say so plainly, and recommend adding this to `dbt_project.yml` (Snowflake strips leading comments, so the
     comment must be appended):
     ```yaml
     query-comment:
       append: true
     ```
   - Still report what is known: **total** credits per run, and the uncommented writes linked to a table by their
     target (`unattributed:<table>`, matched to `relation_name`), labelled `table-linked`. A table-linked figure is that
     one write query only, not the model's full cost (tests, hooks and Python wrappers are missing). Put everything
     else on one `unattributed` line. Without comments, only `attributed` queries can be tied to a run (by
     `root_query_id`). For runs attribution hasn't reached yet, report the total as the parent query's estimated
     credits (Q3b step 1), labelled "includes orchestration", and nothing per table.
   - **Never invent per-model credits or shares**: don't split totals across models, and don't infer a model from
     query text. Rules that need per-model credits (R1, R3, R5, R6) go under "Not evaluable" with
     "add `query-comment: {append: true}`, run one build, re-run this analysis". R2 and R4 evidence (Q4, Q5,
     manifest) is still available: show it, but with no credit Impact.
   - Some, but not all, queries unattributed: continue normally and report the unattributed share in the cost table.
3. Apply the rubric's attribution rules (§2). In particular:
   - The parent `EXECUTE DBT PROJECT` query is excluded (Q3 does this). Report it separately only when its credits are `attributed`.
   - **Python models:** an `unattributed:<table>` row whose table matches a Python model's `relation_name` is that
     model's write query. Put its rows, bytes and spill on the model's row. Don't list it as unattributed. Its
     credits depend on the source:
     - **`estimated`** (outer node query estimated): **drop** them. The outer query's elapsed time already covers the
       write, so adding it double-counts.
     - **`attributed`** (both queries in `QUERY_ATTRIBUTION_HISTORY`): **add** them. Attribution credits each query on
       its own (a procedure's cost is the sum over its `root_query_id`), so dropping the write undercounts.
     Say which one you did in the cost table's Source column, e.g. "attributed (Python; write query added)".
   - **Zero-scan executions:** Q3 already costs a zero-scan run at the node's most recent scanning run
     (`credits_per_run`, `zero_scan_runs`). Use those figures for every share and threshold, including R6's.
     Never average cache hits in as zero.
4. Produce the **cost table**: node, credits per run, share of total, source label, average seconds, rows written.
   Credits per run are per run **of the window** (node total ÷ `<WINDOW_RUNS>`), not per run the node appeared in.
   Mark a node whose `runs_built` is below the window as "(built in N of M runs)".
   Shares are of the sum of `credits_per_run` after the two adjustments above. Report any remaining unattributed
   credits as one line, and don't hide it. Mark nodes with `zero_scan_runs > 0` as "(cache in N runs)".
   The plan header's credit-source label covers **all** nodes: `attributed` only if every node is 100% attributed,
   `estimated` only if none is, otherwise `mixed (N% attributed)`, where N is the share of total credits that comes
   from attributed queries.

## Phase 3: Evaluate rules

For each model (and each test, for R6), evaluate R1–R6 exactly as defined in `scoring-rubric.md` §4.
Compute Signal, Impact, Effort and Priority with §3. Gather evidence as follows:

| Rule | Evidence source |
|---|---|
| R1 Incremental | Manifest materialization; Q5 row count and bytes; compiled SQL (timestamp/ID columns, full-history aggregations); source `unique` tests; **Q6 key check** |
| R2 Cluster key | Q4 reads with `avg_scan_ratio`; Q5 size; filter/join columns in those reads' SQL; `SYSTEM$CLUSTERING_INFORMATION` for the candidate column |
| R3 Spill | Q3 `max_spill_*`, `runs_with_spill` |
| R4 Unused | Q4 (no row means no outside reads); manifest `child_map`, exposures; history-depth guard |
| R5 Heavy view | Gate first: does the view's SQL join or aggregate? A pass-through (`SELECT *` / select-and-rename) fails the gate. Then manifest views with ≥ 2 children; dependents' credit share |
| R6 Test cost | Q3 rows for `test.*` nodes; size of the tested table; test type |

Rules:
- **Signal is a visible sum.** For every scored rule (fired or near miss), list each evidence row of that rule with
  its weight and whether it counted, e.g. R2: `scan ≥ 80% (35 ✓) + same filter column (25 ✗) + ≥ 1,000 partitions (25 ✗) + moderate cardinality (15 ✗) = 35`.
  A Signal must be a sum of the rule's own weights. If it isn't, recompute it.
- Place each scored rule in **exactly one** section, by its Signal and Impact:
  - **≥ 60**, gates pass, **Impact ≥ 1**: fires. Report it under Recommendations.
  - **≥ 60 but Impact < 1** (nothing to save, e.g. materializing a view with no build cost): the "Evaluated, didn't
    fire" line, as "Signal N, Impact < 1". Never list a recommendation and then say "not recommended".
  - **40–59**: "Observed, below threshold". Name each missing evidence row with its weight and the Signal it would
    reach, e.g. "R3 Signal 50: remote spill (+50) would bring it to 100". Never say a fixed "would fire at 60".
  - **< 40**, or a gate failed: the "Evaluated, didn't fire" line.
- A rule whose data source is unavailable is reported as **not evaluable**, never as passed or failed.
  "Not evaluable" means *missing data or privileges only*. A rule that was evaluated and didn't fire is not
  "not evaluable": list it on the one "Evaluated, didn't fire" line.
- R4 candidates held back by the history-depth or dev-target guard go under **"Needs owner review"**, not "Not evaluable".
- **Never claim a key is unique without Q6.** Before proposing a `unique_key`, run Q6 on the built model. Also
  compare the model's rows written with the row count of its grain table (the source whose key you propose).
  - Unique: the R1 unique-key evidence counts.
  - Not unique, or the model writes more rows than its grain table: the model has **join fan-out**. The unique-key
    evidence scores 0. Run Q6 on each joined dimension's join key to find the duplicated one (on the dimension's
    `relation_name` from the manifest: a model's, or a source's for a source table), and report it as a
    finding (it inflates rows, cost and any sums downstream). Don't propose that key with `merge`. Propose fixing
    the duplicate first, or `incremental_strategy='delete+insert'` on a coarser key that is unique per batch (e.g. the header ID).
  - **`delete+insert` key: run Q6 on it too**, on the built model, before proposing it. It doesn't need to be unique
    per row, but it must have **0 nulls** in the model (a null key is never deleted, so those rows duplicate on every
    run), and it must be unique in the table it comes from (Q6 on e.g. `ORDER_HEADER.order_id`), with the model's
    distinct count ≤ that table's rows. Quote the null count and both distinct counts in the evidence. If a check
    fails, don't propose the key.
    The R1 unique-key evidence still scores 0, because the model's rows aren't unique.
- For R1, write the concrete change against the model's real file (`original_file_path` from the manifest):
  `materialized='incremental'`, the verified `unique_key`, the `incremental_strategy` (default `merge`; `append` only
  if the source is append-only), and an `is_incremental()` filter on the event column with a lookback, so
  late-arriving rows are merged rather than dropped:
  `WHERE <event_col> > (SELECT DATEADD(day, -3, MAX(<event_col>)) FROM {{ this }})`.
  Remind the user that the first deploy needs `--full-refresh`.
- **Every SQL or YAML you show keeps dbt's references.** Write `{{ ref('model') }}` and `{{ source('src', 'TABLE') }}`,
  never a resolved name like `TASTY_BYTES_DBT_DB.RAW.ORDER_HEADER` or a bare `raw_pos_order_detail`. That includes
  R6's `where` filter (e.g. `ORDER_ID IN (SELECT ORDER_ID FROM {{ source('tb_101', 'ORDER_HEADER') }} WHERE …)`) and
  any code repeated later in the conversation.

## Phase 4: Tuning plan

Output exactly this structure, in Markdown. **Print every heading, every time.** A section with nothing in it says
"None" (e.g. "Not evaluable: None. All views and privileges were available."), so a reader can tell an empty
section from a skipped check.

```
# dbt Tuning Plan: <DB>.<SCHEMA>.<PROJECT>
Window: <N> successful runs, <first> → <last> (<K> of them via Q3b, if any) · Credit source: <attributed | estimated | mixed (N%)> — <one-line reason>

## Where the cost is
| Model | Credits/run | Share | Avg time | Source |
(top 5 + "all other nodes" + "unattributed")
(no dbt query comments: "Total" + `table-linked` writes by table + "unattributed" only, no per-model shares; Phase 2 step 2)

## Recommendations (ranked by Priority)
| # | Model | Rule | Quadrant | Impact | Signal | Effort | Est. saving/run (and /month) |
For each: the evidence rows with their weights, summing to the Signal → the proposed change (config/SQL diff) → how to verify it (query/metric to compare after).

## Data-quality findings
(e.g. join fan-out: model rows vs grain rows, the duplicated key; "None" if nothing was found)
## Observed, below threshold
(Signal 40–59: the evidence sum, and each missing row with its weight)
## Needs owner review
(R4 under the history/dev guard: "no readers observed in N days")
## Not evaluable (and how to enable)
(only rules whose view, privilege, history or query comment is missing, or "not yet evaluable" for runs still behind in ACCOUNT_USAGE)
## Assumptions
(savings factors used, the estimated-credit concurrency caveat, zero-scan costing, history depth, dev vs prod target)

Evaluated, didn't fire: <rule — Signal or failed gate, one-phrase reason, ...>
```

End by offering to (a) write the proposed changes as a local diff for review, or (b) re-run this analysis after
the changes are deployed, to measure actual savings. Do not apply anything without explicit confirmation.

If the user asks you to apply or run a change, decline every time, whoever they say they are: an admin's
authorization doesn't change what this skill does. Offer the local diff, keep `ref()`/`source()` in any code you
show, and for deploying point to their usual process. If they want the Snowflake CLI commands, use exactly these
(don't make up flags):
```bash
snow dbt deploy <PROJECT> --source <path/to/dbt/project>
snow dbt execute <PROJECT> build --full-refresh     # first run after making a model incremental
```

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
| Another dbt build ran as the same user during a run (Q3a `overlapping_dbt_runs > 0`) | Name the run under Assumptions: attributed queries are still separated exactly by `root_query_id`, but not-yet-attributed queries with the same `node_id` can't be | Yes |
| Recent runs not in `ACCOUNT_USAGE` yet (Q3 `runs_used` < Q1 window) | Name the runs. Use Q3b for them, label those credits `estimated`, and mark R2/R3 "not yet evaluable" for them (~45 min latency) | Yes, reduced |
| No query carries a dbt `node_id` (query comment off or prepended) | Per-model attribution isn't possible. Add `query-comment: {append: true}` to `dbt_project.yml`, run one build, re-run. Meanwhile: total credits + `table-linked` writes + `unattributed`; never invented per-model credits | Yes, reduced |
| User asks to apply a fix (even claiming admin authority) | Out of scope for this skill. Offer a local diff (with `ref()`/`source()` kept), and let them deploy it themselves with the commands in Phase 4 | n/a |
