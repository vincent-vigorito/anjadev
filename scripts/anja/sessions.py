"""Gruppo `sessions`: journal delle sessioni."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .common import _sessions_root
from .config import ROOT, SCRIPTS_DIR, log_exc


def tool_sessions_list(args: dict) -> dict:
    """Lista session files dal filesystem.

    Supporta entrambi i layout:
    - Legacy: wiki/sessions/<date>.md (file-per-day)
    - Target (M-Mem 2+): wiki/sessions/<date>/<HHMMSS-kind-agent-hash>.md
    """
    limit = int(args.get("limit", 20))
    include_archived = bool(args.get("include_archived", False))
    sessions_root = _sessions_root()
    if not sessions_root.is_dir():
        return {"sessions": []}

    entries = []
    # File-per-session (target schema). `archive/` (compact: stub + transcript_path)
    # fuori di default — sessions.read per id li trova comunque (rglob).
    date_dirs = [d for d in sorted(sessions_root.iterdir(), reverse=True) if d.is_dir() and d.name != "archive"]
    if include_archived and (sessions_root / "archive").is_dir():
        date_dirs += [d for d in sorted((sessions_root / "archive").iterdir(), reverse=True) if d.is_dir()]
    for date_dir in date_dirs:
        for f in sorted(date_dir.glob("*.md"), reverse=True):
            entries.append(_parse_session_file(f, date_dir.name))
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break

    # Legacy file-per-day (top level)
    if len(entries) < limit:
        for f in sorted(sessions_root.glob("*.md"), reverse=True):
            if f.name == "index.md":
                continue
            entries.append(_parse_session_file(f, f.stem))
            if len(entries) >= limit:
                break

    return {"sessions": entries, "count": len(entries)}


def _parse_session_file(f: Path, date_hint: str) -> dict:
    """Estrai metadata + summary breve da un session file."""
    info = {
        "id": f.stem,
        "path": str(f.relative_to(ROOT)),
        "date": date_hint,
        "title": "",
        "summary": "",
    }
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception as _exc:
        log_exc("sessions._parse_session_file", _exc)
        return info

    from session_archive import availability
    info["transcript"] = availability(ROOT, text)
    # frontmatter parse
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].split("\n"):
                ls = line.strip()
                if ls.startswith("agent:"):
                    info["agent"] = ls.split(":", 1)[1].strip()
                elif ls.startswith("scope:"):
                    info["scope"] = ls.split(":", 1)[1].strip()
                elif ls.startswith("started:"):
                    info["started"] = ls.split(":", 1)[1].strip()
                elif ls.startswith("provider:"):
                    info["provider"] = ls.split(":", 1)[1].strip()
            text_body = text[end + 4:]
        else:
            text_body = text
    else:
        text_body = text

    # Summary: estrai sezione ## Summary (target schema) o prime 200 char
    m = re.search(r"^## Summary\s*\n(.+?)(?=\n## |\Z)", text_body, re.M | re.DOTALL)
    if m:
        info["summary"] = m.group(1).strip()[:300]
    else:
        info["summary"] = text_body.strip()[:200].replace("\n", " ")

    return info


def tool_sessions_read(args: dict) -> dict:
    """Read full session content by id (filename stem) or path."""
    sid = args.get("id", "")
    path_arg = args.get("path", "")
    sessions_root = _sessions_root()

    target_file = None
    if path_arg:
        # Confina a sessions_root: senza il check, path='../../.secrets.env' (o un path
        # assoluto, che in pathlib scavalca ROOT) leggerebbe file arbitrari e li
        # restituirebbe nel content (F-Sec-Anjadev-SessionsReadTraversal). Stesso
        # pattern di _validate_workspace_path / skill_read_file.
        candidate = (ROOT / path_arg).resolve()
        try:
            candidate.relative_to(sessions_root.resolve())
        except ValueError:
            return {"error": "path must be inside the sessions directory"}
        if candidate.is_file():
            target_file = candidate
    elif sid:
        # cerca in entrambi i layout
        for f in sessions_root.rglob(f"{sid}*.md"):
            target_file = f
            break

    if not target_file:
        return {"error": f"session not found: id='{sid}' path='{path_arg}'"}

    from session_archive import availability
    text = target_file.read_text(encoding="utf-8", errors="replace")
    return {
        "transcript": availability(ROOT, text),
        "id": target_file.stem,
        "path": str(target_file.relative_to(ROOT)),
        "content": target_file.read_text(encoding="utf-8", errors="replace"),
    }


def tool_sessions_summarize(args: dict) -> dict:
    """Genera l'auto-summary di una sessione delegando a `scripts/summarize_session_bg.py`
    (unico summarizer: harness-agnostico claude/grok/codex via ANJA_SUMMARY_BIN o
    frontmatter `harness:`, injection-wrap, la sessione del summarizer non è un journal).

    args:
      session_id: filename stem del session file (es. '194849-cli-claude-d9e6')
      model:     opzionale, 'haiku'|'sonnet'|'opus' (default 'haiku'; usato da claude)
      force:     opzionale, True per sovrascrivere Summary già popolato
    """
    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return {"error": "session_id required"}
    model = (args.get("model") or "haiku").strip()
    force = bool(args.get("force", False))

    sessions_root = _sessions_root()
    if not sessions_root.is_dir():
        return {"error": f"sessions dir not found: {sessions_root}"}
    target_file: Optional[Path] = None
    for f in sessions_root.rglob(f"{session_id}.md"):
        target_file = f
        break
    if not target_file:
        return {"error": f"session not found: {session_id}"}

    try:
        import importlib.util
        sp = SCRIPTS_DIR / "summarize_session_bg.py"
        spec = importlib.util.spec_from_file_location("summarize_session_bg", sp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return {"error": f"summarizer not loadable: {e}"}
    rc = mod.summarize(target_file, model=model, bin_override=os.environ.get("ANJA_SUMMARY_BIN") or None,
                       force=force)
    if rc != 0:
        return {"error": f"summarizer rc={rc} (vedi <wiki>/.bg-summarize.log; 3=CLI non eseguibile, 4=timeout, 5=output vuoto)"}
    if not mod.LAST_SUMMARY:
        return {"error": "no LLM CLI (claude/grok/codex) in PATH: set ANJA_SUMMARY_BIN", "written": False}
    return {
        "summary": mod.LAST_SUMMARY,
        "session_file": str(target_file.relative_to(ROOT)),
        "written": True,
        "model_used": model,
    }


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "sessions.list",
        "group": "sessions",
        "description": "Lista sessioni recenti (chat + routine) ordinate per data desc. Ogni entry ha id, path, summary breve.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean", "default": False, "description": "Include sessions/archive/ (stub post-compact)"},
                "limit": {"type": "integer", "default": 20, "description": "Numero massimo di sessioni"},
            },
        },
    },
    {
        "name": "sessions.read",
        "group": "sessions",
        "description": "Read full content di una specifica sessione. Specifica `id` (filename stem) o `path` (relativo a root).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Session ID (filename stem)"},
                "path": {"type": "string", "description": "Path relativo, alternativa a id"},
            },
        },
    },
    {
        "name": "sessions.summarize",
        "group": "sessions",
        "description": (
            "📝 Genera auto-summary on-demand per una sessione e lo scrive nella "
            "sezione `## Summary` del session file. Spawn `claude` CLI subprocess "
            "(default model 'haiku' per velocità). Usa quando l'utente chiede "
            "'riassumi la sessione X', 'che è successo nella session Y', o per "
            "popolare batch i session file lasciati col placeholder."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Filename stem (es. '194849-cli-claude-d9e6')"},
                "model": {"type": "string", "enum": ["haiku", "sonnet", "opus"], "default": "haiku", "description": "Modello claude da usare. 'haiku' default per velocità+costo."},
                "force": {"type": "boolean", "default": False, "description": "True per sovrascrivere Summary già popolato (non placeholder)"},
            },
            "required": ["session_id"],
        },
    },
]

