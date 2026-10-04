#!/usr/bin/env python3
"""Build submission/dbt-cost-tuning-skill.zip.

Contents (zip root): the skill folder `dbt-cost-tuning/`, `README.md` (from submission/README.md), `evals/`,
`screenshots/` (every run's screenshots, grouped by test case, with an index), and the Tasty Bytes demo project
`tasty_bites/` the evals run against. Only git-tracked files go in, so no caches or
in-progress run markers.

Screenshots are re-encoded as 64-colour PNGs (terminal captures have few colours, so text stays sharp; ~65% smaller).
The originals in the repo are untouched. Text files are scrubbed: every connection name in ~/.snowflake/connections.toml becomes MY_CONNECTION. The build fails
if any account, user, host or email value from that file is still present.

Usage (from the repo root): python3 submission/build_zip.py
"""
import os
import re
import subprocess
import sys
import tomllib
import io
import json
import zipfile

from PIL import Image

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
OUT = os.path.join(ROOT, "submission", "dbt-cost-tuning-skill.zip")
SKILL = ".cortex/skills/dbt-cost-tuning"
TEXT_EXT = {".md", ".json", ".py", ".sql", ".sh", ".yml", ".yaml", ".txt", ".toml", ""}

conns = {}
try:
    conns = tomllib.load(open(os.path.expanduser("~/.snowflake/connections.toml"), "rb"))
except FileNotFoundError:
    pass
conn_names = sorted(conns, key=len, reverse=True)
forbidden = sorted({v for c in conns.values() for k, v in c.items()
                    if k in ("account", "user", "host", "email") and isinstance(v, str) and len(v) > 2}, key=len, reverse=True)


def tracked(path):
    out = subprocess.run(["git", "ls-files", path], capture_output=True, text=True, cwd=ROOT, check=True).stdout
    return [f for f in out.splitlines() if f]


def scrub(text):
    """Connection names, case-insensitive; AG_X_RESTRICTED-style suffixes are kept (MY_CONNECTION_RESTRICTED)."""
    for name in conn_names:
        text = re.sub(re.escape(name), "MY_CONNECTION", text, flags=re.I)
    return text


CASE_LABELS = {"TC01": "happy-path", "TC02": "reworded-prompt", "TC03": "no-warehouse", "TC04": "no-dbt-project",
               "TC05": "no-account-usage", "TC06": "history-behind", "TC07": "no-successful-runs",
               "TC08": "no-query-comments", "TC09": "several-projects", "TC10": "guardrail-apply-request"}
SHOT = re.compile(r"evals/runs/(TC\d+)/(run-(\d{8})T\d+Z)/screenshots/([^/]+\.png)$")


def screenshot_arc(path, run_index):
    """evals/runs/TC01/run-…/screenshots/x.png -> screenshots/TC01-happy-path/run2-2026-10-02-Partial/x.png"""
    case, run, day, name = SHOT.match(path).groups()
    grading = os.path.join(ROOT, "evals", "runs", case, run, "grading.json")
    verdict = json.load(open(grading)).get("verdict", "ungraded") if os.path.exists(grading) else "ungraded"
    n = run_index[case].index(run) + 1
    return f"screenshots/{case}-{CASE_LABELS.get(case, 'case')}/run{n}-{day[:4]}-{day[4:6]}-{day[6:]}-{verdict}/{name}"


entries = [(f, "dbt-cost-tuning/" + os.path.relpath(f, SKILL)) for f in tracked(SKILL)]
entries.append(("submission/README.md", "README.md"))
eval_files = tracked("evals")
run_index = {}
for f in eval_files:
    m = SHOT.match(f)
    if m and m.group(2) not in run_index.setdefault(m.group(1), []):
        run_index[m.group(1)].append(m.group(2))
for runs in run_index.values():
    runs.sort()
# Screenshots go in one top-level folder grouped by test case; everything else in evals/ keeps its path.
entries += [(f, screenshot_arc(f, run_index) if SHOT.match(f) else f) for f in eval_files]
entries += [(f, f) for f in tracked("tasty_bites")]

index = ["# Screenshots by test case", "",
         "One folder per test case, one subfolder per run: `run<N>-<date>-<verdict>`. The run's transcript, grading and",
         "findings are in `evals/runs/<case>/<run id>/` (the run id is listed below).", ""]
for case in sorted(run_index):
    index += [f"## {case}: {CASE_LABELS.get(case, '')}", "", "| Folder | Run id | Screenshots |", "|---|---|---|"]
    folders = {}
    for src, arc in entries:
        m = SHOT.match(src)
        if m and m.group(1) == case:
            folders.setdefault((os.path.dirname(arc), m.group(2)), []).append(os.path.basename(arc))
    for (folder, run), names in sorted(folders.items(), key=lambda kv: kv[0][1]):
        index.append(f"| `{folder.split('/', 2)[2]}` | `{run}` | {', '.join(sorted(names))} |")
    index.append("")

leaks = []
tmp = OUT + ".tmp"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for src, arc in entries:
        data = open(os.path.join(ROOT, src), "rb").read()
        if os.path.splitext(src)[1].lower() in TEXT_EXT:
            text = scrub(data.decode("utf-8"))
            leaks += [f"{arc}: {v[:3]}…" for v in forbidden + conn_names if v.lower() in text.lower()]
            data = text.encode("utf-8")
        elif src.endswith(".png"):
            buf = io.BytesIO()
            Image.open(io.BytesIO(data)).convert("RGB").quantize(
                colors=64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
            data = buf.getvalue()
        z.writestr(arc, data)
    z.writestr("screenshots/README.md", "\n".join(index))

if leaks:
    os.remove(tmp)
    sys.exit("refusing to build, identifiers left in:\n  " + "\n  ".join(leaks))
os.replace(tmp, OUT)
print(f"wrote {os.path.relpath(OUT, ROOT)}: {len(entries)} files, {os.path.getsize(OUT) / 1e6:.1f} MB; "
      f"connection names scrubbed: {len(conn_names)}")
