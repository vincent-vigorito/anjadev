"""Server MCP `anja_memory`: registry (TOOLS è l'unica fonte di gruppi/handler/nomi wire)
e dispatcher JSON-RPC 2.0 su stdio. I tool vivono in un modulo per dominio (vedi MODULE_ORDER)."""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

from . import code, graph, memory, roadmap, sessions, skills, soul, user, wiki, wiki_io, wiki_maint
from .config import _SECRETS_LOADED, PROTO_VERSION, ROOT, SCOPE, SERVER_NAME, SERVER_VERSION, log

# Ordine dei moduli = ordine dei tool sul wire (tools/list). Un modulo può ospitare tool di
# gruppi diversi (es. wiki.find_duplicates è nel modulo wiki ma nel gruppo graph).
MODULE_ORDER = (memory, sessions, soul, user, skills, wiki, wiki_maint, wiki_io, roadmap, code, graph)

TOOLS: list = [t for m in MODULE_ORDER for t in m.TOOLS]


def _find_handler(fn_name: str):
    for m in MODULE_ORDER:
        fn = getattr(m, fn_name, None)
        if callable(fn):
            return fn
    return None


def reset_env_cache() -> None:
    """Ricalcola il filtro ANJA_TOOL_GROUPS (test che cambiano env fra due import)."""
    global _ALLOWED_CACHE
    _ALLOWED_CACHE = None


# Fase 16 — Tool grouping for env-var-driven filtering.
# Set env ANJA_TOOL_GROUPS=memory,sessions,soul,user (comma-sep) to filter tools/list.
# Default (env vuoto): tutti i gruppi core. I gruppi hub rimossi nel v0.21
# (agents/tasks/workspace/kanban/goals/pp) in ANJA_TOOL_GROUPS → warning, ignorati:
# i .mcp.json vecchi avviano comunque il server (senza quei tool).
# Ordine dei gruppi (docs, warning, tools/list). I membri sono derivati da TOOLS: vedi
# _build_registry() in fondo al file — TOOLS è l'unica fonte di gruppi, handler e nomi wire.
GROUP_ORDER = ("memory", "sessions", "soul", "user", "skills", "wiki", "roadmap", "code", "graph")


_HUB_GROUPS_MOVED = ("agents", "tasks", "workspace", "kanban", "goals", "pp")


_ALLOWED_CACHE: Optional[set] = None


def _allowed_tool_names() -> set:
    """Filter set basato su env ANJA_TOOL_GROUPS (calcolato una volta). Vuoto = tutti i core.
    Gruppo hub spostato o sconosciuto → warning su stderr, ignorato (mai crash)."""
    global _ALLOWED_CACHE
    if _ALLOWED_CACHE is not None:
        return _ALLOWED_CACHE
    raw = os.environ.get("ANJA_TOOL_GROUPS", "").strip()
    if not raw:
        _ALLOWED_CACHE = {t for g in TOOL_GROUPS.values() for t in g}
        return _ALLOWED_CACHE
    names = set()
    for g in [x.strip() for x in raw.split(",") if x.strip()]:
        if g in TOOL_GROUPS:
            names.update(TOOL_GROUPS[g])
        elif g in _HUB_GROUPS_MOVED:
            print(f"[anja_memory] WARN ANJA_TOOL_GROUPS: gruppo '{g}' non vive più in anjadev "
                  f"(v0.21): monta `anja_hub_runtime` (anja-hub/scripts/mcp_hub_runtime.py) — ignorato",
                  file=sys.stderr, flush=True)
        else:
            print(f"[anja_memory] WARN ANJA_TOOL_GROUPS: gruppo '{g}' sconosciuto (ignorato); "
                  f"validi: {','.join(TOOL_GROUPS)}", file=sys.stderr, flush=True)
    _ALLOWED_CACHE = names
    return names


