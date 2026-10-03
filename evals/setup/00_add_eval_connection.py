#!/usr/bin/env python3
"""dbt-cost-tuning eval TC05: add an [AG26866_RESTRICTED] connection to ~/.snowflake/connections.toml.

Copies the [AG26866] block and changes only its role to DBT_TUNING_EVAL_RESTRICTED (created by
01_restricted_roles.sql). Does nothing if the connection already exists. Back up the file first:
  cp ~/.snowflake/connections.toml ~/.snowflake/connections.toml.bak
Usage (from the repo root): python3 evals/setup/00_add_eval_connection.py [SOURCE_CONN]
"""
import os
import re
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "AG26866"
NEW, ROLE = f"{SRC}_RESTRICTED", "DBT_TUNING_EVAL_RESTRICTED"
path = os.path.expanduser("~/.snowflake/connections.toml")
lines = open(path).read().splitlines()

if f"[{NEW}]" in (l.strip() for l in lines):
    sys.exit(f"[{NEW}] already exists; nothing to do")

block, inside = [], False
for line in lines:
    if line.strip().startswith("["):
        inside = line.strip() == f"[{SRC}]"
        continue
    if inside:
        block.append(line)
if not block:
    sys.exit(f"[{SRC}] not found in {path}")

block = [f'role = "{ROLE}"' if re.match(r"\s*role\s*=", l) else l for l in block]
if not any(re.match(r"\s*role\s*=", l) for l in block):
    block.append(f'role = "{ROLE}"')
while block and not block[-1].strip():
    block.pop()

with open(path, "a") as f:
    f.write(f"\n[{NEW}]\n" + "\n".join(block) + "\n")
print(f"added [{NEW}] (copy of [{SRC}], role = {ROLE})")
