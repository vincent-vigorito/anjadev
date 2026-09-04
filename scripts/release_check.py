#!/usr/bin/env python3
"""release_check.py — coerenza di release (PIANO.md 1.5). Exit 1 se qualcosa è in deriva.

Verifica:
  - versione identica in plugin.json, marketplace.json (x2), .codex-plugin/plugin.json,
    README ("**Stato**: vX.Y.Z"), SERVER_VERSION dei due server MCP;
  - CHANGELOG.md ha la voce "## vX.Y.Z" (salta con --skip-changelog: bump.sh gira prima);
  - README sezione MCP tools == generata dal registry (gen_tools_doc --check);
  - ogni "N slash command" / "N MCP tool su G gruppi" / "(N tool, G gruppi)" in README e
    manifest corrisponde ai numeri reali;
  - ogni tests/test_*.py espone almeno una funzione test_* (pytest lo raccoglie).

Uso: python3 scripts/release_check.py [--skip-changelog]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))
import gen_tools_doc  # noqa: E402

problems: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        problems.append(msg)


def main(argv: list[str]) -> int:
    # --- versioni
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((PLUGIN / ".claude-plugin" / "marketplace.json").read_text())
    codex = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    versions = {
        "plugin.json": plugin["version"],
        "marketplace.json": market["version"],
        "marketplace.json[plugins.anja]": market["plugins"][0]["version"],
        ".codex-plugin/plugin.json": codex["version"],
    }
    m = re.search(r"^\*\*Stato\*\*: v(\d+\.\d+\.\d+)", readme, re.M)
    versions["README Stato"] = m.group(1) if m else "<assente>"
    for srv in ("anja/config.py", "mcp_code_server.py"):
        m = re.search(r'^SERVER_VERSION\s*=\s*"([^"]+)"', (PLUGIN / "scripts" / srv).read_text(), re.M)
        versions[srv] = m.group(1) if m else "<assente>"
    ver = plugin["version"]
    check(len(set(versions.values())) == 1, "versioni non allineate: " + json.dumps(versions, indent=2))
    if "--skip-changelog" not in argv:
        changelog = (PLUGIN / "CHANGELOG.md").read_text(encoding="utf-8")
        check(f"## v{ver} " in changelog or f"## v{ver}\n" in changelog, f"CHANGELOG.md senza voce '## v{ver}'")

    # --- numeri reali
    mod = gen_tools_doc.load_registry()
    n_tools = len(mod.TOOLS)
    n_groups = len(mod.TOOL_GROUPS)
    n_cmds = len(list((PLUGIN / "commands").glob("*.md")))

    # --- README sezione tool
    span = gen_tools_doc.current_block(readme)
    check(span is not None, "README.md: marker anja:tools mancanti")
    if span:
        check(readme[span[0]:span[1]] == gen_tools_doc.render(mod),
              "README.md: sezione MCP tools non allineata al registry (gen_tools_doc.py --write)")

    # --- conteggi citati in prosa/manifest
    texts = {"README.md": readme, "plugin.json": json.dumps(plugin, ensure_ascii=False),
             "marketplace.json": json.dumps(market, ensure_ascii=False)}
    for label, txt in texts.items():
        for n in re.findall(r"(\d+) slash command", txt):
            check(int(n) == n_cmds, f"{label}: '{n} slash command' ma i comandi sono {n_cmds}")
        for n, g in re.findall(r"(\d+) MCP tool su (\d+) gruppi", txt):
            check((int(n), int(g)) == (n_tools, n_groups),
                  f"{label}: '{n} MCP tool su {g} gruppi' ma sono {n_tools} su {n_groups}")
        for n, g in re.findall(r"\((\d+) tool, (\d+) gruppi\)", txt):
            check((int(n), int(g)) == (n_tools, n_groups),
                  f"{label}: '({n} tool, {g} gruppi)' ma sono {n_tools} su {n_groups}")

    # --- test raccoglibili
    for f in sorted((PLUGIN / "tests").glob("test_*.py")):
        check(re.search(r"^def test_\w+\(", f.read_text(encoding="utf-8"), re.M) is not None,
              f"{f.name}: nessuna funzione test_* (pytest non lo raccoglie)")

    if problems:
        print("release_check: PROBLEMI")
        for p in problems:
            print("  ✗", p)
        return 1
    print(f"release_check: ok — v{ver}, {n_tools} tool / {n_groups} gruppi, {n_cmds} slash command, "
          f"{len(list((PLUGIN / 'tests').glob('test_*.py')))} file di test")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
