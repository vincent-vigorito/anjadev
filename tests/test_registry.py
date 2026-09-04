#!/usr/bin/env python3
"""Registry MCP verificabile (PIANO.md 1.2).

TOOLS è l'unica fonte: gruppi, handler e nomi wire sono derivati. Questo test
garantisce che restino biunivoci e che il filtro ANJA_TOOL_GROUPS valga sia in
tools/list sia in tools/call. Import in-process del server (env minimo).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SERVER = PLUGIN / "scripts" / "mcp_memory_server.py"
PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def load_server(root: Path, groups: str = ""):
    os.environ["ANJA_SCOPE"] = "project"
    os.environ["ANJA_ROOT"] = str(root)
    os.environ["ANJA_TOOL_GROUPS"] = groups
    spec = importlib.util.spec_from_file_location(f"anja_srv_{abs(hash(groups))}", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call(mod, name: str, args: dict | None = None, req_id: int = 7) -> dict:
    return mod.handle_request({"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                               "params": {"name": name, "arguments": args or {}}})


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="anja-registry-"))
    (tmp / ".anjawiki" / "wiki").mkdir(parents=True)
    m = load_server(tmp)

    print("§1 struttura del registry")
    names = [t["name"] for t in m.TOOLS]
    check("nessun nome duplicato", len(names) == len(set(names)))
    check("ogni tool ha name/group/description/inputSchema",
          all(t.get("name") and t.get("group") and t.get("description")
              and isinstance(t.get("inputSchema"), dict) for t in m.TOOLS))
    check("GROUP_ORDER == chiavi TOOL_GROUPS (stesso ordine)", tuple(m.TOOL_GROUPS) == m.GROUP_ORDER)
    union = [n for g in m.TOOL_GROUPS.values() for n in g]
    check("unione gruppi == TOOLS (nessun tool orfano, nessun gruppo fantasma)",
          sorted(union) == sorted(names), f"{sorted(set(union) ^ set(names))}")
    check("un tool appartiene a un solo gruppo", len(union) == len(set(union)))
    check("nessun gruppo vuoto", all(m.TOOL_GROUPS.values()))
    check("gruppi hub non presenti", not set(m._HUB_GROUPS_MOVED) & set(m.TOOL_GROUPS))

    print("§2 handler")
    check("TOOL_HANDLERS keys == TOOLS", set(m.TOOL_HANDLERS) == set(names))
    check("ogni handler è callable", all(callable(h) for h in m.TOOL_HANDLERS.values()))
    check("override solo per tool esistenti", set(m._HANDLER_OVERRIDES) <= set(names))
    stray = [k for k in vars(m) if k.startswith("tool_") and callable(getattr(m, k))
             and k not in {"tool_" + m._wire_name(n) for n in names}
             and getattr(m, k) not in m._HANDLER_OVERRIDES.values()]
    # tool_wiki_search è la keyword-only usata dall'ibrida: interna, non un tool.
    check("funzioni tool_* senza registrazione (solo interne note)", set(stray) <= {"tool_wiki_search"}, str(stray))

    print("§3 nomi wire")
    wires = [m._wire_name(n) for n in names]
    check("wire biiettivo", len(set(wires)) == len(wires))
    check("_canonical_name(wire) == canonico", all(m._canonical_name(w) == n for w, n in zip(wires, names)))
    check("_canonical_name(canonico) == canonico", all(m._canonical_name(n) == n for n in names))
    check("nessun nome wire contiene il punto", not any("." in w for w in wires))

    print("§4 protocollo")
    lst = m.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    wire_tools = lst["result"]["tools"]
    check("tools/list emette tutti i tool (nessun filtro)", len(wire_tools) == len(names))
    check("tools/list emette nomi flat", {t["name"] for t in wire_tools} == set(wires))
    check("tools/list non emette 'group' (metadato interno)", not any("group" in t for t in wire_tools))
    check("tools/list emette solo campi MCP", all(set(t) <= {"name", "description", "inputSchema"} for t in wire_tools))
    r_dot = _call(m, "wiki.tree")
    r_flat = _call(m, "wiki_tree")
    check("tools/call accetta il canonico", "result" in r_dot, str(r_dot)[:200])
    check("tools/call accetta il flat", "result" in r_flat, str(r_flat)[:200])
    check("stesso risultato nelle due forme", r_dot["result"] == r_flat["result"])
    r_unk = _call(m, "wiki.nope")
    check("tool sconosciuto → -32601", r_unk.get("error", {}).get("code") == -32601)

    print("§5 filtro ANJA_TOOL_GROUPS in list e call")
    m2 = load_server(tmp, "memory,wiki,roadmap")
    lst2 = m2.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    n_expected = sum(len(m2.TOOL_GROUPS[g]) for g in ("memory", "wiki", "roadmap"))
    check(f"tools/list filtrato = {n_expected}", len(lst2["result"]["tools"]) == n_expected,
          str(len(lst2["result"]["tools"])))
    r_hidden = _call(m2, "skill.list")
    check("tool nascosto non chiamabile (canonico)", r_hidden.get("error", {}).get("code") == -32601)
    r_hidden2 = _call(m2, "skill_list")
    check("tool nascosto non chiamabile (flat)", r_hidden2.get("error", {}).get("code") == -32601)
    m3 = load_server(tmp, "wiki,kanban,bogus")
    lst3 = m3.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    check("gruppo hub/sconosciuto ignorato senza crash", len(lst3["result"]["tools"]) == len(m3.TOOL_GROUPS["wiki"]))

    print("§6 registry incoerente → import fallisce")
    bad_src = SERVER.read_text(encoding="utf-8").replace('"group": "roadmap",', '"group": "roadmapp",', 1)
    bad = tmp / "bad_server.py"
    bad.write_text(bad_src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("anja_srv_bad", bad)
    mod = importlib.util.module_from_spec(spec)
    raised = ""
    try:
        spec.loader.exec_module(mod)
    except RuntimeError as e:
        raised = str(e)
    check("gruppo inesistente → RuntimeError con diagnosi", "registry incoerente" in raised and "roadmapp" in raised)

    for k in ("ANJA_TOOL_GROUPS",):
        os.environ.pop(k, None)
    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


def test_registry():
    main()


if __name__ == "__main__":
    main()
