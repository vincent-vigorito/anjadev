#!/usr/bin/env python3
"""F-AnjadevCoreSplit — contratto di indipendenza di anjadev da AnjaHub.

anjadev è il plugin CLI (memory/sessions/soul/user/skills/wiki/roadmap/code/graph):
NON deve esporre né importare nulla di hub-only (agents/tasks/workspace/kanban/
goals/pp vivono in anja-hub `mcp_hub_runtime.py`). Test di accettazione §5 del
design `anja-anjadev-core-split-design.md`.

Standalone: python3 tests/test_core_split.py   (o pytest)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER = PLUGIN_ROOT / "scripts" / "mcp_memory_server.py"
INIT = PLUGIN_ROOT / "scripts" / "init_project.py"
PYTHON = "/opt/homebrew/opt/python@3.12/bin/python3.12" if Path(
    "/opt/homebrew/opt/python@3.12/bin/python3.12").is_file() else sys.executable

# tools/list emette i nomi flat (wire): `kanban_show`, non `kanban.show`
HUB_PREFIXES = ("agent_", "task_", "workspace_", "kanban_", "goal_", "pp_")
HUB_GROUPS = ("agents", "tasks", "workspace", "kanban", "goals", "pp")
CORE_GROUPS = ("memory", "sessions", "soul", "user", "skills", "wiki", "roadmap", "code")
HUB_IMPORT_RE = re.compile(r"kanban_io|goal_io|workspace_scaffold|pp_integration|ANJA_HUB_WEBAPP|_load_webapp_module|_hub_webapp_path")

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def rpc(project: Path, msgs: list[dict], env_extra: dict | None = None) -> tuple[dict, str]:
    """Server via stdio, env MINIMO (niente ANJA_HUB / ANJA_HUB_WEBAPP: macchina senza AnjaHub)."""
    env = {"ANJA_SCOPE": "project", "ANJA_ROOT": str(project),
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
           "HOME": os.environ.get("HOME", "/tmp")}
    env.update(env_extra or {})
    all_msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}] + msgs
    p = subprocess.run([PYTHON, str(SERVER)], input="\n".join(json.dumps(m) for m in all_msgs) + "\n",
                       capture_output=True, text=True, env=env, timeout=30)
    out = {}
    for line in p.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            d = json.loads(line)
            if "id" in d:
                out[d["id"]] = d
    return out, p.stderr


def tool_result(resp: dict) -> dict:
    if not resp or "result" not in resp:
        return {"error": f"rpc: {resp}"}
    return json.loads(resp["result"]["content"][0]["text"])


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="anja-split-"))
    project = tmp / "proj"
    project.mkdir()
    res = subprocess.run([PYTHON, str(INIT), "--type", "dev", "--mode", "cold",
                          "--target", str(project / ".anjawiki"), "--name", "proj"],
                         capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        print(res.stderr)
        sys.exit(1)

    print("§5.1 /anja-init: .mcp.json solo gruppi core (con roadmap,code), niente hub")
    mcp = json.loads((project / ".mcp.json").read_text())
    env = mcp["mcpServers"]["anja_memory"]["env"]
    groups = [g for g in env.get("ANJA_TOOL_GROUPS", "").split(",") if g]
    check("ANJA_TOOL_GROUPS presente", bool(groups), str(env))
    check("include roadmap e code", "roadmap" in groups and "code" in groups, str(groups))
    check("nessun gruppo hub", not any(g in HUB_GROUPS for g in groups), str(groups))
    check("niente ANJA_HUB_WEBAPP nell'entry", "ANJA_HUB_WEBAPP" not in env, str(env))

    print("§5.2 tools/list scope=project SENZA ANJA_TOOL_GROUPS → solo core")
    out, err = rpc(project, [{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}])
    names = [t["name"] for t in out[2]["result"]["tools"]]
    hub_leak = [n for n in names if n.startswith(HUB_PREFIXES)]
    check(f"nessun tool hub esposto (trovati {len(names)})", not hub_leak, str(hub_leak[:8]))
    check("i core ci sono (wiki_read, memory_recall, roadmap_list, code_status, skill_load — nomi flat sul wire)",
          all(n in names for n in ("wiki_read", "memory_recall", "roadmap_list", "code_status", "skill_load")), str(names[:10]))
    check("nessun punto nei nomi sul wire", not any("." in n for n in names), str([n for n in names if "." in n][:5]))

    print("§5.2b gruppo hub/orfano in ANJA_TOOL_GROUPS → warning stderr, ignorato, server up")
    out, err = rpc(project, [{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}],
                   {"ANJA_TOOL_GROUPS": "memory,kanban,goals"})
    names = [t["name"] for t in out.get(2, {}).get("result", {}).get("tools", [])]
    check("server risponde", 2 in out, str(err[-300:]))
    check("solo memory_*", names and all(n.startswith("memory_") for n in names), str(names))
    check("warning su kanban/goals in stderr", "kanban" in err and "goals" in err and "WARN" in err.upper(), err[-300:])

    print("§5.3 nessun import/sys.path verso anja-hub/webapp in anjadev")
    hits = []
    for f in list((PLUGIN_ROOT / "scripts").glob("*.py")) + list((PLUGIN_ROOT / "hooks").rglob("*")) \
            + list((PLUGIN_ROOT / "commands").rglob("*.md")):
        if f.is_file():
            try:
                txt = f.read_text(encoding="utf-8")
            except Exception:
                continue
            if HUB_IMPORT_RE.search(txt):
                hits.append(f.name)
    check("grep hub-imports = 0", not hits, str(hits))
    src = SERVER.read_text(encoding="utf-8")
    check("TOOL_GROUPS senza gruppi hub",
          not any(f'"{g}":' in src.split("TOOL_GROUPS = {", 1)[1].split("\n}\n", 1)[0] for g in HUB_GROUPS))

    print("§5.4 smoke core senza AnjaHub: roadmap.add + code.status L0 + wiki upsert")
    out, err = rpc(project, [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "roadmap.add", "arguments": {"title": "Test task split", "priority": "P2"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "roadmap.list", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "code.status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "wiki.upsert_concept", "arguments": {"slug": "split-test", "title": "Split test", "sections": {"Summary": "ok"}}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "memory.timeline", "arguments": {"days": 7}}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "user.read", "arguments": {}}},
    ], {"ANJA_TOOL_GROUPS": ",".join(CORE_GROUPS)})
    r = tool_result(out.get(2))
    check("roadmap.add ok", "error" not in r and r.get("id"), str(r)[:200])
    r = tool_result(out.get(3))
    check("roadmap.list vede il task", any("split" in t.get("title", "").lower() for t in r.get("tasks", [])), str(r)[:200])
    r = tool_result(out.get(4))
    check("code.status risponde (anche senza key/index)", isinstance(r, dict) and "rpc:" not in str(r.get("error", "")), str(r)[:200])
    r = tool_result(out.get(5))
    check("wiki.upsert_concept ok", "error" not in r, str(r)[:200])
    r = tool_result(out.get(6))
    check("memory.timeline ok senza kanban/goals", "error" not in r and "events" in r or "entries" in r or isinstance(r, dict) and "error" not in r, str(r)[:200])
    r = tool_result(out.get(7))
    check("user.read: errore graceful o fallback ~/.anja (nessun crash, nessun 'hub' obbligatorio)",
          isinstance(r, dict) and ("content" in r or "hint" in r), str(r)[:200])
    check("nessun riferimento a ~/.anjahub nell'hint",
          ".anjahub" not in json.dumps(r), str(r)[:200])

    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


def test_core_split():
    main()


if __name__ == "__main__":
    main()
