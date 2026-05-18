#!/usr/bin/env python3
"""
migrate_cc_memory.py — migra Claude Code memory esistente nel SOUL.md di un progetto.

Pattern: CC memory (`~/.claude/projects/<encoded>/memory/*.md`) viene letta e aggregata
nelle sezioni rilevanti di `<project>/SOUL.md`:

- File di tipo `user` → "User profile" + "Preferences"
- File di tipo `feedback` → "Memorable feedback" (con data)
- File di tipo `project`/`reference` → "Relationship facts"

Non modifica la CC memory (sarà sovrascritta dal sync futuro M-Mem 3).
Mostra diff e chiede conferma prima di toccare SOUL.md (default).

Usage:
    python3 migrate_cc_memory.py --target <project-root>
    python3 migrate_cc_memory.py --target <project-root> --yes   # no confirm
    python3 migrate_cc_memory.py --target <project-root> --dry-run
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


def encode_cc_path(project_root: Path) -> str:
    """CC encoding: leading '/' → '-', then '/' → '-'.
    Es: /Users/vincent/Documents/bybit-mcp-trading → -Users-vincent-Documents-bybit-mcp-trading
    """
    s = str(project_root.resolve())
    return "-" + s.lstrip("/").replace("/", "-")


def cc_memory_dir(project_root: Path) -> Path:
    encoded = encode_cc_path(project_root)
    return Path.home() / ".claude" / "projects" / encoded / "memory"


def parse_cc_memory_file(f: Path) -> dict:
    """Parse frontmatter + body. Frontmatter format:
    ---
    name: ...
    description: ...
    type: user | feedback | project | reference
    originSessionId: ...
    ---
    <body>
    """
    text = f.read_text(encoding="utf-8")
    info = {"name": f.stem, "description": "", "type": "unknown", "body": text, "file": f.name}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            fm = text[3:end]
            for line in fm.split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    info["name"] = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    info["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("type:"):
                    info["type"] = line.split(":", 1)[1].strip()
            info["body"] = text[end + 4:].lstrip("\n")
    # estrai file mtime per la data
    info["mtime_iso"] = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
    return info


def load_cc_memory(project_root: Path) -> list:
    """Ritorna lista di dict {name, description, type, body, file, mtime_iso}."""
    cm_dir = cc_memory_dir(project_root)
    if not cm_dir.is_dir():
        return []
    out = []
    for f in sorted(cm_dir.glob("*.md")):
        if f.name == "MEMORY.md":
            continue  # MEMORY.md è solo l'indice
        try:
            out.append(parse_cc_memory_file(f))
        except Exception as e:
            print(f"WARNING: skip {f.name}: {e}", file=sys.stderr)
    return out


def aggregate_to_soul_sections(cc_entries: list) -> dict:
    """Distribuisce le entries CC nelle sezioni SOUL.md."""
    sections = {
        "user_profile": [],
        "preferences": [],
        "memorable_feedback": [],
        "relationship_facts": [],
    }
    for e in cc_entries:
        t = e["type"]
        line_prefix = f"- [{e['mtime_iso']}]"
        body_short = (e["description"] or e["body"]).strip().replace("\n\n", " — ").replace("\n", " ")
        # Tronca body se troppo lungo per SOUL (tier HOT)
        if len(body_short) > 280:
            body_short = body_short[:277] + "…"

        if t == "user":
            sections["user_profile"].append(f"{line_prefix} **{e['name']}**: {body_short}")
        elif t == "feedback":
            sections["memorable_feedback"].append(f"{line_prefix} {body_short}")
        elif t in ("project", "reference"):
            sections["relationship_facts"].append(f"- **{e['name']}**: {body_short}")
        else:
            sections["memorable_feedback"].append(f"{line_prefix} ({t}) {body_short}")

    return sections


def update_soul_md(soul_path: Path, sections: dict, dry_run: bool = False) -> str:
    """Aggiunge le righe CC nelle sezioni rispettive di SOUL.md.
    Strategia: append-only, sotto le sezioni esistenti.
    Ritorna il nuovo contenuto.
    """
    if not soul_path.is_file():
        raise FileNotFoundError(f"SOUL.md non trovato in {soul_path}. Esegui prima /anja-init.")

    text = soul_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    new_lines = lines.copy()

    # Marker line: data import
    today = datetime.now().strftime("%Y-%m-%d")
    import_marker = f"<!-- imported from CC memory: {today} -->"

    section_targets = [
        ("## User profile", sections["user_profile"]),
        ("## Preferences", sections["preferences"]),
        ("## Memorable feedback", sections["memorable_feedback"]),
        ("## Relationship facts", sections["relationship_facts"]),
    ]

    for header, items in section_targets:
        if not items:
            continue
        try:
            idx = next(i for i, ln in enumerate(new_lines) if ln.strip() == header)
        except StopIteration:
            # sezione mancante: la appendiamo in fondo
            new_lines.append("")
            new_lines.append(header)
            new_lines.append("")
            idx = len(new_lines) - 1
        # Trova fine sezione (prossimo header `## ` o EOF)
        insert_at = len(new_lines)
        for j in range(idx + 1, len(new_lines)):
            if new_lines[j].startswith("## "):
                insert_at = j
                break
        # Inserisci prima del prossimo header
        block = ["", import_marker] + items + [""]
        new_lines = new_lines[:insert_at] + block + new_lines[insert_at:]

    new_content = "\n".join(new_lines)
    if not dry_run:
        soul_path.write_text(new_content, encoding="utf-8")
    return new_content


def show_diff_summary(sections: dict) -> None:
    print("\n=== Migration plan ===")
    for key, items in sections.items():
        title = key.replace("_", " ").title()
        if items:
            print(f"\n## {title} ({len(items)} entries)")
            for ln in items[:5]:
                print(f"  {ln}")
            if len(items) > 5:
                print(f"  … (+{len(items) - 5} more)")
        else:
            print(f"\n## {title}: (nessuna entry)")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--target", required=True, help="project root path")
    p.add_argument("--dry-run", action="store_true", help="non scrive SOUL.md, mostra solo il piano")
    p.add_argument("--yes", "-y", action="store_true", help="skip confirm prompt")
    args = p.parse_args()

    project_root = Path(args.target).expanduser().resolve()
    if not project_root.is_dir():
        sys.exit(f"ERROR: target not found: {project_root}")

    soul_path = project_root / "SOUL.md"
    if not soul_path.is_file():
        sys.exit(f"ERROR: {soul_path} non esiste. Esegui prima /anja-init nel progetto.")

    cm_dir = cc_memory_dir(project_root)
    print(f"Project root: {project_root}")
    print(f"CC memory:    {cm_dir}")
    print(f"SOUL.md:      {soul_path}")

    if not cm_dir.is_dir():
        print(f"\n(Nessuna CC memory trovata in {cm_dir}. Niente da migrare.)")
        return

    entries = load_cc_memory(project_root)
    if not entries:
        print(f"\n(Directory CC memory esiste ma è vuota. Niente da migrare.)")
        return

    print(f"\nTrovate {len(entries)} entries CC memory:")
    for e in entries:
        print(f"  - {e['file']:40s} type={e['type']:10s} name={e['name']}")

    sections = aggregate_to_soul_sections(entries)
    show_diff_summary(sections)

    if args.dry_run:
        print("\n[dry-run] niente scritto. Rilancia senza --dry-run per applicare.")
        return

    if not args.yes:
        ans = input("\nApplicare migration a SOUL.md? [y/N]: ").strip().lower()
        if ans != "y":
            print("Annullato.")
            return

    update_soul_md(soul_path, sections, dry_run=False)
    print(f"\n✓ SOUL.md aggiornato.")
    print(f"  CC memory in {cm_dir} è invariata (sarà sovrascritta dal sync M-Mem 3 quando attivo).")


if __name__ == "__main__":
    main()
