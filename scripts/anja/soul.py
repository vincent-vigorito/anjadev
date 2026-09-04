"""Gruppo `soul`: SOUL.md dell'agent."""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import ROOT, SCRIPTS_DIR, log_exc


def _soul_path() -> Path:
    return ROOT / "SOUL.md"


def tool_soul_show(args: dict) -> dict:
    """Read full SOUL.md content."""
    sp = _soul_path()
    if not sp.is_file():
        return {"error": f"SOUL.md not found at {sp}"}
    return {"path": str(sp.relative_to(ROOT)), "content": sp.read_text(encoding="utf-8")}


def tool_soul_update(args: dict) -> dict:
    """Append entry in sezione SOUL.md.

    args:
      type: 'feedback' | 'preference' | 'fact'  (preferenza positiva: 'preference-pos', negativa: 'preference-neg')
      content: str
    """
    entry_type = (args.get("type") or "feedback").lower()
    content = (args.get("content") or "").strip()
    if not content:
        return {"error": "content required"}

    sp = _soul_path()
    if not sp.is_file():
        return {"error": f"SOUL.md not found at {sp}"}

    # Mapping type → section + line format
    today = datetime.now().strftime("%Y-%m-%d")
    SECTION_MAP = {
        "feedback":       ("## Memorable feedback", f"- [{today}] {content}"),
        "preference":     ("## Preferences",         f"- [{today}] {content}"),
        "preference-pos": ("## Preferences",         f"- ✅ {content}"),
        "preference-neg": ("## Preferences",         f"- ❌ {content}"),
        "fact":           ("## Relationship facts",  f"- {content}"),
    }
    if entry_type not in SECTION_MAP:
        return {"error": f"invalid type '{entry_type}'. Allowed: {list(SECTION_MAP)}"}

    section_header, new_line = SECTION_MAP[entry_type]
    text = sp.read_text(encoding="utf-8")
    lines = text.split("\n")

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

    # Insert prima del prossimo "## " header (o a fine file)
    insert_at = len(lines)
    for j in range(idx + 1, len(lines)):
        if lines[j].startswith("## "):
            insert_at = j
            break

    # Trova ultima riga non vuota della sezione per inserire dopo
    last_content = idx + 1
    for j in range(idx + 1, insert_at):
        if lines[j].strip():
            last_content = j + 1

    lines.insert(last_content, new_line)
    sp.write_text("\n".join(lines), encoding="utf-8")

    # update frontmatter `updated:`
    text2 = sp.read_text(encoding="utf-8")
    text2 = re.sub(r"^updated:\s*.*$", f"updated: {today}", text2, count=1, flags=re.M)
    sp.write_text(text2, encoding="utf-8")

    # Trigger compose_claude_md.py + cc_memory_sync (best-effort)
    _trigger_post_soul_update()

    return {
        "path": str(sp.relative_to(ROOT)),
        "section": section_header,
        "added_line": new_line,
        "status": "updated",
    }


def _trigger_post_soul_update():
    """Best-effort: dopo soul.update, rigenera CLAUDE.md e sync CC memory."""
    here = SCRIPTS_DIR / "mcp_memory_server.py"
    scripts_dir = here.parent
    for script_name, args in (
        ("compose_claude_md.py", ["--target", str(ROOT), "--quiet"]),
        ("cc_memory_sync.py", ["--target", str(ROOT), "--quiet"]),
    ):
        script = scripts_dir / script_name
        if not script.is_file():
            continue
        try:
            subprocess.run(
                [sys.executable, str(script)] + args,
                check=False, capture_output=True, timeout=8,
            )
        except Exception as _exc:
            log_exc("soul._trigger_post_soul_update", _exc)
            pass


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "soul.show",
        "group": "soul",
        "description": "Read SOUL.md (identity + user preferences + memorable feedback + relationship facts).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "soul.update",
        "group": "soul",
        "description": "Append una entry a SOUL.md. type=feedback|preference|preference-pos|preference-neg|fact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["feedback", "preference", "preference-pos", "preference-neg", "fact"]},
                "content": {"type": "string", "description": "Testo della entry"},
            },
            "required": ["type", "content"],
        },
    },
]

