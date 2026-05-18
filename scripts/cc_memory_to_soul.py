#!/usr/bin/env python3
"""
cc_memory_to_soul.py — assorbe i file CC native auto-memory in SOUL.md.

CC ha un suo sistema di "auto-memory" che scrive file in:
  ~/.claude/projects/<encoded>/memory/<name>.md

con frontmatter `name/description/type/originSessionId`. Questi file sono CC-only
(non agnostici cross-tool). Per fare di SOUL.md la single source of truth,
dobbiamo:
  1. Identificare i file CC native (esclude anja_*, MEMORY.md)
  2. Mappare type → sezione SOUL.md:
     - type=user        → User profile / Preferences
     - type=feedback    → Memorable feedback
     - type=project     → Relationship facts
     - type=reference   → Relationship facts
  3. Append content in SOUL.md (con marker `<!-- absorbed from CC: <name> -->`)
  4. Cancellare il file CC source
  5. Aggiornare MEMORY.md index rimuovendo le entry assorbite
  6. Re-sync SOUL → CC mirror (anja_*.md aggiornati)

Idempotente: i file `anja_*.md` sono mirror nostri, MAI assorbiti.
Gli anja_*.md vengono ignorati nello scan.

Usage:
    python3 cc_memory_to_soul.py --target <project-root>
    python3 cc_memory_to_soul.py --target <root> --dry-run
    python3 cc_memory_to_soul.py --target <root> --quiet
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# File CC mirror nostri — MAI assorbire (sono managed da cc_memory_sync.py)
ANJA_MIRROR_FILES = {"anja_user.md", "anja_feedback.md", "MEMORY.md"}


def encode_cc_path(project_root: Path) -> str:
    s = str(project_root.resolve())
    return "-" + s.lstrip("/").replace("/", "-")


def cc_memory_dir(project_root: Path) -> Path:
    return Path.home() / ".claude" / "projects" / encode_cc_path(project_root) / "memory"


def parse_cc_file(f: Path) -> dict:
    """Parse CC memory file con frontmatter."""
    info = {"name": f.stem, "description": "", "type": "unknown", "body": "", "file": f.name}
    text = f.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].split("\n"):
                ls = line.strip()
                if ls.startswith("name:"):
                    info["name"] = ls.split(":", 1)[1].strip()
                elif ls.startswith("description:"):
                    info["description"] = ls.split(":", 1)[1].strip()
                elif ls.startswith("type:"):
                    info["type"] = ls.split(":", 1)[1].strip()
            info["body"] = text[end + 4:].lstrip("\n").rstrip()
        else:
            info["body"] = text
    else:
        info["body"] = text
    info["mtime_iso"] = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
    return info


# Mapping type → SOUL section header
TYPE_TO_SECTION = {
    "user": "## User profile",
    "feedback": "## Memorable feedback",
    "preference": "## Preferences",
    "project": "## Relationship facts",
    "reference": "## Relationship facts",
}


def format_absorbed_line(info: dict) -> str:
    """Formatta entry assorbita per inclusione in SOUL.md."""
    name = info["name"]
    desc = info["description"]
    body = info["body"].strip()
    date = info["mtime_iso"]

    if info["type"] == "feedback":
        # Format `- [DATE] <body or desc>`
        oneliner = body.split("\n")[0].strip() if body else desc
        oneliner = oneliner[:280]
        return f"- [{date}] {oneliner}"

    if info["type"] == "user":
        # Sub-section nel User profile
        # Prendiamo il body intero come bullet/note, prefissato con name
        body_clean = body.replace("\n\n", "\n").strip()
        if len(body_clean) > 400:
            body_clean = body_clean[:397] + "…"
        return f"- **{name}** ({date}): {body_clean}"

    # type=project / reference / unknown: relationship fact
    desc_part = f" — {desc}" if desc else ""
    body_short = body.split("\n\n")[0][:300] if body else ""
    body_part = f"\n  {body_short}" if body_short else ""
    return f"- **{name}**{desc_part}{body_part}"


def absorb_into_soul(soul_path: Path, absorbed_by_section: dict, today: str) -> None:
    """Append le entry assorbite nelle sezioni SOUL.md, segnalate con marker."""
    text = soul_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    marker = f"<!-- absorbed from CC native auto-memory: {today} -->"

    for section_header, items in absorbed_by_section.items():
        if not items:
            continue
        # Trova sezione
        try:
            idx = next(i for i, ln in enumerate(lines) if ln.strip() == section_header)
        except StopIteration:
            # Append sezione a fine file
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(section_header)
            lines.append("")
            idx = len(lines) - 1
        # Trova insert point (prima del prossimo "## ")
        insert_at = len(lines)
        for j in range(idx + 1, len(lines)):
            if lines[j].startswith("## "):
                insert_at = j
                break
        # ultima riga non-vuota della sezione
        last_content = idx + 1
        for j in range(idx + 1, insert_at):
            if lines[j].strip():
                last_content = j + 1
        block = ["", marker] + items + [""]
        lines = lines[:last_content] + block + lines[last_content:]

    new_content = "\n".join(lines)
    soul_path.write_text(new_content, encoding="utf-8")

    # Update frontmatter `updated:`
    text2 = soul_path.read_text(encoding="utf-8")
    text2 = re.sub(r"^updated:\s*.*$", f"updated: {today}", text2, count=1, flags=re.M)
    soul_path.write_text(text2, encoding="utf-8")


def update_memory_index(memory_dir: Path, removed_filenames: list, quiet: bool) -> None:
    """Rimuove le entry [name](filename) dei file assorbiti dal MEMORY.md index."""
    index = memory_dir / "MEMORY.md"
    if not index.is_file():
        return
    text = index.read_text(encoding="utf-8")
    lines = text.splitlines()
    new_lines = []
    for line in lines:
        # Match `- [name](filename) — description`
        skip = False
        for fname in removed_filenames:
            stem = fname.replace(".md", "")
            if f"]({fname})" in line or f"]({stem})" in line:
                skip = True
                break
        if not skip:
            new_lines.append(line)
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    index.write_text(new_text, encoding="utf-8")
    if not quiet and len(new_lines) != len(lines):
        print(f"[absorb] MEMORY.md: rimosse {len(lines) - len(new_lines)} entry indice")


def absorb(project_root: Path, dry_run: bool = False, quiet: bool = False) -> int:
    soul_path = project_root / "SOUL.md"
    if not soul_path.is_file():
        if not quiet:
            print(f"[absorb] SOUL.md not found in {project_root}, skip")
        return 0

    cm_dir = cc_memory_dir(project_root)
    if not cm_dir.is_dir():
        return 0

    candidates = []
    for f in sorted(cm_dir.glob("*.md")):
        if f.name in ANJA_MIRROR_FILES:
            continue
        candidates.append(f)

    if not candidates:
        if not quiet:
            print(f"[absorb] no CC native files to absorb in {cm_dir}")
        return 0

    # Group by section
    today = datetime.now().strftime("%Y-%m-%d")
    by_section = {}
    parsed = []
    for f in candidates:
        try:
            info = parse_cc_file(f)
        except Exception as e:
            if not quiet:
                print(f"[absorb] skip {f.name}: {e}", file=sys.stderr)
            continue
        section = TYPE_TO_SECTION.get(info["type"], "## Relationship facts")
        line = format_absorbed_line(info)
        by_section.setdefault(section, []).append(line)
        parsed.append((f, info))

    if not quiet:
        print(f"[absorb] CC native files trovati: {len(parsed)}")
        for f, info in parsed:
            print(f"  - {f.name:30s} type={info['type']:10s} → {TYPE_TO_SECTION.get(info['type'], '## Relationship facts')}")

    if dry_run:
        print(f"\n[dry-run] would absorb {len(parsed)} files into SOUL.md, then delete CC files")
        for sec, items in by_section.items():
            print(f"\n{sec}:")
            for it in items:
                print(f"  {it}")
        return 0

    # Append in SOUL
    absorb_into_soul(soul_path, by_section, today)

    # Cancella i file CC e aggiorna MEMORY.md index
    removed = []
    for f, _ in parsed:
        f.unlink()
        removed.append(f.name)
    update_memory_index(cm_dir, removed, quiet)

    if not quiet:
        print(f"[absorb] ✓ {len(parsed)} file CC assorbiti in SOUL.md, source cancellati")

    # Re-sync SOUL → CC mirror per aggiornare anja_*.md con il nuovo content
    here = Path(__file__).resolve()
    sync_script = here.parent / "cc_memory_sync.py"
    if sync_script.is_file():
        try:
            subprocess.run(
                [sys.executable, str(sync_script), "--target", str(project_root), "--quiet"],
                check=False, capture_output=True, timeout=8,
            )
            if not quiet:
                print("[absorb] ✓ SOUL → CC mirror re-synced")
        except Exception:
            pass

    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--target", required=True, help="project root")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()
    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        sys.exit(f"ERROR: target not found: {target}")
    sys.exit(absorb(target, dry_run=args.dry_run, quiet=args.quiet))


if __name__ == "__main__":
    main()
