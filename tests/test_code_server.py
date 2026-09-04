#!/usr/bin/env python3
"""anja_code (mcp_code_server.py): sandbox di execute_python sul wire stdio.

Copre: esecuzione normale, timeout (con figli nel process group), cap output, env
scrubbing delle API key, recursion guard, validazione argomenti, cwd temporaneo in
modalità strict. PIANO.md 2.5(d) e Fase 3."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _helpers import cov_env

PLUGIN = Path(__file__).resolve().parents[1]
SERVER = PLUGIN / "scripts" / "mcp_code_server.py"
PY = os.environ.get("ANJA_TEST_PYTHON") or sys.executable
PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def rpc(project: Path, calls: list[dict], env_extra: dict | None = None, timeout: int = 120,
        enabled: bool = True, raw: bool = False):
    env = {"ANJA_SCOPE": "project", "ANJA_ROOT": str(project),
           "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(project.parent), **cov_env()}
    if enabled:
        env["ANJA_CODE_EXEC"] = "1"
    env.update(env_extra or {})
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 1000, "method": "tools/list", "params": {}}]
    for i, c in enumerate(calls, start=2):
        msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                     "params": {"name": "execute_python", "arguments": c}})
    r = subprocess.run([PY, str(SERVER)], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, env=env, timeout=timeout)
    by_id = {}
    for line in r.stdout.splitlines():
        if line.startswith("{"):
            d = json.loads(line)
            by_id[d.get("id")] = d
    if raw:
        return by_id
    out = []
    for i in range(len(calls)):
        resp = by_id.get(i + 2) or {}
        content = (resp.get("result") or {}).get("content") or [{}]
        try:
            out.append(json.loads(content[0].get("text", "{}")))
        except Exception:
            out.append({"error": f"risposta non JSON: {resp}"})
    return out


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="anja-code-"))
    project = tmp / "proj"
    (project / ".anjawiki").mkdir(parents=True)

    print("§0 opt-in ANJA_CODE_EXEC (PIANO 3.4)")
    by = rpc(project, [{"script": "print(1)"}], enabled=False, raw=True)
    check("senza ANJA_CODE_EXEC: tools/list vuoto", by.get(1000, {}).get("result", {}).get("tools") == [], str(by.get(1000))[:200])
    err = by.get(2, {}).get("error", {})
    check("senza ANJA_CODE_EXEC: tools/call rifiutato con hint", err.get("code") == -32601 and "ANJA_CODE_EXEC" in err.get("message", ""), str(err)[:200])
    by = rpc(project, [], raw=True)
    check("con ANJA_CODE_EXEC=1: execute_python esposto", [t["name"] for t in by.get(1000, {}).get("result", {}).get("tools", [])] == ["execute_python"])

    print("§1 esecuzione normale + argomenti")
    r = rpc(project, [
        {"script": "print('hello'); print(2+2)"},
        {"script": ""},
        {"script": "print(1)", "mode": "weird"},
        {"script": "import os; print(os.getcwd())", "mode": "project"},
        {"script": "print('x' * 10)", "timeout": 1},   # clamp a 5s, non errore
    ])
    check("stdout ritorna", r[0].get("ok") is True and r[0].get("stdout", "").strip() == "hello\n4".strip(), str(r[0])[:200])
    check("script vuoto → errore", "error" in r[1])
    check("mode invalido → errore", "error" in r[2])
    check("mode project: cwd = root progetto", r[3].get("stdout", "").strip() == str(project.resolve()), str(r[3])[:200])
    check("timeout sotto il minimo viene clampato, non rifiutato", r[4].get("ok") is True, str(r[4])[:200])

    print("§2 env scrubbing")
    r = rpc(project, [{"script": "import os; print(sorted(k for k in os.environ if 'KEY' in k or 'SECRET' in k or 'TOKEN' in k))"}],
            env_extra={"OPENROUTER_API_KEY": "sk-or-leak", "MY_SECRET": "s", "GITHUB_TOKEN": "t"})
    check("nessuna API key/secret/token nell'env dello script", r[0].get("stdout", "").strip() == "[]", str(r[0])[:200])

    print("§3 recursion guard")
    r = rpc(project, [{"script": "print('execute_python')"}])
    check("token vietati → errore", "recursion" in str(r[0].get("error", "")))

    print("§4 cap output")
    r = rpc(project, [{"script": "import sys; sys.stdout.write('A' * 300_000)", "max_output_kb": 1}])
    check("stdout troncato a ~1KB", r[0].get("stdout_truncated") is True and len(r[0].get("stdout", "")) < 2000, str(r[0])[:120])

    print("§5 timeout con process group (figlio che sopravvive al padre)")
    marker = tmp / "child.pid"
    script = (
        "import subprocess, sys, time, os\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(120)\n"
    )
    t0 = time.monotonic()
    r = rpc(project, [{"script": script, "timeout": 5}])
    elapsed = time.monotonic() - t0
    check("timeout riportato", r[0].get("error") == "timeout" and r[0].get("killed") is True, str(r[0])[:200])
    check("ritorna entro ~10s (non aspetta i 120s)", elapsed < 20, f"{elapsed:.1f}s")
    child_alive = False
    if marker.is_file():
        pid = int(marker.read_text())
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
            child_alive = True
            os.kill(pid, 9)
        except ProcessLookupError:
            child_alive = False
    check("nessun processo orfano dopo il timeout (killpg)", marker.is_file() and not child_alive,
          "figlio ancora vivo" if child_alive else "marker mancante")

    print("§6 strict: cwd temporaneo pulito e rimosso")
    r = rpc(project, [{"script": "import os; print(os.getcwd())", "mode": "strict"}])
    cwd = r[0].get("stdout", "").strip()
    check("strict: cwd è una dir temporanea anja-code-*", "anja-code-" in cwd, cwd)
    check("strict: workspace rimosso a fine call", bool(cwd) and not Path(cwd).exists(), cwd)

    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


def test_code_server():
    main()


if __name__ == "__main__":
    main()
