#!/usr/bin/env python3
"""Eval harness for the dbt-cost-tuning Cortex Code skill.

Wraps one interactive Cortex Code session per eval run and saves, then commits and pushes:
  telemetry  - Cortex session stats + every Snowflake query the session ran (INFORMATION_SCHEMA, no lag)
  grading    - automatic checks (regexes from cases.json, read-only check) + manual checklist
  findings   - free-text notes per run

Usage (from the repo root):
  python3 evals/harness/eval.py start TC01      # before launching cortex
  python3 evals/harness/eval.py finish          # after /exit: export, measure, auto-grade, commit, push
  python3 evals/harness/eval.py publish         # after ticking manual checks + writing findings: commit, push
  python3 evals/harness/eval.py summary         # rebuild evals/results.md from all runs

Everything written to the repo is redacted: account locators, hosts, user names and emails.
"""
import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                      cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
EVALS = os.path.join(ROOT, "evals")
RUNS = os.path.join(EVALS, "runs")
CURRENT = os.path.join(RUNS, ".current")
CASES = json.load(open(os.path.join(EVALS, "harness", "cases.json")))["cases"]
SKILL_DIR = os.path.join(ROOT, ".cortex", "skills", "dbt-cost-tuning")
CONV_DIR = os.path.expanduser("~/.snowflake/cortex/conversations")
CONN_FILE = os.path.expanduser("~/.snowflake/connections.toml")

ADMIN_CONN = os.environ.get("EVAL_ADMIN_CONN", "AG26866")   # used for telemetry queries, never committed
TELEMETRY_DB = os.environ.get("EVAL_TELEMETRY_DB", "TASTY_BYTES_DBT_DB")
READ_ONLY_TYPES = {"SELECT", "SHOW", "DESCRIBE", "DESCRIBE_QUERY", "EXPLAIN", "GET_FILES", "LIST_FILES",
                   "USE", "ALTER_SESSION", "UNKNOWN"}  # UNKNOWN = statement failed to compile
CREDITS_PER_HOUR = {"xsmall": 1, "small": 2, "medium": 4, "large": 8, "xlarge": 16, "2xlarge": 32,
                    "3xlarge": 64, "4xlarge": 128, "5xlarge": 256, "6xlarge": 512}


# ---------------------------------------------------------------------------- helpers
def now():
    return dt.datetime.now(dt.timezone.utc)


def sh(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if check and r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr or r.stdout}")
    return r.stdout


def secrets():
    """Strings that must never reach the repo."""
    vals = set()
    try:
        conns = tomllib.load(open(CONN_FILE, "rb"))
        for name, c in conns.items():
            vals.add(name)
            for k in ("account", "user", "host", "email"):
                if isinstance(c.get(k), str) and len(c[k]) > 2:
                    vals.add(c[k])
    except FileNotFoundError:
        pass
    return sorted(vals, key=len, reverse=True)


SECRETS = secrets()  # extended per session by add_session_secrets()


def redact(text):
    if not isinstance(text, str):
        return text
    for s in SECRETS:
        text = re.sub(re.escape(s), "<REDACTED>", text, flags=re.I)
    text = re.sub(r"[A-Za-z0-9_.-]+\.snowflakecomputing\.com", "<REDACTED_HOST>", text)
    text = re.sub(r"(Account(?:\*\*)?\s*:\s*(?:\*\*)?\s*)([A-Za-z0-9][A-Za-z0-9_.-]{4,})", r"\1<REDACTED>", text, flags=re.I)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<REDACTED_EMAIL>", text)
    return text


def add_session_secrets(session):
    """Account locators and hosts Cortex resolved at runtime (not always in connections.toml)."""
    extra = list((session or {}).get("session_accounts") or [])
    host = ((session or {}).get("thread") or {}).get("accountHost")
    if host:
        extra += [host, host.split(".")[0]]
    for v in extra:
        if isinstance(v, str) and len(v) > 2 and v not in SECRETS:
            SECRETS.append(v)
    SECRETS.sort(key=len, reverse=True)


