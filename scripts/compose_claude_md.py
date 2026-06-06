#!/usr/bin/env python3
"""
compose_claude_md.py — compone la triade in un unico AGENTS.md cross-harness.

Modello (F-NonCC-ManualMode):
  - Source (editati):  AGENTS.src.md (a mano) · SOUL.md (via soul.update) · TOOLS.md (auto)
  - Output composed:   AGENTS.md = i 3 source uniti INLINE + sezione Bootstrap
                       → letto NATIVO da Codex / Grok / OpenCode (no @import, no hook)
  - Wrapper:           CLAUDE.md = `@AGENTS.md` → Claude Code lo espande

Perché AGENTS.md e non più CLAUDE.md come composed:
  AGENTS.md è lo standard cross-tool. Gli harness diversi da CC NON espandono la
  sintassi @import, quindi il file che leggono deve avere SOUL+TOOLS già inline.
  CC invece non legge AGENTS.md nativamente → lo importa via CLAUDE.md (@AGENTS.md).

Migrazione idempotente:
  se trova un AGENTS.md *source* (non auto-generato) e nessun AGENTS.src.md,
  lo rinomina AGENTS.md → AGENTS.src.md prima di comporre. Run successivi: no-op.

Idempotente: ad ogni run riscrive AGENTS.md + CLAUDE.md dal content corrente dei source.

Usage:
    python3 compose_claude_md.py --target <project-or-hub-root>
    python3 compose_claude_md.py --target ... --quiet
    python3 compose_claude_md.py --target ... --dry-run
"""

import argparse
import sys
from datetime import date
from pathlib import Path


AUTO_GEN_MARKER = "auto_generated_by: anja/compose_claude_md.py"

AGENTS_HEADER_TEMPLATE = """---
auto_generated: true
{marker}
sources: [AGENTS.src.md, SOUL.md, TOOLS.md]
updated: {date}
---

<!--
  AUTO-GENERATED — non editare manualmente.
  File context cross-harness: letto NATIVO da Codex/Grok/OpenCode; Claude Code lo
  importa via CLAUDE.md (@AGENTS.md).
  Per modificare: edita AGENTS.src.md (project context), invoca soul.update
  (preferenze), rigenera TOOLS.md via tools_md.py. Compose viene rieseguito.
-->

"""

CLAUDE_WRAPPER_TEMPLATE = """---
auto_generated: true
{marker}
sources: [AGENTS.md]
updated: {date}
---

<!-- AUTO-GENERATED wrapper. Claude Code legge questo file e via @AGENTS.md espande
     il context composto. NON editare: il context vive in AGENTS.src.md. -->

@AGENTS.md
"""

# Bootstrap per harness senza gli hook di Claude Code (Codex/Grok/...).
# Su CC l'hook session_start stampa "[anja] Sessione aperta" → la condizione è falsa
# e l'agente la ignora; altrove guida il pull di contesto manuale.
BOOTSTRAP_SECTION = """
---

# Bootstrap (harness senza hook anja)

> Se all'avvio **non** vedi un blocco `[anja] Sessione aperta (...)`, il tuo harness
> non ha gli hook anja. Prima di lavorare, fai tu il pull di contesto:
> 1. `roadmap.list` (status open + in_progress) → focus task
> 2. `memory.timeline` → ultime sessioni + log recente
> 3. a fine lavoro: scrivi il journal in `<scope>/sessions/<YYYY-MM-DD>/<id>.md`
>    (i .md sono accessibili bash-native via grep/cat/write — vedi SCHEMA.md)
>
> Su Claude Code è automatico (hook session_start/end): ignora questa sezione.
"""


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    return text[end + 4:].lstrip("\n")


def _strip_at_imports(text: str) -> str:
    """Rimuove le righe `@SOUL.md` / `@TOOLS.md` dal body AGENTS: nel composed il
    loro contenuto è già appeso inline, e gli harness non-CC non espandono @import
    (resterebbe testo morto)."""
    keep = [ln for ln in text.splitlines() if ln.strip() not in ("@SOUL.md", "@TOOLS.md")]
    return "\n".join(keep)


