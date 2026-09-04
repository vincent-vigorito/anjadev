#!/usr/bin/env python3
"""Installa anja in un progetto per Antigravity CLI (`agy`): server MCP + lifecycle hook.

Scrive (merge non distruttivo, idempotente) nella cartella `.agents/` del progetto:
  - mcp_config.json  → server `anja_memory` (stesso formato del `.mcp.json` di Claude Code)
  - hooks.json       → hook nominato `anja`: PreInvocation / PostToolUse / Stop →
                       hooks/antigravity_adapter.py (formato piatto agy, validato 1.1.26)

Poi: `agy` dalla root del progetto (in headless: `agy -p ... --add-dir .`, altrimenti agy
non legge `.agents/hooks.json`). Vedi README "Antigravity CLI".
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = PLUGIN_ROOT / "hooks" / "antigravity_adapter.py"
SERVER = PLUGIN_ROOT / "scripts" / "mcp_memory_server.py"
HOOK_NAME = "anja"
DEFAULT_TOOL_GROUPS = "memory,sessions,soul,user,skills,wiki,roadmap,code"


def _command(*args: str) -> str:
    return " ".join(shlex.quote(str(a)) for a in (sys.executable, ADAPTER, *args))


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} non contiene un oggetto JSON")
    return data


def anja_hooks() -> dict:
    return {
        "PreInvocation": [{"type": "command", "command": _command("pre-invocation"), "timeout": 20}],
        "PostToolUse": [{"type": "command", "command": _command("post-tool-use"), "timeout": 10}],
        "Stop": [{"type": "command", "command": _command("stop"), "timeout": 20}],
    }


def install_hooks(project: Path) -> tuple[Path, bool]:
    path = project / ".agents" / "hooks.json"
    data = _load_json(path)
    desired = anja_hooks()
    if data.get(HOOK_NAME) == desired:
        return path, False
    data[HOOK_NAME] = desired          # gli altri hook nominati restano intatti
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, True


def install_mcp(project: Path) -> tuple[Path, bool]:
    path = project / ".agents" / "mcp_config.json"
    data = _load_json(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: mcpServers deve essere un oggetto")
    if "anja_memory" in servers:
        return path, False               # mai sovrascrivere una configurazione utente
    servers["anja_memory"] = {
        "command": sys.executable,
        "args": [str(SERVER)],
        "env": {"ANJA_SCOPE": "project", "ANJA_ROOT": str(project), "ANJA_TOOL_GROUPS": DEFAULT_TOOL_GROUPS},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=".", help="root del progetto (default: cwd)")
    args = ap.parse_args()
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        ap.error(f"project non trovato: {project}")
    if not (project / ".anjawiki").is_dir():
        print(f"[anja] attenzione: {project} non ha .anjawiki/ (fai prima /anja-init)", file=sys.stderr)
    hp, hc = install_hooks(project)
    mp, mc = install_mcp(project)
    print(f"{'aggiornato' if hc else 'già presente'}: {hp}")
    print(f"{'aggiornato' if mc else 'già presente'}: {mp}")
    print("Avvia `agy` dalla root del progetto. In headless: `agy -p \"...\" --add-dir .`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
