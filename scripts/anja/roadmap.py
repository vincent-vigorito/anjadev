"""Gruppo `roadmap`: task del 4° file speciale roadmap.md."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .common import _wiki_root
from .config import ROOT, SCRIPTS_DIR, log_exc


def _roadmap_module():
    """Lazy-load roadmap_io dal medesimo dir di questo file."""
    try:
        import sys as _sys
        here = SCRIPTS_DIR
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import roadmap_io  # noqa
        return roadmap_io
    except Exception as _exc:
        log_exc("roadmap._roadmap_module", _exc)
        return None


def _roadmap_path() -> Path:
    return _wiki_root() / "roadmap.md"


def tool_roadmap_list(args: dict) -> dict:
    """Lista task filtrati. Filters: status, priority, owner."""
    rio = _roadmap_module()
    if not rio:
        return {"error": "roadmap_io not available"}
    path = _roadmap_path()
    data = rio.parse_roadmap(path)
    status = args.get("status")
    priority = args.get("priority")
    owner = args.get("owner")
    tasks = rio.list_tasks(data, status=status, priority=priority, owner=owner)
    counts = {s: 0 for s in rio.VALID_STATUS}
    for sec_tasks in data["sections"].values():
        for t in sec_tasks:
            counts[t.get("status", "open")] = counts.get(t.get("status", "open"), 0) + 1
    return {
        "path": str(path.relative_to(ROOT)) if path.exists() else None,
        "tasks": tasks,
        "count": len(tasks),
        "summary": counts,
    }


def tool_roadmap_add(args: dict) -> dict:
    """Aggiungi task nuovo in stato open.

    args: title (req), priority (opt: P0|P1|P2|P3), est (opt), owner (opt)
    """
    rio = _roadmap_module()
    if not rio:
        return {"error": "roadmap_io not available"}
    title = (args.get("title") or "").strip()
    if not title:
        return {"error": "title required"}
    priority = args.get("priority")
    if priority and priority not in rio.VALID_PRIORITY:
        return {"error": f"priority must be one of {rio.VALID_PRIORITY}"}

    path = _roadmap_path()
    data = rio.parse_roadmap(path)
    task = {
        "title": title,
        "status": "open",
        "added": rio._today_iso(),
    }
    if priority:
        task["priority"] = priority
    if args.get("est"):
        task["est"] = args["est"]
    if args.get("owner"):
        task["owner"] = args["owner"]

    # Assign unique id
    existing = {t.get("id") for sec in data["sections"].values() for t in sec}
    task["id"] = rio._assign_id(task, existing)

    data["sections"].setdefault("Open", []).append(task)
    rio.write_roadmap(path, data)
    return {
        "id": task["id"],
        "task": task,
        "path": str(path.relative_to(ROOT)),
        "action": "added",
    }


def tool_roadmap_update(args: dict) -> dict:
    """Modifica metadata di un task per id. Supporta:
    title, priority, status, est, owner, added, started, done, took, blocker.

    Se `status` cambia, sposta automaticamente il task nella sezione canonica.
    """
    rio = _roadmap_module()
    if not rio:
        return {"error": "roadmap_io not available"}
    task_id = (args.get("id") or "").strip()
    if not task_id:
        return {"error": "id required"}

    path = _roadmap_path()
    data = rio.parse_roadmap(path)
    sec_name, idx = rio.find_task(data["sections"], task_id)
    if sec_name is None:
        return {"error": f"task not found: {task_id}"}

    task = data["sections"][sec_name][idx]
    changes = {}
    for field in ("title", "priority", "est", "owner", "added", "started", "done", "took", "blocker"):
        if field in args and args[field] is not None:
            task[field] = args[field]
            changes[field] = args[field]

    new_status = args.get("status")
    if new_status:
        if new_status not in rio.VALID_STATUS:
            return {"error": f"status must be one of {rio.VALID_STATUS}"}
        task["status"] = new_status
        changes["status"] = new_status
        target_section = rio.SECTION_FOR_STATUS.get(new_status, sec_name)
        if target_section != sec_name:
            rio.move_task_to_section(data["sections"], sec_name, idx, target_section)

    if not changes:
        return {"error": "no changes provided"}

    rio.write_roadmap(path, data)
    return {"id": task_id, "changes": changes, "path": str(path.relative_to(ROOT))}


def tool_roadmap_complete(args: dict) -> dict:
    """Shortcut: marca done un task. Setta status=done, done=today, took (opt)."""
    rio = _roadmap_module()
    if not rio:
        return {"error": "roadmap_io not available"}
    task_id = (args.get("id") or "").strip()
    if not task_id:
        return {"error": "id required"}

    path = _roadmap_path()
    data = rio.parse_roadmap(path)
    sec_name, idx = rio.find_task(data["sections"], task_id)
    if sec_name is None:
        return {"error": f"task not found: {task_id}"}

    task = data["sections"][sec_name][idx]
    task["status"] = "done"
    task["done"] = rio._today_iso()
    if args.get("took"):
        task["took"] = args["took"]

    rio.move_task_to_section(data["sections"], sec_name, idx, "Done")
    rio.write_roadmap(path, data)
    return {"id": task_id, "action": "completed", "took": task.get("took"), "path": str(path.relative_to(ROOT))}


def tool_roadmap_block(args: dict) -> dict:
    """Shortcut: marca blocked un task. Setta status=blocked, blocker=<reason>."""
    rio = _roadmap_module()
    if not rio:
        return {"error": "roadmap_io not available"}
    task_id = (args.get("id") or "").strip()
    blocker = (args.get("blocker") or "").strip()
    if not task_id or not blocker:
        return {"error": "id and blocker required"}

    path = _roadmap_path()
    data = rio.parse_roadmap(path)
    sec_name, idx = rio.find_task(data["sections"], task_id)
    if sec_name is None:
        return {"error": f"task not found: {task_id}"}

    task = data["sections"][sec_name][idx]
    task["status"] = "blocked"
    task["blocker"] = blocker

    rio.move_task_to_section(data["sections"], sec_name, idx, "Blocked")
    rio.write_roadmap(path, data)
    return {"id": task_id, "action": "blocked", "blocker": blocker, "path": str(path.relative_to(ROOT))}


def tool_roadmap_archive(args: dict) -> dict:
    """Archivia task done più vecchi di N giorni in `wiki/archive/roadmap-YYYY-Q.md`.

    Per ora: solo rimozione dalla Done section (l'archive file è creato append-only
    se vuoi tenere traccia — semplice scelta: write archive file per quarter).
    """
    rio = _roadmap_module()
    if not rio:
        return {"error": "roadmap_io not available"}
    days = int(args.get("older_than_days", 30))

    path = _roadmap_path()
    if not path.is_file():
        return {"archived": 0, "note": "no roadmap.md"}
    data = rio.parse_roadmap(path)

    # Estrai i task che verranno archiviati prima di rimuoverli
    cutoff = datetime.now().astimezone().date() - timedelta(days=days)
    done_section = data["sections"].get("Done", [])
    to_archive = []
    for t in done_section:
        done_str = t.get("done", "")
        try:
            done_date = datetime.fromisoformat(done_str).date()
            if done_date < cutoff:
                to_archive.append(t)
        except Exception as _exc:
            log_exc("roadmap.tool_roadmap_archive", _exc)
            pass

    if not to_archive:
        return {"archived": 0, "older_than_days": days}

    # Scrivi archive file per quarter (YYYY-Q1, Q2, Q3, Q4)
    now = datetime.now().astimezone().date()
    quarter = (now.month - 1) // 3 + 1
    archive_dir = _wiki_root() / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / f"roadmap-{now.year}-Q{quarter}.md"

    if archive_file.is_file():
        existing = archive_file.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"
    else:
        existing = f"# Roadmap archive {now.year} Q{quarter}\n\n"

    new_lines = [existing.rstrip(), "", f"## Archived {rio._today_iso()}", ""]
    for t in to_archive:
        new_lines.append(rio.task_to_line(t))
    archive_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Rimuovi dalla Done section
    rio.archive_done(data, older_than_days=days)
    rio.write_roadmap(path, data)

    return {
        "archived": len(to_archive),
        "archive_file": str(archive_file.relative_to(ROOT)),
        "older_than_days": days,
    }


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "roadmap.list",
        "group": "roadmap",
        "description": (
            "📋 ROADMAP: lista task del progetto da `wiki/roadmap.md`. Filtra per "
            "status/priority/owner. Restituisce list + summary count per status. "
            "USE per 'che cosa c'è da fare', 'cosa è in-progress', 'task aperti P0'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "in_progress", "done", "blocked", "cancelled"]},
                "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                "owner": {"type": "string"},
            },
        },
    },
    {
        "name": "roadmap.add",
        "group": "roadmap",
        "description": (
            "📋 ROADMAP: aggiungi nuovo task in stato open. ID auto-generato come "
            "slug del title. USE quando l'utente dice 'aggiungi task X', 'metti in "
            "roadmap Y', 'da fare Z'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titolo del task (1 riga)"},
                "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"], "description": "P0 critico, P1 importante, P2 nice, P3 idea"},
                "est": {"type": "string", "description": "Stima effort (es. '15min', '2h', '~5h')"},
                "owner": {"type": "string", "description": "Chi (anja, vincent, agent name)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "roadmap.update",
        "group": "roadmap",
        "description": (
            "📋 ROADMAP: modifica metadata di un task per id. Se cambia `status` "
            "sposta auto nella sezione canonica (Open/Done/Blocked). USE per "
            "'segna in-progress', 'cambia priorità', 'aggiungi owner', ecc."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Task id (slug)"},
                "title": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "in_progress", "done", "blocked", "cancelled"]},
                "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                "est": {"type": "string"},
                "owner": {"type": "string"},
                "added": {"type": "string"},
                "started": {"type": "string", "description": "ISO date inizio lavoro"},
                "done": {"type": "string", "description": "ISO date completamento"},
                "took": {"type": "string", "description": "Effort actual (es. '3h')"},
                "blocker": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "roadmap.complete",
        "group": "roadmap",
        "description": (
            "📋 ROADMAP: shortcut completion. Setta status=done, done=today, "
            "took opzionale, sposta in Done. USE per 'task X done', 'completato Y'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "took": {"type": "string", "description": "Effort actual (opt)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "roadmap.block",
        "group": "roadmap",
        "description": (
            "📋 ROADMAP: shortcut blocking. Setta status=blocked + blocker=<reason>, "
            "sposta in Blocked. USE per 'task X bloccato da Y'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "blocker": {"type": "string", "description": "Reason / dipendenza che blocca"},
            },
            "required": ["id", "blocker"],
        },
    },
    {
        "name": "roadmap.archive",
        "group": "roadmap",
        "description": (
            "📋 ROADMAP: archivia task done più vecchi di N giorni (default 30) in "
            "`wiki/archive/roadmap-YYYY-QN.md`. Mantiene Done section snella. "
            "Run periodico, es. 1x/mese."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "older_than_days": {"type": "integer", "default": 30},
            },
        },
    },
]

