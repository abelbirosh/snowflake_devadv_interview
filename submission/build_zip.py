#!/usr/bin/env python3
"""Build submission/dbt-cost-tuning-skill.zip.

Contents (zip root): the skill folder `dbt-cost-tuning/`, `README.md` (from submission/README.md), `evals/`, and the
Tasty Bytes demo project `tasty_bites/` the evals run against. Only git-tracked files go in, so no caches or
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


entries = [(f, "dbt-cost-tuning/" + os.path.relpath(f, SKILL)) for f in tracked(SKILL)]
entries.append(("submission/README.md", "README.md"))
entries += [(f, f) for f in tracked("evals") + tracked("tasty_bites")]

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

if leaks:
    os.remove(tmp)
    sys.exit("refusing to build, identifiers left in:\n  " + "\n  ".join(leaks))
os.replace(tmp, OUT)
print(f"wrote {os.path.relpath(OUT, ROOT)}: {len(entries)} files, {os.path.getsize(OUT) / 1e6:.1f} MB; "
      f"connection names scrubbed: {len(conn_names)}")
