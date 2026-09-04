#!/usr/bin/env python3
"""gen_tools_doc.py — sezione "MCP tools" del README generata dal registry (PIANO.md 1.4).

Il registry (`TOOLS` in mcp_memory_server.py) è l'unica fonte: gruppi, conteggi e
descrizioni nel README ne discendono, così non vanno in deriva.

Uso:
  python3 scripts/gen_tools_doc.py            # stampa il blocco generato
  python3 scripts/gen_tools_doc.py --write    # riscrive il blocco tra i marker nel README
  python3 scripts/gen_tools_doc.py --check    # exit 1 se il README è diverso dal generato (CI)

Marker nel README:  <!-- anja:tools:start -->  ...  <!-- anja:tools:end -->
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SERVER = PLUGIN / "scripts" / "mcp_memory_server.py"
README = PLUGIN / "README.md"
START = "<!-- anja:tools:start -->"
END = "<!-- anja:tools:end -->"
DESC_MAX = 140


def load_registry():
    """Importa il server con env minimo (root temporanea, nessun filtro gruppi)."""
    saved = {k: os.environ.get(k) for k in ("ANJA_SCOPE", "ANJA_ROOT", "ANJA_TOOL_GROUPS")}
    tmp = tempfile.mkdtemp(prefix="anja-gen-")
    os.environ["ANJA_SCOPE"] = "project"
    os.environ["ANJA_ROOT"] = tmp
    os.environ["ANJA_TOOL_GROUPS"] = ""
    try:
        spec = importlib.util.spec_from_file_location("anja_memory_server_for_docs", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return mod


def _short(desc: str) -> str:
    """Prima frase della description, una riga, pipe escapate, max DESC_MAX char."""
    d = " ".join(desc.split())
    m = re.match(r"(.+?[.!?])(\s|$)", d)
    if m and len(m.group(1)) >= 20:
        d = m.group(1)
    if len(d) > DESC_MAX:
        d = d[:DESC_MAX - 1].rstrip() + "…"
    # pipe (tabella) e angolari (GitHub li tratterebbe come tag HTML) escapati
    return d.replace("|", "\\|").replace("<", "\\<").replace(">", "\\>")


def render(mod=None) -> str:
    mod = mod or load_registry()
    by_name = {t["name"]: t for t in mod.TOOLS}
    groups = mod.TOOL_GROUPS
    total = sum(len(v) for v in groups.values())
    lines = [
        START,
        f"## MCP tools ({total} totali via `mcp_memory_server`)",
        "",
        f"Esposti via stdio, filtrabili via env `ANJA_TOOL_GROUPS` ({len(groups)} gruppi: "
        + ", ".join(f"`{g}`" for g in groups) + "). Sezione generata da `scripts/gen_tools_doc.py`"
        " dal registry del server: non editare a mano.",
        "",
    ]
    for g, names in groups.items():
        lines.append(f"### Gruppo `{g}` ({len(names)} tool)")
        lines.append("")
        lines.append("| Tool | Descrizione |")
        lines.append("|------|-------------|")
        for n in names:
            lines.append(f"| `{n}` | {_short(by_name[n]['description'])} |")
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def current_block(text: str) -> tuple[int, int] | None:
    a = text.find(START)
    b = text.find(END)
    if a < 0 or b < 0 or b < a:
        return None
    return a, b + len(END)


def main(argv: list[str]) -> int:
    generated = render()
    if "--write" in argv or "--check" in argv:
        text = README.read_text(encoding="utf-8")
        span = current_block(text)
        if span is None:
            print(f"ERRORE: marker {START} / {END} non trovati in README.md", file=sys.stderr)
            return 2
        old = text[span[0]:span[1]]
        if "--check" in argv:
            if old == generated:
                print("README: sezione MCP tools allineata al registry")
                return 0
            print("ERRORE: README.md sezione MCP tools NON allineata al registry — "
                  "esegui: python3 scripts/gen_tools_doc.py --write", file=sys.stderr)
            return 1
        README.write_text(text[:span[0]] + generated + text[span[1]:], encoding="utf-8")
        print(f"README.md aggiornato ({generated.count(chr(10))} righe)")
        return 0
    print(generated)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
