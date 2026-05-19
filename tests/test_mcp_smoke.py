#!/usr/bin/env python3
"""Smoke test: 1 call per tool MCP, verifica risposta non-error.

Standalone runnable senza pytest:
    python3 anja/tests/test_mcp_smoke.py

Con pytest (se installato):
    python3 -m pytest anja/tests/ -v

Crea un wiki temporaneo in TMPDIR per non sporcare lo state reale del progetto.
Lancia il server MCP via stdio JSON-RPC come farebbe Claude Code, e verifica
che ogni tool risponda senza chiave `error` nel result.

Tool esclusi (richiedono setup esterno o side-effect indesiderati):
  - code.search, code.reindex, code.status — richiedono API key embed + index built
  - sessions.summarize — spawna `claude` CLI esterno
  - wiki.delete (con confirm=true) — distruttivo; testiamo solo preview
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Plugin root = parent della cartella tests/
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PLUGIN_ROOT / "scripts" / "mcp_memory_server.py"
INIT_SCRIPT = PLUGIN_ROOT / "scripts" / "init_project.py"
PYTHON = "/opt/homebrew/opt/python@3.12/bin/python3.12" if Path(
    "/opt/homebrew/opt/python@3.12/bin/python3.12"
).is_file() else sys.executable


def _setup_test_project() -> Path:
    """Crea project temporaneo scaffoldato con /anja-init equivalente."""
    tmp = Path(tempfile.mkdtemp(prefix="anja-smoke-"))
    project = tmp / "test-project"
    project.mkdir()
    anjawiki = project / ".anjawiki"

    # Scaffolda via init_project.py (cold mode)
    res = subprocess.run(
        [PYTHON, str(INIT_SCRIPT),
         "--type", "dev",
         "--mode", "cold",
         "--target", str(anjawiki),
         "--name", "test-project"],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"init failed: {res.stderr}")
    return project


def _call_server(project_root: Path, calls: list[dict]) -> list[dict]:
    """Lancia server MCP via stdio, manda initialize + N tools/call, ritorna risposte."""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    for i, call in enumerate(calls, start=2):
        msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                     "params": {"name": call["name"], "arguments": call.get("args", {})}})

    proc = subprocess.Popen(
        [PYTHON, str(SERVER_PATH)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        env={
            "ANJA_SCOPE": "project",
            "ANJA_ROOT": str(project_root),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": os.environ.get("HOME", "/tmp"),
        },
    )
    stdin_text = "\n".join(json.dumps(m) for m in msgs) + "\n"
    try:
        out, err = proc.communicate(stdin_text, timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise RuntimeError(f"server timeout. stderr: {err[-500:]}")

    responses = {}
    for line in out.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in d:
            responses[d["id"]] = d

    return [responses.get(i + 2) for i in range(len(calls))]


def _is_ok(response: dict, allow_error_keys: tuple[str, ...] = ()) -> tuple[bool, str]:
    """True se response è valida e content non riporta `error` (eccetto allow_error_keys)."""
    if not response:
        return False, "no response"
    if "error" in response:
        return False, f"jsonrpc error: {response['error']}"
    result = response.get("result", {})
    content = result.get("content", [])
    if not content:
        return False, "empty content"
    text = content[0].get("text", "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Plain text response (rare) — considerato ok se non vuoto
        return bool(text), "non-json ok" if text else "empty text"
    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        if any(k in err.lower() for k in allow_error_keys):
            return True, f"acceptable error: {err}"
        return False, f"tool error: {err}"
    return True, "ok"


def run_smoke_tests() -> tuple[int, int]:
    """Returns (passed, total)."""
    print(f"[smoke] setup test project in {tempfile.gettempdir()}/anja-smoke-*")
    project = _setup_test_project()
    print(f"[smoke] project: {project}")

    # First, seed: crea 1 entity per avere qualcosa da read/backlink/lint
    seed_calls = [
        {"name": "wiki.upsert_entity", "args": {
            "slug": "test-entity",
            "title": "Test Entity",
            "sections": {"Sintesi": "Entity creata da smoke test."},
        }},
        {"name": "wiki.upsert_concept", "args": {
            "slug": "test-concept",
            "title": "Test Concept",
            "sections": {"Definizione": "Concept creato da smoke test."},
        }},
        {"name": "roadmap.add", "args": {
            "title": "smoke test task",
            "priority": "P3",
        }},
    ]
    seed_responses = _call_server(project, seed_calls)
    for call, resp in zip(seed_calls, seed_responses):
        ok, msg = _is_ok(resp)
        print(f"  seed {call['name']}: {'✓' if ok else '✗'} {msg}")

    # Smoke calls per tool (1 ciascuno)
    smoke_calls = [
        # wiki group
        {"name": "wiki.tree", "args": {}},
        {"name": "wiki.stats", "args": {"top_n": 3}},
        {"name": "wiki.search", "args": {"query": "test", "limit": 3}},
        {"name": "wiki.read", "args": {"slug": "test-entity"}},
        {"name": "wiki.backlinks", "args": {"slug": "test-entity"}},
        {"name": "wiki.lint", "args": {"categories": ["orphans", "broken_links"]}},
        {"name": "wiki.log_append", "args": {"type": "note", "description": "smoke test log entry"}},
        {"name": "wiki.delete", "args": {"slug": "test-entity", "confirm": False}},
        {"name": "wiki.index_update", "args": {"category": "Entities", "entries": ["- [[test-entity]] — smoke entry"]}},
        {"name": "wiki.export", "args": {"format": "json"}},
        # roadmap group
        {"name": "roadmap.list", "args": {}},
        # sessions group
        {"name": "sessions.list", "args": {"limit": 5}},
        # memory group
        {"name": "memory.recall", "args": {"topic": "test"}},
        {"name": "memory.timeline", "args": {"limit": 5}},
        # soul / user (allow "not found" perché in test project SOUL minimal)
        {"name": "soul.show", "args": {}},
        # skills
        {"name": "skill.list", "args": {}},
    ]
    responses = _call_server(project, smoke_calls)
    passed = 0
    print()
    print("[smoke] tool calls:")
    # user.read in scope=project senza hub: error atteso (no global, no hub) — non testato qui
    for call, resp in zip(smoke_calls, responses):
        allow = ()
        if call["name"] in ("memory.recall", "memory.timeline"):
            allow = ("not found", "no sessions", "empty")
        elif call["name"] == "skill.list":
            # Hub-only tool: senza ANJA_HUB_WEBAPP ritorna errore "not available"
            allow = ("not available", "requires the anja-hub")
        ok, msg = _is_ok(resp, allow_error_keys=allow)
        status = "✓" if ok else "✗"
        print(f"  {status} {call['name']:30s} — {msg}")
        if ok:
            passed += 1

    # cleanup
    shutil.rmtree(project.parent, ignore_errors=True)
    return passed, len(smoke_calls)


if __name__ == "__main__":
    passed, total = run_smoke_tests()
    print()
    print(f"[smoke] {passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


def test_mcp_smoke():
    """pytest entry point."""
    passed, total = run_smoke_tests()
    assert passed == total, f"smoke failed: {passed}/{total}"