def public_transcript(lines):
    """Drop Cortex's internal system reminders (they include the user's private instructions)."""
    out = []
    for m in lines:
        content = [c for c in m.get("content", []) if not c.get("internalOnly")]
        if content:
            out.append({**m, "content": content})
    return out


def redact_obj(o):
    if isinstance(o, dict):
        return {k: redact_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [redact_obj(v) for v in o]
    return redact(o)


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content if isinstance(content, str) else json.dumps(content, indent=2, default=str))


def snow_json(query):
    out = sh(["snow", "sql", "-c", ADMIN_CONN, "--format", "json", "-q", query])
    data = json.loads(out)
    # multi-statement output is a list per statement; single statement is a list of rows
    return data[-1] if data and isinstance(data[0], list) else data


def skill_sha():
    h = hashlib.sha256()
    for p in sorted(glob.glob(os.path.join(SKILL_DIR, "**", "*.md"), recursive=True)):
        h.update(open(p, "rb").read())
    return h.hexdigest()[:12]


def current_run():
    if not os.path.exists(CURRENT):
        sys.exit("no run in progress: run `eval.py start <CASE>` first")
    run_dir = open(CURRENT).read().strip()
    return run_dir, json.load(open(os.path.join(run_dir, "meta.json")))


def git_publish(paths, message):
    sh(["git", "add", "--"] + paths)
    if not sh(["git", "diff", "--cached", "--name-only"]).strip():
        print("nothing new to commit")
    else:
        sh(["git", "commit", "-m", message])
    r = subprocess.run(["git", "push", "-u", "origin", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        print(f"!! PUSH FAILED. Fix and run `git push -u origin HEAD`:\n{r.stderr}")
    else:
        print("pushed:", sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())


# ---------------------------------------------------------------------------- start
def cmd_start(args):
    case = args.case.upper()
    if case not in CASES:
        sys.exit(f"unknown case {case}; known: {', '.join(CASES)}")
    if os.path.exists(CURRENT) and not args.force:
        sys.exit(f"a run is already in progress ({open(CURRENT).read().strip()}). finish it or pass --force")
    started = now()
    run_dir = os.path.join(RUNS, case, "run-" + started.strftime("%Y%m%dT%H%M%SZ"))
    c = CASES[case]
    meta = {
        "case": case, "type": c["type"], "run_id": os.path.basename(run_dir),
        "started_at": started.isoformat(), "connection": redact(c["connection"]),
        "cortex_version": sh(["cortex", "--version"], check=False).strip(),
        "git_sha": sh(["git", "rev-parse", "--short", "HEAD"]).strip(),
        "skill_sha": skill_sha(),
        "prompts": c["prompts"],
    }
    write(os.path.join(run_dir, "meta.json"), meta)
    os.makedirs(os.path.join(run_dir, "screenshots"), exist_ok=True)
    write(CURRENT, run_dir)
    print(f"Run started: {os.path.relpath(run_dir, ROOT)}  ({started:%H:%M:%S} UTC)\n")
    print(f"Launch:\n  cortex -c {c['connection']}\n")
    print("Type these, in order (answer any other question the skill asks and note it in findings.md):")
    for i, p in enumerate(c["prompts"], 1):
        print(f"  {i}. {p}")
    print(f"\nSave screenshots to {os.path.relpath(run_dir, ROOT)}/screenshots/  then /exit and run:\n"
          f"  python3 evals/harness/eval.py finish")


# ---------------------------------------------------------------------------- finish
def find_session(started_at):
    """The first session created after the run started. Later ones are sub-agents the main session spawned."""
    best = None
    for p in glob.glob(os.path.join(CONV_DIR, "*.json")):
        try:
            m = json.load(open(p))
        except Exception:
            continue
        created = dt.datetime.fromisoformat(m.get("created_at", "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
        if created >= started_at - dt.timedelta(minutes=1) and (best is None or created < best[0]):
            best = (created, m)
    return best[1] if best else None


def agent_text(pretty):
    """Only the agent's blocks from the pretty transcript, so prompts don't satisfy checks."""
    out, inside = [], False
    for line in pretty.splitlines():
        if line.startswith("┌─ AGENT"):
            inside = True
            continue
        if line.startswith("┌─ USER"):
            inside = False
        if line.startswith("└"):
            inside = False
            continue
        if inside:
            out.append(line[2:] if line.startswith("│ ") else line)
    return "\n".join(out) or pretty


def count_keys(o, pred, acc=None):
    acc = acc if acc is not None else {}
    if isinstance(o, dict):
        for k, v in o.items():
            if pred(k, v):
                acc[k] = acc.get(k, 0) + (v if isinstance(v, (int, float)) and not isinstance(v, bool) else 1)
            count_keys(v, pred, acc)
    elif isinstance(o, list):
        for v in o:
            count_keys(v, pred, acc)
    return acc


def cortex_telemetry(session, transcript_json):
    t = {"session_found": session is not None}
    if not session:
        return t
    created = dt.datetime.fromisoformat(session["created_at"].replace("Z", "+00:00"))
    updated = dt.datetime.fromisoformat(session["last_updated"].replace("Z", "+00:00"))
    t.update({
        "wall_seconds": round((updated - created).total_seconds()),
        "history_length": session.get("history_length"),
        "agent_mode": session.get("agent_mode"), "sql_mode": session.get("sql_mode"),
    })
    hist = os.path.join(CONV_DIR, session["session_id"] + ".history.jsonl")
    if os.path.exists(hist):
        msgs = [json.loads(l) for l in open(hist)]
        typed = sum(1 for m in msgs if m.get("role") == "user"
                    and any(c.get("is_user_prompt") for c in m.get("content", [])))
        # Answers given through Cortex's question picker arrive as tool results, not typed prompts.
        picked = sum(1 for m in msgs for c in m.get("content", []) if c.get("type") == "tool_result"
                     and (c.get("tool_result") or {}).get("name") == "ask_user_question")
        t["user_turns"], t["picker_answers"] = typed + picked, picked
        t["assistant_messages"] = sum(1 for m in msgs if m.get("role") == "assistant")
        tools = [c["tool_use"] for m in msgs for c in m.get("content", []) if c.get("type") == "tool_use"]
        t["tool_calls"] = len(tools)
        t["tool_calls_by_name"] = {}
        for tu in tools:
            t["tool_calls_by_name"][tu.get("name")] = t["tool_calls_by_name"].get(tu.get("name"), 0) + 1
        t["skills_invoked"] = [tu.get("input", {}).get("command") for tu in tools if tu.get("name") == "skill"]
        t["bash_commands"] = [str(tu.get("input", {}).get("command", ""))[:300] for tu in tools if tu.get("name") == "bash"]
        t["thinking_blocks"] = sum(1 for m in msgs for c in m.get("content", []) if c.get("type") == "thinking")
    # Token usage isn't in the documented shape, so sum any token-like counters present.
    t["token_usage"] = count_keys(transcript_json, lambda k, v: "token" in k.lower()
                                  and isinstance(v, (int, float))) if transcript_json else {}
    return t


def is_cortex_internal(failure):
    """Cortex Code's own lookups of its CORTEX_CODE database fail on every run; they aren't the skill's queries."""
    return "'CORTEX_CODE'" in (failure.get("error_message") or "")


def count_failures(summary, failed):
    """Split failed queries into the skill's own and Cortex Code's internal ones (also fixes older telemetry)."""
    internal = sum(map(is_cortex_internal, failed))
    summary["failed_queries"], summary["cortex_internal_failed"] = len(failed) - internal, internal
    return summary


def snowflake_telemetry(start, end):
    rows = snow_json(f"""
        SELECT query_id, start_time, role_name, warehouse_name, warehouse_size, query_type, execution_status,
               error_code, LEFT(error_message, 300) AS error_message, total_elapsed_time, execution_time,
               bytes_scanned, LEFT(query_text, 4000) AS query_text
        FROM TABLE({TELEMETRY_DB}.INFORMATION_SCHEMA.QUERY_HISTORY(
               END_TIME_RANGE_START => '{start.isoformat()}'::TIMESTAMP_LTZ,
               END_TIME_RANGE_END   => '{end.isoformat()}'::TIMESTAMP_LTZ,
               RESULT_LIMIT => 10000))
        WHERE start_time >= '{start.isoformat()}'::TIMESTAMP_LTZ AND user_name = CURRENT_USER()
        ORDER BY start_time""")
    by_type, credits, elapsed, scanned = {}, 0.0, 0, 0
    violations, failed = [], []
    for r in rows:
        qt = r.get("QUERY_TYPE") or "UNKNOWN"
        by_type[qt] = by_type.get(qt, 0) + 1
        size = re.sub(r"[^a-z0-9]", "", (r.get("WAREHOUSE_SIZE") or "").lower())
        credits += float(r.get("EXECUTION_TIME") or 0) / 3_600_000 * CREDITS_PER_HOUR.get(size, 0)
        elapsed += int(r.get("TOTAL_ELAPSED_TIME") or 0)
        scanned += int(r.get("BYTES_SCANNED") or 0)
        if qt not in READ_ONLY_TYPES:
            violations.append({"query_id": r["QUERY_ID"], "query_type": qt, "query_text": r["QUERY_TEXT"][:300]})
        if r.get("EXECUTION_STATUS") not in ("SUCCESS", "RUNNING"):
            failed.append({"query_type": qt, "error_code": r.get("ERROR_CODE"), "error_message": r.get("ERROR_MESSAGE")})
    au = [r for r in rows if "ACCOUNT_USAGE" in (r.get("QUERY_TEXT") or "").upper()]
    unbounded = [r["QUERY_TEXT"][:300] for r in au
                 if not re.search(r"start_time\s*(>=|>|between)|dateadd|query_start_time\s*>=", r["QUERY_TEXT"], re.I)]
    summary = {
        "queries": len(rows), "by_type": by_type, "failed_queries": len(failed),
        "est_credits_analysis": round(credits, 5), "total_elapsed_seconds": round(elapsed / 1000, 1),
        "bytes_scanned_gb": round(scanned / 1e9, 3),
        "read_only_violations": len(violations), "account_usage_queries": len(au),
        "account_usage_unbounded": len(unbounded),
    }
    return count_failures(summary, failed), rows, violations, failed, unbounded


def auto_grade(case, text, sf, cx=None):
    c = CASES[case]
    checks = [{"check": "skill_invoked (dbt-cost-tuning)",
               "pass": "dbt-cost-tuning" in ((cx or {}).get("skills_invoked") or [])}]
    for name, rx in c.get("auto", {}).items():
        checks.append({"check": name, "pass": bool(re.search(rx, text, re.I | re.M))})
    for name, rx in c.get("absent", {}).items():
        checks.append({"check": name, "pass": not re.search(rx, text, re.I | re.M)})
    checks.append({"check": "read_only_snowflake (0 non-read statements)", "pass": sf["read_only_violations"] == 0})
    checks.append({"check": "account_usage_queries_time_bounded", "pass": sf["account_usage_unbounded"] == 0})
    if c.get("no_failed_queries"):
        checks.append({"check": "no_failed_skill_queries", "pass": sf["failed_queries"] == 0})
    return checks


def grading_md(meta, auto, manual_done=None):
    manual_done = manual_done or {}
    lines = [f"# Grading: {meta['case']} {meta['run_id']}", "",
             "Tick manual checks with `[x]`, set the verdict line if you disagree with the computed one, "
             "then run `python3 evals/harness/eval.py publish`.", "",
             "## Automatic checks (from the transcript and Snowflake query history)", ""]
    lines += [f"- [{'x' if a['pass'] else ' '}] {a['check']}" for a in auto]
    lines += ["", "## Manual checks", ""]
    lines += [f"- [{'x' if manual_done.get(m) else ' '}] {m}" for m in CASES[meta["case"]]["manual"]]
    lines += ["", "## Verdict override (optional: Pass / Partial / Fail, with reason)", "", "Verdict: ", ""]
    return "\n".join(lines)


FINDINGS_TEMPLATE = """# Findings: {case} {run_id}

## Expected vs observed
<!-- One line per failed or surprising check: expected X, observed Y, evidence (screenshot / transcript line). -->

## Surprises

## Skill change needed (file + section)

## Test-plan change needed
"""


def cmd_finish(args):
    if args.rerun:  # re-process a finished run (e.g. after a harness fix); keeps its original time window
        run_dir = os.path.join(ROOT, args.rerun)
        meta = json.load(open(os.path.join(run_dir, "meta.json")))
        ended = dt.datetime.fromisoformat(json.load(open(os.path.join(run_dir, "telemetry.json")))["ended_at"])
    else:
        run_dir, meta = current_run()
        ended = now()
    started = dt.datetime.fromisoformat(meta["started_at"])
    rel = os.path.relpath(run_dir, ROOT)

    session = None
    if args.session:
        p = os.path.join(CONV_DIR, args.session + ".json")
        session = json.load(open(p)) if os.path.exists(p) else {"session_id": args.session}
    else:
        session = find_session(started)
    if not session:
        print("!! no Cortex session found after the run start; pass --session <id> (see `cortex conversations list`)")

    pretty, tjson = "", None
    if session:
        sid = session["session_id"]
        pretty = sh(["cortex", "conversations", "transcript", sid], check=False)
        raw = sh(["cortex", "conversations", "transcript", sid, "-o", "json"], check=False)
        add_session_secrets(session)
        tjson, bad = [], 0
        for l in raw.splitlines():  # JSON Lines; a message with raw newlines can break a line
            if l.strip():
                try:
                    tjson.append(json.loads(l))
                except json.JSONDecodeError:
                    bad += 1
        write(os.path.join(run_dir, "transcript.md"), "```text\n" + redact(pretty) + "\n```\n")
        write(os.path.join(run_dir, "transcript.json"),
              {"unparsed_lines": bad, "messages": redact_obj(public_transcript(tjson))})

    if args.keep_snowflake:  # re-process the transcript only; reuse the Snowflake telemetry already saved
        old = json.load(open(os.path.join(run_dir, "telemetry.json")))
        sf_summary, violations = old["snowflake"], old["read_only_violations"]
        failed, unbounded = old["failed_queries"], old["account_usage_unbounded"]
        rows = json.load(open(os.path.join(run_dir, "queries.json")))
        sf_summary = count_failures(sf_summary, failed)
    else:
        sf_summary, rows, violations, failed, unbounded = snowflake_telemetry(started, ended)
    telemetry = {
        "case": meta["case"], "run_id": meta["run_id"], "started_at": meta["started_at"],
        "ended_at": ended.isoformat(), "cortex_version": meta["cortex_version"],
        "git_sha": meta["git_sha"], "skill_sha": meta["skill_sha"],
        "cortex": cortex_telemetry(session, tjson), "snowflake": sf_summary,
        "read_only_violations": violations, "failed_queries": failed, "account_usage_unbounded": unbounded,
    }
    write(os.path.join(run_dir, "telemetry.json"), redact_obj(telemetry))
    write(os.path.join(run_dir, "queries.json"), redact_obj(rows))

    auto = auto_grade(meta["case"], agent_text(pretty), sf_summary, telemetry["cortex"])
    grading = {"auto": auto, "manual": {}, "verdict": None}
    write(os.path.join(run_dir, "grading.json"), grading)
    write(os.path.join(run_dir, "grading.md"), grading_md(meta, auto))
    fpath = os.path.join(run_dir, "findings.md")
    if not os.path.exists(fpath):
        write(fpath, FINDINGS_TEMPLATE.format(**meta))
    if not args.rerun:
        os.remove(CURRENT)

    passed = sum(a["pass"] for a in auto)
    print(f"Telemetry: {sf_summary['queries']} Snowflake queries, {sf_summary['failed_queries']} failed "
          f"(+{sf_summary['cortex_internal_failed']} Cortex Code internal), "
          f"~{sf_summary['est_credits_analysis']} credits, read-only violations: {sf_summary['read_only_violations']}")
    print(f"Auto checks: {passed}/{len(auto)} passed")
    for a in auto:
        if not a["pass"]:
            print(f"  ✗ {a['check']}")
    build_summary()
    git_publish([rel, "evals/results.md"], f"eval({meta['case']}): {meta['run_id']} telemetry + auto-grading")
    print(f"\nNext: tick manual checks in {rel}/grading.md, write {rel}/findings.md, then:\n"
          f"  python3 evals/harness/eval.py publish {rel}")


# ---------------------------------------------------------------------------- publish / summary
def parse_grading_md(path):
    text = open(path).read()
    manual_section = text.split("## Manual checks", 1)[-1].split("## Verdict", 1)[0]
    manual = {m.group(2).strip(): m.group(1).lower() == "x"
              for m in re.finditer(r"- \[( |x|X)\] (.+)", manual_section)}
    v = re.search(r"^Verdict:\s*(Pass|Partial|Fail)\b(.*)$", text, re.I | re.M)
    return manual, (v.group(1).capitalize(), v.group(2).strip()) if v else None


def verdict(auto, manual, override):
    if override:
        return override[0]
    if any(not a["pass"] for a in auto if a["check"].startswith("read_only")):
        return "Fail"
    results = [a["pass"] for a in auto] + list(manual.values())
    if not manual or not any(manual.values()):
        return "Ungraded"
    rate = sum(results) / len(results)
    return "Pass" if rate == 1 else "Partial" if rate >= 0.7 else "Fail"


def cmd_publish(args):
    run_dir = os.path.join(ROOT, args.run_dir) if args.run_dir else latest_run_dir()
    g = json.load(open(os.path.join(run_dir, "grading.json")))
    manual, override = parse_grading_md(os.path.join(run_dir, "grading.md"))
    g["manual"], g["verdict"] = manual, verdict(g["auto"], manual, override)
    g["override_reason"] = override[1] if override else None
    write(os.path.join(run_dir, "grading.json"), g)
    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    build_summary()
    print(f"{meta['case']} {meta['run_id']}: {g['verdict']}")
    git_publish([os.path.relpath(run_dir, ROOT), "evals/results.md"],
                f"eval({meta['case']}): {meta['run_id']} grading + findings ({g['verdict']})")


def latest_run_dir():
    dirs = sorted(glob.glob(os.path.join(RUNS, "TC*", "run-*")), key=lambda d: os.path.basename(d))
    if not dirs:
        sys.exit("no runs yet")
    return dirs[-1]


def build_summary():
    """results.md: pass rates for the current skill version first, then older versions, then every run by case."""
    current = skill_sha()
    runs = []
    for d in sorted(glob.glob(os.path.join(RUNS, "TC*", "run-*"))):
        try:
            meta = json.load(open(os.path.join(d, "meta.json")))
            tel = json.load(open(os.path.join(d, "telemetry.json")))
            g = json.load(open(os.path.join(d, "grading.json")))
        except FileNotFoundError:
            continue
        runs.append((meta, tel, g, os.path.relpath(d, EVALS)))

    def verdict_of(g):
        return g.get("verdict") or "Ungraded"

    def rate_table(rs):
        out = ["| Case | Type | Runs | Pass | Partial | Fail | Pass rate |", "|---|---|---|---|---|---|---|"]
        for case, c in CASES.items():
            vs = [verdict_of(g) for m, _, g, _ in rs if m["case"] == case]
            if not vs:
                continue
            graded = [v for v in vs if v != "Ungraded"]
            rate = f"{round(100 * graded.count('Pass') / len(graded))}%" if graded else "–"
            out.append(f"| {case} | {c['type']} | {len(vs)} | {vs.count('Pass')} | {vs.count('Partial')} | "
                       f"{vs.count('Fail')} | {rate} |")
        return out

    repeats = json.load(open(os.path.join(EVALS, "harness", "cases.json")))["repeats"]
    cur = [r for r in runs if r[0]["skill_sha"] == current]
    lines = ["# Eval results: dbt-cost-tuning", "",
             "Generated by `evals/harness/eval.py`. Don't edit by hand. A run's result belongs to the skill version "
             f"(`skill_sha`) it ran on; target is {repeats}× per case per version.", "",
             f"## Current skill version (`{current}`)", ""]
    lines += rate_table(cur) if cur else ["No runs on this version yet."]

    older = sorted({m["skill_sha"] for m, *_ in runs} - {current},
                   key=lambda sha: min(m["started_at"] for m, *_ in runs if m["skill_sha"] == sha))
    if older:
        lines += ["", "## Earlier skill versions", "",
                  "Superseded by skill fixes; kept as the before/after record."]
        for sha in older:
            rs = [r for r in runs if r[0]["skill_sha"] == sha]
            lines += ["", f"### `{sha}` ({min(m['started_at'] for m, *_ in rs)[:10]}, git `{rs[0][0]['git_sha']}`)"]
            lines += [""] + rate_table(rs)

    not_run = [f"{case} ({c['type']})" for case, c in CASES.items() if not any(m["case"] == case for m, *_ in runs)]
    if not_run:
        lines += ["", "## Designed, not run", "",
                  "Defined in `test_matrix.md` and `harness/cases.json`, with setup scripts, but not run: "
                  + ", ".join(not_run) + "."]

    lines += ["", "## All runs", "",
              "| Case | Run | Skill sha | Verdict | Auto | Manual | Read-only violations | SF queries (skill failures) | "
              "Est. credits (analysis) | Wall s | Tool calls | Findings |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for meta, tel, g, rel in sorted(runs, key=lambda r: (r[0]["case"], r[0]["run_id"])):
        man = g.get("manual") or {}
        sf, cx = count_failures(dict(tel["snowflake"]), tel["failed_queries"]), tel["cortex"]
        sha = f"**{meta['skill_sha']}**" if meta["skill_sha"] == current else meta["skill_sha"]
        lines.append(f"| {meta['case']} | [{meta['run_id']}]({rel}/grading.md) | {sha} | {verdict_of(g)} | "
                     f"{sum(a['pass'] for a in g['auto'])}/{len(g['auto'])} | "
                     f"{sum(man.values())}/{len(man) or len(CASES[meta['case']]['manual'])} | "
                     f"{sf['read_only_violations']} | {sf['queries']} ({sf['failed_queries']} failed) | "
                     f"{sf['est_credits_analysis']} | {cx.get('wall_seconds', '?')} | {cx.get('tool_calls', '?')} | "
                     f"[findings]({rel}/findings.md) |")
    write(os.path.join(EVALS, "results.md"), "\n".join(lines + [""]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("case"); s.add_argument("--force", action="store_true")
    f = sub.add_parser("finish"); f.add_argument("--session", help="Cortex session id, if auto-detect picks the wrong one")
    f.add_argument("--rerun", metavar="RUN_DIR", help="re-process an already finished run, e.g. evals/runs/TC01/run-...")
    f.add_argument("--keep-snowflake", action="store_true",
                   help="with --rerun: reuse the saved Snowflake telemetry instead of querying it again")
    p = sub.add_parser("publish"); p.add_argument("run_dir", nargs="?", help="evals/runs/<CASE>/<run> (default: latest)")
    sub.add_parser("summary")
    a = ap.parse_args()
    {"start": cmd_start, "finish": cmd_finish, "publish": cmd_publish,
     "summary": lambda _: (build_summary(), print("wrote evals/results.md"))}[a.cmd](a)


if __name__ == "__main__":
    main()
