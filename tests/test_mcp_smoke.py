#!/usr/bin/env python3
"""Smoke test parametrico: OGNI tool del registry viene chiamato almeno una volta
sul wire (stdio JSON-RPC, come farebbe Claude Code) — PIANO.md 1.3.

Regole:
  - un errore JSON-RPC (protocollo, eccezione nell'handler) è SEMPRE un fallimento;
  - un errore applicativo (`{"error": ...}` nel payload) è accettato solo per i tool
    che dipendono dall'ambiente (embedding provider, index vettoriale, CLI del harness),
    e solo se il messaggio è quello atteso;
  - la copertura è verificata: se un tool del registry non è nella sequenza, il test
    fallisce (un tool nuovo deve avere una voce qui).

Il progetto è temporaneo; HOME è temporaneo (user.* scrive in ~/.anja/, mai quello reale).

Standalone:  python3 tests/test_mcp_smoke.py
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _helpers import cov_env

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PLUGIN_ROOT / "scripts" / "mcp_memory_server.py"
INIT_SCRIPT = PLUGIN_ROOT / "scripts" / "init_project.py"
PYTHON = os.environ.get("ANJA_TEST_PYTHON") or sys.executable

# 1x1 PNG trasparente per wiki.attach_image
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

# Tool il cui errore applicativo è accettato (dipendono da provider embedding / index / CLI),
# con i frammenti di messaggio attesi (lower-case). Qualunque altro errore → fallimento.
ENV_DEPENDENT = {
    "wiki.embed": ("provider", "index", "api key", "sqlite"),
    "wiki.find_duplicates": ("provider", "index", "api key", "sqlite"),
    "wiki.search_semantic": ("provider", "index", "api key", "sqlite"),
    "sessions.search_semantic": ("provider", "index", "api key", "sqlite"),
    "graph.search_text": ("provider", "index", "api key", "sqlite"),
    "graph.semantic_neighbors": ("provider", "index", "api key", "sqlite", "not found"),
    "graph.report": ("provider", "index", "api key", "sqlite"),
    "graph.html": ("provider", "index", "api key", "sqlite"),
    "code.compare_decision": ("cannot establish", "decision missing"),
    "code.search": ("provider", "index", "api key", "sqlite", "ripgrep", "rg"),
    "code.reindex": ("provider", "index", "api key", "sqlite"),
    "sessions.summarize": ("not found",),   # id inesistente: non deve spawnare il CLI
}


def _setup_project(tmp: Path) -> tuple[Path, dict]:
    project = tmp / "test project"   # spazio nel path: deve funzionare
    project.mkdir()
    anjawiki = project / ".anjawiki"
    res = subprocess.run([PYTHON, str(INIT_SCRIPT), "--type", "dev", "--mode", "cold",
                          "--target", str(anjawiki), "--name", "test-project"],
                         capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(f"init failed: {res.stderr}")
    # SOUL.md del progetto (soul.show/update) con le sezioni attese
    (project / "SOUL.md").write_text(
        "# SOUL\n\n## Preferences\n\n## Memorable feedback\n\n## Relationship facts\n", encoding="utf-8")
    # profilo utente globale in HOME temporanea (user.read/update)
    home = tmp / "home"
    (home / ".anja").mkdir(parents=True)
    (home / ".anja" / "user.md").write_text("# User\n\n## Gusti e preferenze\n- smoke\n", encoding="utf-8")
    # un session journal per sessions.list/read
    sdir = anjawiki / "wiki" / "sessions" / "2026-09-04"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "120000-cli-claude-ab12.md").write_text(
        "---\nsession_id: 120000-cli-claude-ab12\ndate: 2026-09-04\nagent: claude\nkind: cli\n"
        "messages: 3\n---\n\n## Summary\n\nSmoke session.\n", encoding="utf-8")
    # immagine e un file sorgente per code.search
    (tmp / "pic.png").write_bytes(_PNG)
    (project / "app.py").write_text("def authenticate(user):\n    return user == 'smoke'\n", encoding="utf-8")
    env = {"ANJA_SCOPE": "project", "ANJA_ROOT": str(project),
           "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
           "ANJA_CLAUDE_BIN": str(tmp / "unavailable-reranker"), **cov_env()}
    return project, env


def _rpc(env: dict, calls: list[dict], timeout: int = 90) -> tuple[list[dict], list[str]]:
    """initialize + tools/list + N tools/call. Ritorna (risposte per call, nomi wire del registry)."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "smoke", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}]
    for i, call in enumerate(calls, start=3):
        msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                     "params": {"name": call["name"], "arguments": call.get("args", {})}})
    proc = subprocess.Popen([PYTHON, str(SERVER_PATH)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)
    try:
        out, err = proc.communicate("\n".join(json.dumps(m) for m in msgs) + "\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise RuntimeError(f"server timeout. stderr: {err[-800:]}") from None
    assert proc.returncode == 0, f"server exit={proc.returncode}, python={PYTHON}, stderr={err[-2000:]}"
    responses = {}
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in d:
                responses[d["id"]] = d
    missing = [i + 3 for i in range(len(calls)) if i + 3 not in responses]
    assert not missing, (f"missing response ids={missing}; python={PYTHON}; cwd={Path.cwd()}; "
                         f"root={env['ANJA_ROOT']}; stderr={err[-2000:]}")
    registry = [t["name"] for t in responses[2]["result"]["tools"]]
    return [responses.get(i + 3) for i in range(len(calls))], registry


def _payload(resp: dict | None):
    if not resp or "result" not in resp:
        return None
    content = resp["result"].get("content") or []
    try:
        return json.loads(content[0]["text"])
    except Exception:
        return None


def _verdict(name: str, resp: dict | None) -> tuple[bool, str]:
    if not resp:
        return False, "nessuna risposta"
    if "error" in resp:
        return False, f"errore JSON-RPC: {resp['error']}"
    payload = _payload(resp)
    if isinstance(payload, dict) and "error" in payload:
        err = str(payload["error"])
        if any(k in err.lower() for k in ENV_DEPENDENT.get(name, ())):
            return True, f"errore applicativo atteso: {err[:70]}"
        return False, f"errore applicativo: {err[:120]}"
    return True, "ok"


SKILL_MD = "---\nname: smoke-skill\ndescription: Skill creata dallo smoke test\n---\n\n# Smoke skill\n\nPasso uno.\n"


def _phase_a(tmp: Path, project: Path) -> list[dict]:
    """Seed + tutto ciò che non ha bisogno di id generati."""
    link = "Vedi [[test-concept]] e [[test-source]]."
    return [
        {"name": "wiki.upsert_entity", "args": {"slug": "test-entity", "title": "Test Entity",
                                                "sections": {"Sintesi": f"Entity smoke. {link}"}, "tags": ["smoke"]}},
        {"name": "wiki.upsert_concept", "args": {"slug": "test-concept", "title": "Test Concept",
                                                 "sections": {"Definizione": "Concept smoke."}}},
        {"name": "wiki.upsert_source", "args": {"slug": "test-source", "title": "app.py",
                                                "sections": {"Sintesi": "Source smoke."}, "source_path": "app.py"}},
        {"name": "wiki.upsert_analysis", "args": {"slug": "test-analysis", "title": "Analisi smoke",
                                                  "sections": {"Risposta": "Analysis smoke."},
                                                  "question": "Funziona lo smoke?"}},
        {"name": "wiki.update_overview", "args": {"sections": {"Stato": "Overview aggiornata dallo smoke."}}},
        {"name": "wiki.index_update", "args": {"category": "Entities", "entries": ["- [[test-entity]] — smoke"]}},
        {"name": "wiki.log_append", "args": {"type": "note", "description": "smoke log entry"}},
        {"name": "wiki.read", "args": {"slug": "test-entity"}},
        {"name": "wiki.backlinks", "args": {"slug": "test-concept"}},
        {"name": "wiki.search", "args": {"query": "smoke", "limit": 3}},
        {"name": "wiki.tree", "args": {}},
        {"name": "wiki.stats", "args": {"top_n": 3}},
        {"name": "wiki.lint", "args": {}},
        {"name": "wiki.verify", "args": {"slug": "test-entity", "by": "process:smoke"}},
        {"name": "wiki.rename", "args": {"old_slug": "test-concept", "new_slug": "test-concept-2"}},
        {"name": "wiki.replace_links", "args": {"old": "test-concept", "new": "test-concept-2", "dry_run": True}},
        {"name": "wiki.attach_image", "args": {"slug": "test-entity", "image_path": str(tmp / "pic.png"),
                                               "alt_text": "pixel"}},
        {"name": "wiki.export", "args": {"format": "json"}},
        {"name": "wiki.delete", "args": {"slug": "test-analysis", "confirm": True}},
        {"name": "wiki.embed", "args": {}},
        {"name": "wiki.find_duplicates", "args": {}},
        {"name": "wiki.search_semantic", "args": {"query": "smoke"}},
        {"name": "graph.search_text", "args": {"query": "smoke"}},
        {"name": "graph.semantic_neighbors", "args": {"source": "test-entity"}},
        {"name": "graph.report", "args": {"write": False}},
        {"name": "graph.html", "args": {}},
        {"name": "sessions.list", "args": {"limit": 5}},
        {"name": "sessions.read", "args": {"id": "120000-cli-claude-ab12"}},
        {"name": "sessions.summarize", "args": {"session_id": "does-not-exist-0000"}},
        {"name": "sessions.search_semantic", "args": {"query": "smoke"}},
        {"name": "memory.write", "args": {"content": "Nota smoke.", "title": "smoke"}},
        {"name": "memory.recall", "args": {"topic": "smoke"}},
        {"name": "memory.timeline", "args": {"limit": 5}},
        {"name": "soul.show", "args": {}},
        {"name": "soul.update", "args": {"type": "fact", "content": "smoke fact"}},
        {"name": "user.read", "args": {}},
        {"name": "user.update", "args": {"section": "Gusti e preferenze", "content": "- smoke update"}},
        {"name": "skill.list", "args": {}},
        {"name": "skill.save", "args": {"name": "smoke-skill", "content": SKILL_MD}},
        {"name": "skill.load", "args": {"name": "smoke-skill"}},
        {"name": "skill.read_file", "args": {"name": "smoke-skill", "path": "SKILL.md"}},
        {"name": "skill.patch", "args": {"name": "smoke-skill", "old_string": "Passo uno.", "new_string": "Passo due."}},
        {"name": "skill.history", "args": {"name": "smoke-skill"}},
        {"name": "skill.rollback", "args": {"name": "smoke-skill"}},
        {"name": "skill.edit", "args": {"name": "smoke-skill", "content": SKILL_MD.replace("Passo uno", "Passo tre")}},
        {"name": "skill.write_file", "args": {"name": "smoke-skill", "path": "notes.md", "content": "note"}},
        {"name": "skill.remove_file", "args": {"name": "smoke-skill", "path": "notes.md"}},
        {"name": "skill.delete", "args": {"name": "smoke-skill"}},
        {"name": "code.status", "args": {}},
        {"name": "code.compare_decision", "args": {"decision_path": "SOUL.md", "base_commit": "0" * 40, "paths": ["app.py"]}},
        {"name": "code.inspect", "args": {"path": "app.py", "symbol": "authenticate"}},
        {"name": "code.search", "args": {"query": "authenticate", "smart_level": 1, "limit": 3}},
        {"name": "code.reindex", "args": {"limit": 5}},
        {"name": "roadmap.add", "args": {"title": "smoke task", "priority": "P3"}},
        {"name": "roadmap.add", "args": {"title": "smoke task 2", "priority": "P2"}},
        {"name": "roadmap.list", "args": {}},
    ]


def _task_id(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("id", "task_id"):
        if isinstance(payload.get(key), str):
            return payload[key]
    task = payload.get("task")
    if isinstance(task, dict) and isinstance(task.get("id"), str):
        return task["id"]
    return None


def _phase_b(ids: list[str]) -> list[dict]:
    a, b = ids[0], ids[1]
    return [
        {"name": "roadmap.update", "args": {"id": a, "status": "in_progress", "est": "1h"}},
        {"name": "roadmap.complete", "args": {"id": a, "took": "30m"}},
        {"name": "roadmap.block", "args": {"id": b, "blocker": "smoke blocker"}},
        {"name": "roadmap.archive", "args": {"older_than_days": 0}},
    ]


def run_smoke_tests() -> tuple[int, int, list[str]]:
    tmp = Path(tempfile.mkdtemp(prefix="anja-smoke-"))
    failures: list[str] = []
    total = 0
    try:
        project, env = _setup_project(tmp)
        print(f"[smoke] project: {project}")
        calls_a = _phase_a(tmp, project)
        resp_a, registry = _rpc(env, calls_a)
        ids = [_task_id(_payload(r)) for c, r in zip(calls_a, resp_a) if c["name"] == "roadmap.add"]
        if not all(ids):
            failures.append(f"roadmap.add non ha restituito id: {[_payload(r) for c, r in zip(calls_a, resp_a) if c['name'] == 'roadmap.add']}")
            ids = ["T-000", "T-000"]
        calls_b = _phase_b(ids)
        resp_b, _ = _rpc(env, calls_b)
        called = set()
        for call, resp in list(zip(calls_a, resp_a)) + list(zip(calls_b, resp_b)):
            total += 1
            called.add(call["name"])
            ok, msg = _verdict(call["name"], resp)
            print(f"  {'✓' if ok else '✗'} {call['name']:26s} — {msg}")
            if not ok:
                failures.append(f"{call['name']}: {msg}")
        wire = {n.replace(".", "_") for n in called}
        missing = sorted(set(registry) - wire)
        extra = sorted(wire - set(registry))
        if missing:
            failures.append(f"tool del registry MAI chiamati: {missing}")
        if extra:
            failures.append(f"chiamati tool non nel registry: {extra}")
        print(f"[smoke] copertura: {len(wire & set(registry))}/{len(registry)} tool del registry")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return total - len([f for f in failures if not f.startswith("tool del") and not f.startswith("chiamati")]), total, failures


def test_mcp_smoke():
    passed, total, failures = run_smoke_tests()
    assert not failures, "\n".join(failures)


if __name__ == "__main__":
    passed, total, failures = run_smoke_tests()
    print()
    for f in failures:
        print(f"FAIL: {f}")
    print(f"[smoke] {passed}/{total} call ok, {len(failures)} problemi")
    sys.exit(1 if failures else 0)