# Nomi sul wire: i tool hanno nomi canonici puntati (`wiki.read`) ma alcuni client
# (Grok Build, OpenAI-style function calling) scartano i nomi con il punto — Claude
# Code li mostra già come `mcp__anja_memory__wiki_read`. tools/list emette la forma
# flat, tools/call accetta entrambe. Registry/handler/gruppi restano canonici.
def _wire_name(name: str) -> str:
    return name.replace(".", "_")


# Handler non deducibili dalla convenzione `tool_<nome flat>`.
_HANDLER_OVERRIDES = {
    "wiki.search": wiki.tool_wiki_search_hybrid,   # tool_wiki_search è la sola-keyword, usata dall'ibrida
}


def _build_registry() -> tuple:
    groups: dict = {g: [] for g in GROUP_ORDER}
    handlers: dict = {}
    by_wire: dict = {}
    problems: list = []
    seen: set = set()
    for t in TOOLS:
        name = t.get("name") or "<senza nome>"
        if name in seen:
            problems.append(f"{name}: nome duplicato")
        seen.add(name)
        group = t.get("group")
        if group not in groups:
            problems.append(f"{name}: gruppo {group!r} non in GROUP_ORDER {GROUP_ORDER}")
        else:
            groups[group].append(name)
        if not isinstance(t.get("inputSchema"), dict) or not t.get("description"):
            problems.append(f"{name}: inputSchema/description mancanti")
        wire = _wire_name(name)
        if wire in by_wire:
            problems.append(f"{name}: nome wire '{wire}' collide con {by_wire[wire]}")
        by_wire[wire] = name
        fn = _HANDLER_OVERRIDES.get(name) or _find_handler("tool_" + wire)
        if not callable(fn):
            problems.append(f"{name}: handler tool_{wire} mancante")
        else:
            handlers[name] = fn
    for g, names in groups.items():
        if not names:
            problems.append(f"gruppo '{g}' vuoto")
    if problems:
        raise RuntimeError("[anja_memory] registry incoerente:\n  " + "\n  ".join(problems))
    return groups, handlers, by_wire


TOOL_GROUPS, TOOL_HANDLERS, _CANONICAL_BY_WIRE = _build_registry()


def _canonical_name(name: str) -> str:
    return _CANONICAL_BY_WIRE.get(name, name)


def handle_request(req: dict) -> dict:
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")

    # initialize
    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTO_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        })

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "tools/list":
        allowed = _allowed_tool_names()
        # `group` è metadato interno del registry: sul wire vanno solo i campi MCP.
        filtered = [{**{k: v for k, v in t.items() if k != "group"}, "name": _wire_name(t["name"])}
                    for t in TOOLS if t["name"] in allowed]
        return _ok(req_id, {"tools": filtered})

    if method == "tools/call":
        name = _canonical_name(params.get("name") or "")
        args = params.get("arguments") or {}
        # Fase 16: rispetta filter group per call (security: client non può chiamare tool nascosti)
        if name not in _allowed_tool_names():
            return _err(req_id, -32601, f"tool '{name}' not available in this server instance")
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return _err(req_id, -32601, f"unknown tool: {name}")
        try:
            result = handler(args)
            # MCP tools/call response format: content array of TextContent
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            return _ok(req_id, {"content": content, "isError": "error" in result})
        except Exception as e:
            log(f"tool '{name}' failed: {type(e).__name__}: {e}", "warn")
            return _err(req_id, -32603, f"tool '{name}' failed: {type(e).__name__}: {e}")

    if method == "ping":
        return _ok(req_id, {})

    return _err(req_id, -32601, f"method not found: {method}")


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def main():
    # Stderr per debug; stdout solo JSON-RPC
    groups_env = os.environ.get("ANJA_TOOL_GROUPS", "")
    active_count = len(_allowed_tool_names())
    print(f"[anja_memory] starting (scope={SCOPE} root={ROOT} "
          f"groups={groups_env or 'ALL'} tools={active_count} "
          f"secrets_loaded={_SECRETS_LOADED})",
          file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = _err(None, -32700, f"parse error: {e}")
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue

        resp = handle_request(req)
        if resp is None:
            continue  # notification, no response
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
