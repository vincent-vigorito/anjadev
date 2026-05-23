#!/usr/bin/env python3
"""skill_evolution.py — PostToolUse hook: traccia invocazioni di skill scripts.

Detecta quando l'agent invoca uno script di una skill anja (path matching
`*/skills/<name>/scripts/<script>.py`) tramite Bash. Estrae name skill,
argomenti, exit code, output preview e accoda all'inbox.

Inbox: `~/.anja/skill_evolution_inbox.jsonl` (append-only, jsonl).

Dedup: hash dei primi 200 char di stdin (tool_input.command). Se ripetuto
< 60 secondi, skip (rumore di retry).

Skip silenzioso se ANJA_SKILL_EVOLUTION=0 o se stdin non parseable.

Input atteso (JSON via stdin):
  {
    "tool_name": "Bash",
    "tool_input": {"command": "python3 .../skills/X/scripts/Y.py args..."},
    "tool_response": {"stdout": "...", "stderr": "...", "interrupted": false},
    ...
  }
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


INBOX_PATH = Path.home() / ".anja" / "skill_evolution_inbox.jsonl"
DEDUP_WINDOW_SEC = 60
OUTPUT_PREVIEW_MAX = 800

# Pattern: cattura "/<somewhere>/skills/<name>/scripts/<script>.py" dalla bash command
SKILL_SCRIPT_RE = re.compile(
    r"(?:^|\s|['\"])(/(?:[^/\s'\"]+/)*skills/([a-z][a-z0-9-]*)/scripts/([a-z][a-z0-9_.-]*\.py))",
)


def _parse_stdin() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}


def _extract_skill_invocation(command: str) -> dict | None:
    """Cerca pattern skill script invocation nella bash command.
    Ritorna {skill_name, script_path, args} o None se non trovato."""
    m = SKILL_SCRIPT_RE.search(command)
    if not m:
        return None
    script_path = m.group(1)
    skill_name = m.group(2)
    script_name = m.group(3)
    # Args = tutto dopo il script path, splittato grossolanamente
    after = command[m.end():].strip()
    return {
        "skill_name": skill_name,
        "script_path": script_path,
        "script_name": script_name,
        "args_raw": after[:300],
    }


def _recent_hash_seen(entry_hash: str) -> bool:
    """True se hash visto < DEDUP_WINDOW_SEC secondi fa. Idempotency."""
    if not INBOX_PATH.is_file():
        return False
    cutoff = time.time() - DEDUP_WINDOW_SEC
    try:
        with INBOX_PATH.open(encoding="utf-8") as f:
            # Read last ~50 lines (più che sufficiente per window)
            lines = f.readlines()[-50:]
        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("hash") == entry_hash and rec.get("ts_unix", 0) > cutoff:
                return True
    except OSError:
        return False
    return False


def _append_inbox(entry: dict) -> None:
    INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INBOX_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> None:
    if os.environ.get("ANJA_SKILL_EVOLUTION", "1") == "0":
        return

    data = _parse_stdin()
    if data.get("tool_name") != "Bash":
        return
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command", "") or ""
    if not command:
        return

    invocation = _extract_skill_invocation(command)
    if not invocation:
        return

    # Tool response
    response = data.get("tool_response") or {}
    stdout = response.get("stdout", "") or ""
    stderr = response.get("stderr", "") or ""
    exit_code = response.get("interrupted") and -1 or 0
    # CC PostToolUse hooks ricevono "stdout" anche se exit != 0, exit explicit non sempre
    # presente — uso heuristic
    if stderr.strip() and not stdout.strip():
        exit_code = 1

    output_preview = (stdout or stderr)[:OUTPUT_PREVIEW_MAX]

    # Hash for dedup
    entry_hash = hashlib.sha256(command[:200].encode("utf-8")).hexdigest()[:16]
    if _recent_hash_seen(entry_hash):
        return

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ts_unix": time.time(),
        "hash": entry_hash,
        "skill": invocation["skill_name"],
        "script": invocation["script_name"],
        "script_path": invocation["script_path"],
        "args_raw": invocation["args_raw"],
        "exit": exit_code,
        "output_preview": output_preview,
        "processed": False,
    }
    try:
        _append_inbox(entry)
    except Exception:
        pass


if __name__ == "__main__":
    main()
