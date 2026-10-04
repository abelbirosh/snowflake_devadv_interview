"""Drives one headless Cortex Code session for an automated eval run.

Cortex is started with `--input-format stream-json --output-format stream-json`: the harness writes the case's
prompts to stdin one turn at a time and reads events from stdout. Tool approvals arrive as `control_request`
(`can_use_tool`) events and are answered by `decide()`, the same policy the runbook gives a person: approve
read-only SQL, deny anything that changes Snowflake or the repo. Every decision is logged for grading.
"""
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid

READ_ONLY_SQL = re.compile(r"^\s*(\(\s*)*(SELECT|WITH|SHOW|DESC|DESCRIBE|EXPLAIN|LIST|LS|GET|USE|ALTER\s+SESSION)\b", re.I)
SQL_COMMENT = re.compile(r"(--[^\n]*\n|/\*.*?\*/)", re.S)
TEMP_DIRS = tuple(os.path.realpath(p) for p in {"/tmp", "/private/tmp", "/var/folders", os.environ.get("TMPDIR", "/tmp")})
# Shell commands that could change Snowflake, the dbt project or git. Matched as commands (start of the line or after
# ; & | $( ` xargs), not as words, so a path like /tmp/dbt-cost-tuning is fine.
CMD = r"(?:^|[;&|(`]\s*|\bxargs\s+|\benv\s+)"
BASH_DENY = re.compile(CMD + r"(?:snow|dbt|cortex|sudo|curl|wget|tee)(?=\s|$)"
                       + r"|" + CMD + r"git\s+(?:commit|push|checkout|reset|apply|stash|add|rm|mv|restore|clean)\b"
                       + r"|" + CMD + r"(?:rm\s+-\w*[rf]|sed\s+-i)", re.I | re.M)
QUESTION_TOOLS = {"ask_user_question", "askuserquestion", "question"}


HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(\n|$)", re.S)
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def shell_skeleton(cmd):
    """The command with heredoc bodies and quoted strings blanked, so code inside `python3 -c "..."` (like
    `print(a > b)`) isn't read as a shell redirect or command. Writes made from inside that code are caught
    afterwards by the run's no_repo_edits check."""
    return QUOTED.sub("''", HEREDOC.sub("\n", cmd))


def sql_statements(text):
    return [s for s in SQL_COMMENT.sub("\n", text + "\n").split(";") if s.strip()]


def decide(tool, inp, repo):
    """(allow, reason). `tool`/`inp` are the control request's tool_name and input."""
    name = (tool or "").lower()
    resource = str(inp.get("resource") or inp.get("command") or inp.get("sql") or inp.get("file_path") or "")
    if name in ("sql", "sql_execute"):
        bad = [s.strip()[:80] for s in sql_statements(resource) if not READ_ONLY_SQL.match(s)]
        return (not bad, "read-only SQL" if not bad else f"not read-only: {bad[0]}")
    if name in ("write", "edit", "apply_patch", "notebook_actions"):
        path = os.path.realpath(resource) if resource else ""
        if path.startswith(TEMP_DIRS):
            return True, "write under a temp dir"
        return False, "file edits are out of scope for this read-only eval"
    if name == "bash":
        resource = shell_skeleton(resource)
        if BASH_DENY.search(resource):
            return False, "command could change Snowflake, dbt or git"
        for target in re.findall(r"(?<![<>&0-9])>{1,2}\s*([^\s;&|]+)", resource):
            if not (target.startswith(("&", "/dev/null", "$TMPDIR")) or os.path.realpath(target).startswith(TEMP_DIRS)):
                return False, "redirects output to a file outside a temp dir"
        return True, "read-only shell"
    if name in ("read", "grep", "glob", "web_fetch", "web_search", "skill"):
        return True, "read-only tool"
    return False, f"tool {tool!r} not on the eval allow-list"


class CortexSession:
    """One `cortex` process in stream-json mode. Use as a context manager."""

    def __init__(self, connection, workdir, log_path, answers=None, extra_args=()):
        self.session_id = str(uuid.uuid4())
        self.workdir, self.answers = workdir, dict(answers or {})
        self.permissions, self.turns = [], []
        self.log = open(log_path, "w")
        self.cmd = ["cortex", "--input-format", "stream-json", "--output-format", "stream-json",
                    "-c", connection, "--session-id", self.session_id,
                    "--no-auto-update", *extra_args]
        self.events = queue.Queue()

    def __enter__(self):
        self.proc = subprocess.Popen(self.cmd, cwd=self.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
        return self

    def __exit__(self, *exc):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except Exception:
            self.proc.kill()
        self.log.close()

    def _read(self):
        for line in self.proc.stdout:
            self.log.write(line)
            self.log.flush()
            try:
                self.events.put(json.loads(line))
            except json.JSONDecodeError:
                pass  # banner / log lines
        self.events.put(None)

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _answer(self, request_id, allow, message, inp):
        body = {"behavior": "allow", "updatedInput": inp} if allow else {"behavior": "deny", "message": message}
        self._send({"type": "control_response",
                    "response": {"subtype": "success", "request_id": request_id, "response": body}})

    def _question_reply(self, inp, pending):
        """Reply to Cortex's question picker: a case answer whose key matches the question, else the next prompt."""
        text = json.dumps(inp)
        for pattern, reply in self.answers.items():
            if re.search(pattern, text, re.I):
                return reply
        return pending.pop(0) if pending else "Use your recommended default."

    def turn(self, prompt, pending, timeout_s):
        """Send one user message and handle events until Cortex reports the turn's `result`."""
        self._send({"type": "user", "message": {"role": "user", "content": prompt}})
        t0, record = time.time(), {"prompt": prompt, "result": None}
        while True:
            left = timeout_s - (time.time() - t0)
            if left <= 0:
                record["result"] = "timeout"
                break
            try:
                ev = self.events.get(timeout=left)
            except queue.Empty:
                continue
            if ev is None:
                record["result"] = "process_exited"
                break
            if ev.get("type") == "control_request" and ev["request"].get("subtype") == "can_use_tool":
                r = ev["request"]
                tool, inp = r.get("tool_name") or "", r.get("input") or {}
                if tool.lower().replace("_", "") in {q.replace("_", "") for q in QUESTION_TOOLS}:
                    reply = self._question_reply(inp, pending)
                    # Approving with `answers` filled in is how a picker answer reaches the agent (a deny reads
                    # as "cancelled"). Keyed by question text and id; Cortex accepts either.
                    answers = {k: reply for q in inp.get("questions", []) for k in (q.get("question"), q.get("questionId")) if k}
                    self._answer(ev["request_id"], True, "", {**inp, "answers": answers})
                    self.permissions.append({"tool": tool, "allowed": "answered", "reply": reply, "input": inp})
                    continue
                allow, reason = decide(tool, inp, self.workdir)
                self._answer(ev["request_id"], allow, f"Denied by the eval harness: {reason}.", inp)
                self.permissions.append({"tool": tool, "allowed": allow, "reason": reason, "input": inp})
            elif ev.get("type") == "result":
                record.update(result=ev.get("subtype"), is_error=ev.get("is_error"),
                              duration_ms=ev.get("duration_ms"), num_turns=ev.get("num_turns"),
                              usage=ev.get("usage"), permission_denials=ev.get("permission_denials"))
                break
        record["seconds"] = round(time.time() - t0)
        self.turns.append(record)
        return record

    def run(self, prompts, timeout_s):
        pending = list(prompts)
        while pending:
            rec = self.turn(pending.pop(0), pending, timeout_s)
            if rec["result"] in ("timeout", "process_exited"):
                break
        return self.turns