def _is_auto_generated(text: str) -> bool:
    if not text:
        return False
    head = text[:500]
    return AUTO_GEN_MARKER in head or "auto_generated: true" in head


def _read_optional(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _migrate_source(target: Path, quiet: bool = False) -> None:
    """One-shot idempotente: AGENTS.md *source* (non auto-gen) → AGENTS.src.md.

    Skip se AGENTS.src.md esiste già, o se AGENTS.md è il composed auto-generato,
    o se AGENTS.md è un symlink/non esiste.
    """
    src = target / "AGENTS.src.md"
    agents = target / "AGENTS.md"
    if src.exists():
        return
    if not agents.is_file() or agents.is_symlink():
        return
    if _is_auto_generated(agents.read_text(encoding="utf-8", errors="replace")):
        return
    agents.rename(src)
    if not quiet:
        print(f"[compose] migrato source: AGENTS.md → AGENTS.src.md")


def _backup_if_user_file(path: Path, quiet: bool = False) -> None:
    """Se `path` esiste, NON è auto-generato e NON è symlink → backup .original.md
    (1 volta sola). Protegge un eventuale file utente preesistente prima di sovrascrivere."""
    if path.is_symlink():
        path.unlink()
        return
    if not path.is_file():
        return
    if _is_auto_generated(path.read_text(encoding="utf-8", errors="replace")):
        return
    backup = path.with_suffix(".original.md")
    if not backup.exists():
        path.rename(backup)
        if not quiet:
            print(f"[compose] ⚠ {path.name} utente backuppato in {backup.name}")
    elif path.exists():
        path.unlink()


def _write_gemini(target: Path) -> None:
    """Gemini CLI legge GEMINI.md (non AGENTS.md): lo creiamo come symlink → AGENTS.md.
    Fallback a copia del contenuto se i symlink non sono supportati (es. Windows)."""
    gemini = target / "GEMINI.md"
    if gemini.exists() or gemini.is_symlink():
        gemini.unlink()
    try:
        gemini.symlink_to("AGENTS.md")
    except (OSError, NotImplementedError):
        gemini.write_text((target / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8")


def compose(target: Path, dry_run: bool = False, quiet: bool = False) -> int:
    target = target.resolve()
    if not target.is_dir():
        print(f"ERROR: target not found: {target}", file=sys.stderr)
        return 1

    if not dry_run:
        _migrate_source(target, quiet=quiet)

    agents_text = _read_optional(target / "AGENTS.src.md")
    soul_text = _read_optional(target / "SOUL.md")
    tools_text = _read_optional(target / "TOOLS.md")

    if not agents_text and not soul_text and not tools_text:
        if not quiet:
            print(f"[compose] nessuno tra AGENTS.src.md/SOUL.md/TOOLS.md in {target}, skip")
        return 0

    # --- build AGENTS.md composed (inline, no @import) ---
    parts = [AGENTS_HEADER_TEMPLATE.format(marker=AUTO_GEN_MARKER, date=date.today().isoformat())]
    if agents_text:
        parts.append("# AGENTS — project / hub context\n")
        parts.append(_strip_at_imports(_strip_frontmatter(agents_text)).strip())
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
    parts.append(BOOTSTRAP_SECTION)
    composed = "\n".join(parts)

    agents_path = target / "AGENTS.md"
    claude_path = target / "CLAUDE.md"
    wrapper = CLAUDE_WRAPPER_TEMPLATE.format(marker=AUTO_GEN_MARKER, date=date.today().isoformat())

    if dry_run:
        print(f"[dry-run] would write {agents_path} ({len(composed)} bytes) + {claude_path} (wrapper @AGENTS.md)")
        return 0

    _backup_if_user_file(claude_path, quiet=quiet)  # vecchio CLAUDE.md composed/utente
    agents_path.write_text(composed, encoding="utf-8")
    claude_path.write_text(wrapper, encoding="utf-8")
    _write_gemini(target)  # GEMINI.md → AGENTS.md (per Gemini CLI)
    if not quiet:
        print(f"[compose] ✓ AGENTS.md composed ({len(composed)} bytes) + CLAUDE.md (@AGENTS.md) + GEMINI.md (→AGENTS.md)")
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
