# Findings: TC05 run-20261005T011128Z

## Expected vs observed
- **Failed: "No spill numbers reported".** Under "Observed, below threshold" the plan says: R3 on `orders` Signal 50
  "from the rubric's verified Tasty Bytes data: 12.45 GB local spill in both runs". The role can't read
  `ACCOUNT_USAGE`, so spill isn't measurable; the number comes from the worked example in
  `references/scoring-rubric.md` (R3 section). R3 is also listed under "Not evaluable", so it's in two sections.
- Passed: continued on `INFORMATION_SCHEMA`; R2/R3/R4 not evaluable, each with the `IMPORTED PRIVILEGES` grant; R1
  fires on `orders` (#1, 0.09439 estimated = 81.3%, matching the hand-verified baseline exactly).
- The 6 failed skill queries are expected for this role: 4 `ACCOUNT_USAGE` capability probes and 2 Q6 checks on
  `RAW` tables (no `USAGE` on schema `RAW`); the skill retried Q6 through the staging views and still reported the
  fan-out.

## Surprises
The skill quoted reference data from its own rubric as account evidence, and said so in the text.

## Skill change needed (file + section)
S17 in `fix_list.md`: scoring-rubric.md worked examples must not carry this demo account's numbers (or must be
marked "illustration only, never report"); SKILL.md Phase 3: a rule whose data source is unavailable goes only under
"Not evaluable", with no Signal and no figures.

## Test-plan change needed
None.
