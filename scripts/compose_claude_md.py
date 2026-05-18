#!/usr/bin/env python3
"""
compose_claude_md.py — genera CLAUDE.md concatenando AGENTS.md + SOUL.md + TOOLS.md.

Risolve il problema "CC carica solo CLAUDE.md, non AGENTS.md / @-import non scatta":
  - Source files (editati): AGENTS.md (primary), SOUL.md (via tool MCP), TOOLS.md (auto-gen)
  - Output (auto-generated): CLAUDE.md = concat con header + section delimiters
  - CLAUDE.md ha frontmatter `auto_generated: true` per identificarlo

Gestione vecchio CLAUDE.md preesistente (non auto-generato):
  - Backup in `CLAUDE.original.md` (1 sola volta, se non esiste già)
  - Warning visibile all'utente
  - Sovrascrive con il composed nuovo

Idempotente: ad ogni run riscrive CLAUDE.md con il content corrente di AGENTS+SOUL+TOOLS.

Usage:
    python3 compose_claude_md.py --target <project-or-hub-root>
    python3 compose_claude_md.py --target ... --quiet
    python3 compose_claude_md.py --target ... --dry-run
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path


AUTO_GEN_MARKER = "auto_generated_by: anja/compose_claude_md.py"

CLAUDE_HEADER_TEMPLATE = """---
auto_generated: true
{marker}
sources: [AGENTS.md, SOUL.md, TOOLS.md]
updated: {date}
---

<!--
  AUTO-GENERATED — non editare manualmente.
  Source primary: AGENTS.md (editato dall'utente).
  Source secondary: SOUL.md (preferenze, scritto via tool MCP anja_memory.soul.update),
  TOOLS.md (capabilities, auto-generato da scripts/tools_md.py).

  Per modificare: edita AGENTS.md (project context), invoca soul.update tool (preferenze),
  rigenera TOOLS.md via tools_md.py. Compose viene rieseguito automaticamente.
-->

"""


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    return text[end + 4:].lstrip("\n")


def _is_auto_generated(text: str) -> bool:
    """Detect se un CLAUDE.md è già auto-generato da noi."""
    if not text:
        return False
    head = text[:500]
    return AUTO_GEN_MARKER in head or "auto_generated: true" in head


def _backup_existing(claude_path: Path, quiet: bool = False) -> bool:
    """Se CLAUDE.md esiste e NON è auto-generato, backup in CLAUDE.original.md.
    Ritorna True se ha backuppato, False se non c'era da fare niente."""
    if not claude_path.is_file() and not claude_path.is_symlink():
        return False
    if claude_path.is_symlink():
        # symlink (es. CLAUDE.md → AGENTS.md): rimuove direttamente, niente backup
        claude_path.unlink()
        if not quiet:
            print(f"[compose] removed symlink {claude_path.name} (will be replaced by composed file)")
        return False
    text = claude_path.read_text(encoding="utf-8", errors="replace")
    if _is_auto_generated(text):
        return False  # è già nostro, nessun backup needed
    # vecchio CLAUDE.md utente — backuppa
    backup_path = claude_path.parent / "CLAUDE.original.md"
    if backup_path.exists():
        # già esiste un backup precedente: non sovrascriviamo (preserve user's first backup)
        if not quiet:
            print(f"[compose] CLAUDE.original.md già esiste, lascio invariato. Cancello CLAUDE.md attuale.")
    else:
        claude_path.rename(backup_path)
        if not quiet:
            print(f"[compose] ⚠ vecchio CLAUDE.md backuppato in {backup_path.name}")
        return True
    # cancella il vecchio CLAUDE.md per fare spazio al composed
    if claude_path.exists():
        claude_path.unlink()
    return True


def _read_optional(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def compose(target: Path, dry_run: bool = False, quiet: bool = False) -> int:
    target = target.resolve()
    if not target.is_dir():
        print(f"ERROR: target not found: {target}", file=sys.stderr)
        return 1

    agents_text = _read_optional(target / "AGENTS.md")
    soul_text = _read_optional(target / "SOUL.md")
    tools_text = _read_optional(target / "TOOLS.md")

    if not agents_text and not soul_text and not tools_text:
        if not quiet:
            print(f"[compose] nessuno tra AGENTS.md/SOUL.md/TOOLS.md trovato in {target}, skip")
        return 0

    # build composed body
    parts = [CLAUDE_HEADER_TEMPLATE.format(marker=AUTO_GEN_MARKER, date=date.today().isoformat())]

    if agents_text:
        parts.append("# AGENTS — project / hub context\n")
        parts.append(_strip_frontmatter(agents_text).strip())
        parts.append("\n")

    if soul_text:
        parts.append("\n---\n")
        parts.append("\n# SOUL — identity, preferences, memorable feedback\n")
        parts.append(_strip_frontmatter(soul_text).strip())
        parts.append("\n")

    if tools_text:
        parts.append("\n---\n")
        parts.append("\n# TOOLS — capabilities (auto-generated)\n")
        parts.append(_strip_frontmatter(tools_text).strip())
        parts.append("\n")

    composed = "\n".join(parts)

    claude_path = target / "CLAUDE.md"
    if dry_run:
        print(f"[dry-run] would write {claude_path} ({len(composed)} bytes)")
        if claude_path.exists() and not _is_auto_generated(_read_optional(claude_path)):
            print(f"[dry-run] would BACKUP existing {claude_path.name} → CLAUDE.original.md")
        return 0

    _backup_existing(claude_path, quiet=quiet)
    claude_path.write_text(composed, encoding="utf-8")
    if not quiet:
        rel = claude_path.relative_to(target.parent) if target.parent.exists() else claude_path
        print(f"[compose] ✓ {rel} composed ({len(composed)} bytes from AGENTS+SOUL+TOOLS)")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--target", required=True, help="project root or hub root")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()
    sys.exit(compose(Path(args.target), dry_run=args.dry_run, quiet=args.quiet))


if __name__ == "__main__":
    main()
