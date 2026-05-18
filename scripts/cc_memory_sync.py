#!/usr/bin/env python3
"""
cc_memory_sync.py — sync unidirezionale SOUL.md → CC memory.

SOUL.md è il source of truth (decisione 1 design doc §12).
CC memory (`~/.claude/projects/<encoded>/memory/MEMORY.md`) è un mirror per non
perdere la memoria quando si lavora con Claude Code direttamente.

Strategia:
- Estrae le sezioni "User profile", "Preferences", "Memorable feedback", "Relationship facts"
  da SOUL.md
- Le scrive in 2 file CC memory (formato CC `name/description/type` frontmatter):
    * `~/.claude/projects/<encoded>/memory/anja_user.md` (type=user) → User profile + Preferences
    * `~/.claude/projects/<encoded>/memory/anja_feedback.md` (type=feedback) → Memorable feedback + Facts
- Aggiorna `MEMORY.md` index aggiungendo i pointer (se mancanti)

Idempotente: ad ogni run riscrive i 2 file con il content corrente di SOUL.md.

Usage:
    python3 cc_memory_sync.py --target <project-root>
    python3 cc_memory_sync.py --target <project-root> --quiet
    python3 cc_memory_sync.py --target <project-root> --dry-run
"""

import argparse
import re
import sys
from pathlib import Path


def encode_cc_path(project_root: Path) -> str:
    """CC encoding: leading '/' → '-', then '/' → '-'."""
    s = str(project_root.resolve())
    return "-" + s.lstrip("/").replace("/", "-")


def cc_memory_dir(project_root: Path) -> Path:
    encoded = encode_cc_path(project_root)
    return Path.home() / ".claude" / "projects" / encoded / "memory"


def parse_soul_sections(soul_text: str) -> dict:
    """Estrai sezioni rilevanti da SOUL.md. Ritorna dict con keys:
    user_profile, preferences, memorable_feedback, relationship_facts.
    """
    sections = {
        "user_profile": "",
        "preferences": "",
        "memorable_feedback": "",
        "relationship_facts": "",
    }
    section_targets = [
        ("## User profile", "user_profile"),
        ("## Preferences", "preferences"),
        ("## Memorable feedback", "memorable_feedback"),
        ("## Relationship facts", "relationship_facts"),
    ]
    for header, key in section_targets:
        # match "## Section\n<content>...\n## next" o EOF
        pattern = rf"^{re.escape(header)}\s*\n(.+?)(?=\n## |\Z)"
        m = re.search(pattern, soul_text, re.M | re.DOTALL)
        if m:
            content = m.group(1).strip()
            # strip HTML comments
            content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL).strip()
            sections[key] = content
    return sections


def render_cc_user_md(sections: dict) -> str:
    user_profile = sections.get("user_profile", "").strip()
    prefs = sections.get("preferences", "").strip()
    body = ""
    if user_profile:
        body += "## User profile\n\n" + user_profile + "\n\n"
    if prefs:
        body += "## Preferences\n\n" + prefs + "\n"
    if not body:
        body = "(synced from SOUL.md — empty sections)\n"
    return (
        "---\n"
        "name: anja_user\n"
        'description: User profile + preferences synced from SOUL.md (anja cc_memory_sync)\n'
        "type: user\n"
        "---\n\n"
        + body
    )


def render_cc_feedback_md(sections: dict) -> str:
    feedback = sections.get("memorable_feedback", "").strip()
    facts = sections.get("relationship_facts", "").strip()
    body = ""
    if feedback:
        body += "## Memorable feedback\n\n" + feedback + "\n\n"
    if facts:
        body += "## Relationship facts\n\n" + facts + "\n"
    if not body:
        body = "(synced from SOUL.md — empty sections)\n"
    return (
        "---\n"
        "name: anja_feedback\n"
        'description: Memorable feedback + relationship facts synced from SOUL.md (anja cc_memory_sync)\n'
        "type: feedback\n"
        "---\n\n"
        + body
    )


def update_memory_index(memory_dir: Path) -> None:
    """Assicura che `MEMORY.md` index citi i 2 file anja. Append-only se già citato."""
    index = memory_dir / "MEMORY.md"
    anja_lines = [
        "- [anja_user](anja_user.md) — User profile + preferences synced from SOUL.md",
        "- [anja_feedback](anja_feedback.md) — Memorable feedback + facts synced from SOUL.md",
    ]
    existing = ""
    if index.is_file():
        existing = index.read_text(encoding="utf-8")

    new_lines = []
    for line in anja_lines:
        if line not in existing:
            new_lines.append(line)
    if not new_lines:
        return
    # append a fine file
    out = existing.rstrip() + "\n" if existing else ""
    out += "\n".join(new_lines) + "\n"
    index.write_text(out, encoding="utf-8")


def sync(project_root: Path, dry_run: bool = False, quiet: bool = False) -> int:
    soul_path = project_root / "SOUL.md"
    if not soul_path.is_file():
        if not quiet:
            print(f"[cc_memory_sync] SOUL.md not found in {project_root}, skip", file=sys.stderr)
        return 0

    soul_text = soul_path.read_text(encoding="utf-8")
    sections = parse_soul_sections(soul_text)

    user_md = render_cc_user_md(sections)
    feedback_md = render_cc_feedback_md(sections)

    memory_dir = cc_memory_dir(project_root)
    user_target = memory_dir / "anja_user.md"
    feedback_target = memory_dir / "anja_feedback.md"

    if dry_run:
        print(f"[dry-run] would write:")
        print(f"  - {user_target} ({len(user_md)} bytes)")
        print(f"  - {feedback_target} ({len(feedback_md)} bytes)")
        return 0

    memory_dir.mkdir(parents=True, exist_ok=True)
    user_target.write_text(user_md, encoding="utf-8")
    feedback_target.write_text(feedback_md, encoding="utf-8")
    update_memory_index(memory_dir)

    if not quiet:
        rel = memory_dir.relative_to(Path.home())
        print(f"[cc_memory_sync] ✓ ~/{rel}/anja_user.md + anja_feedback.md synced from SOUL.md")
    return 0


def main():
    p = argparse.ArgumentParser(description="Sync SOUL.md → CC memory (mirror unidirezionale)")
    p.add_argument("--target", required=True, help="project root path")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()
    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        sys.exit(f"ERROR: target not found: {target}")
    sys.exit(sync(target, dry_run=args.dry_run, quiet=args.quiet))


if __name__ == "__main__":
    main()
