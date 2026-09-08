#!/usr/bin/env python3
"""roadmap_io.py — parser/writer per `wiki/roadmap.md` (4° file speciale).

Schema file:
    ---
    title: Roadmap
    type: roadmap
    created: YYYY-MM-DD
    updated: YYYY-MM-DD
    ---

    # Roadmap

    ## Open
    - [ ] (P0) Fix Qty rounding | est: 15min | owner: anja | added: 2026-05-16
    - [~] (P1) Migrate token | started: 2026-05-17 | owner: vincent

    ## Done (last 30 days)
    - [x] F-Rebrand-Finish | done: 2026-05-16 | took: ~3h | owner: anja

    ## Blocked
    - [⏸] (P2) Hub backup | blocker: spec design | owner: vincent

Convenzioni:
- Status checkbox: `[ ]` open · `[~]` in_progress · `[x]` done · `[⏸]` blocked · `[-]` cancelled
- Priority: `P0|P1|P2|P3` (opt). Notation: `(P0)` subito dopo lo status, prima del title.
- Metadata inline dopo `|`: `est:`, `owner:`, `added:`, `started:`, `done:`, `took:`, `blocker:`
- ID opaco persistito come `id: task-<uuid>`; migrazione legacy al primo write con backup.

Sezioni canoniche (ordine): Open, Done, Blocked. Cancelled vanno in Done sezione (con [-]) o saltati.

Stdlib pure, no deps esterne.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

from anja.persistence import (
    MISSING,
    PersistenceError,
    check_revision,
    create_text,
    read_text,
    revision,
    write_many,
)

STATUS_GLYPH = {
    "open": " ",
    "in_progress": "~",
    "done": "x",
    "blocked": "⏸",
    "cancelled": "-",
}
GLYPH_TO_STATUS = {v: k for k, v in STATUS_GLYPH.items()}
VALID_STATUS = tuple(STATUS_GLYPH.keys())
VALID_PRIORITY = ("P0", "P1", "P2", "P3")
SECTION_FOR_STATUS = {
    "open": "Open",
    "in_progress": "Open",
    "done": "Done",
    "blocked": "Blocked",
    "cancelled": "Done",
}
DEFAULT_SECTIONS = ("Open", "Done", "Blocked")

KNOWN_META_KEYS = ("id", "est", "owner", "added", "started", "done", "took", "blocker")

# Splitter title/metadata: cerca il primo `|` seguito da una known key + colon.
# Così `|` dentro al title (es. `wiki.export(format=md|json|html)`) NON è split.
_META_SPLIT_RE = re.compile(r"\s*\|\s*(?=(?:" + "|".join(KNOWN_META_KEYS) + r")\s*:)")

_LINE_PREFIX_RE = re.compile(
    r"^\s*-\s*\[(?P<glyph>[ x~\-⏸])\]\s*"
    r"(?:\((?P<priority>P[0-3])\)\s*)?"
    r"(?P<rest>.+?)\s*$"
)

_META_KV_RE = re.compile(r"(\w+):\s*([^|]+?)(?=\s*\||$)")


def _slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "task"


def _today_iso() -> str:
    return datetime.now().astimezone().date().isoformat()


def parse_line(line: str) -> dict | None:
    """Parse una riga task. Restituisce dict o None se non matcha.

    Split title/metadata solo prima di una `known_key:` per gestire `|`
    embedded nel title (es. `wiki.export(format=md|json|html)`).
    """
    m = _LINE_PREFIX_RE.match(line)
    if not m:
        return None
    glyph = m.group("glyph")
    status = GLYPH_TO_STATUS.get(glyph, "open")
    rest = m.group("rest")
    parts = _META_SPLIT_RE.split(rest, maxsplit=1)
    title = parts[0].strip()
    meta_str = parts[1] if len(parts) > 1 else ""
    task = {
        "title": title,
        "status": status,
        "priority": m.group("priority"),
    }
    for km in _META_KV_RE.finditer(meta_str):
        key = km.group(1).strip()
        val = km.group(2).strip()
        task[key] = val
    return task


def task_to_line(task: dict) -> str:
    """Serializza task dict a riga markdown."""
    for key, value in task.items():
        if isinstance(value, str) and ("\n" in value or "\r" in value or (key != "title" and "|" in value)):
            raise PersistenceError("invalid_task_value", "task fields must fit a single metadata line", field=key)
    if _META_SPLIT_RE.search(task.get("title", "")):
        raise PersistenceError("invalid_task_title", "task title contains a metadata delimiter")
    status = task.get("status", "open")
    glyph = STATUS_GLYPH.get(status, " ")
    priority = task.get("priority")
    title = task.get("title", "").strip()
    prefix_priority = f"({priority}) " if priority else ""

    meta_keys_order = ("id", "est", "owner", "added", "started", "done", "took", "blocker")
    meta_parts = []
    for k in meta_keys_order:
        if k in task and task[k]:
            meta_parts.append(f"{k}: {task[k]}")
    # Aggiungi altri eventuali (escludendo già scritti + id/status/priority/title)
    skip = {"id", "status", "priority", "title"} | set(meta_keys_order)
    for k, v in task.items():
        if k in skip or not v:
            continue
        meta_parts.append(f"{k}: {v}")

    meta_str = (" | " + " | ".join(meta_parts)) if meta_parts else ""
    return f"- [{glyph}] {prefix_priority}{title}{meta_str}"


def _legacy_id(task: dict, existing_ids: set) -> str:
    base = _slugify(task["title"])
    if base not in existing_ids:
        return base
    n = 2
    while f"{base}-{n}" in existing_ids:
        n += 1
    return f"{base}-{n}"


def _assign_id(task: dict, existing_ids: set) -> str:
    return "task-" + uuid.uuid4().hex


def parse_roadmap(path: Path) -> dict:
    """Parse l'intero file roadmap.md. Restituisce:
    {
      "frontmatter": {...},
      "preamble": "# Roadmap\\n...",
      "sections": OrderedDict({"Open": [task,...], "Done": [task,...], "Blocked": [...]})
    }
    Ogni task ha ID persistito o provvisorio deterministico sullo snapshot legacy.
    Il parser non scrive; il writer persiste gli ID e salva un backup prima della migrazione.
    """
    if not path.is_file():
        return {
            "frontmatter": {},
            "_revision": MISSING,
            "_original": None,
            "_migration": False,
            "preamble": "",
            "sections": OrderedDict((s, []) for s in DEFAULT_SECTIONS),
        }

    text = read_text(path)
    # Frontmatter parse minimale
    frontmatter = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            fm_raw = text[3:end].strip()
            body = text[end + 4:].lstrip("\n")
            for line in fm_raw.split("\n"):
                if ":" in line:
                    k, _, v = line.partition(":")
                    frontmatter[k.strip()] = v.strip()

    if int(frontmatter.get("roadmap_schema", "1")) > 2:
        raise PersistenceError("unsupported_schema", "roadmap schema is newer than this writer")
    sections: "OrderedDict[str, list]" = OrderedDict()
    section_notes = {}
    current_section: str | None = None
    preamble_lines: list[str] = []
    existing_ids: set = set()

    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
            if current_section in sections:
                raise PersistenceError("duplicate_section", "duplicate roadmap section", section=current_section)
            sections[current_section] = []
            section_notes[current_section] = []
            continue
        if current_section is None:
            preamble_lines.append(line)
            continue
        # Riga task
        task = parse_line(line)
        if task:
            if "id" not in task:
                legacy = _legacy_id(task, existing_ids)
                existing_ids.add(legacy)
                task["id"] = "task-" + uuid.uuid5(uuid.NAMESPACE_URL, revision(text) + ":" + legacy).hex
            elif not re.fullmatch(r"task-[0-9a-f]{32}", task["id"]):
                raise PersistenceError("invalid_task_id", "invalid persistent task id", id=task["id"])
            sections[current_section].append(task)
        elif stripped and stripped not in ("_(nessun task open)_", "_(nessun done negli ultimi 30 giorni)_", "_(nessun blocker attivo)_"):
            section_notes[current_section].append(line)

    tasks = [t for group in sections.values() for t in group]
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise PersistenceError("duplicate_task_id", "duplicate persistent task IDs; repair before writing")
    migration = frontmatter.get("roadmap_schema") != "2"
    if migration:
        aliases = {}
        used = set()
        for task in tasks:
            alias = _legacy_id(task, used)
            used.add(alias)
            family = re.sub(r"-\d+$", "", _slugify(task["title"]))
            aliases[alias] = [t["id"] for t in tasks
                              if re.sub(r"-\d+$", "", _slugify(t["title"])) == family]
        frontmatter["legacy_task_ids"] = json.dumps(aliases, separators=(",", ":"))

    # Ensure default sections esistano
    for s in DEFAULT_SECTIONS:
        if s not in sections:
            sections[s] = []

    return {
        "frontmatter": frontmatter,
        "_revision": revision(text),
        "_original": text,
        "_migration": migration,
        "_section_notes": section_notes,
        "preamble": "\n".join(preamble_lines).strip("\n"),
        "sections": sections,
    }


def write_roadmap(path: Path, data: dict, extra_changes=None) -> None:
    """Scrive roadmap.md preservando frontmatter + preamble + sezioni ordinate."""
    fm = dict(data.get("frontmatter") or {})
    fm.setdefault("title", "Roadmap")
    fm.setdefault("type", "roadmap")
    fm.setdefault("created", _today_iso())
    fm["updated"] = _today_iso()
    fm["roadmap_schema"] = "2"

    preamble = data.get("preamble") or "# Roadmap"
    sections: "OrderedDict[str, list]" = data.get("sections") or OrderedDict()

    fm_lines = ["---"]
    for k in ("title", "type", "created", "updated"):
        if k in fm:
            fm_lines.append(f"{k}: {fm[k]}")
    for k, v in fm.items():
        if k not in ("title", "type", "created", "updated"):
            fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")

    out = []
    out.append("\n".join(fm_lines))
    out.append("")
    out.append(preamble.strip("\n"))

    # Sezioni nell'ordine canonico, poi altre custom
    ordered_keys = list(DEFAULT_SECTIONS) + [s for s in sections if s not in DEFAULT_SECTIONS]
    for sec_name in ordered_keys:
        tasks = sections.get(sec_name, [])
        out.append("")
        out.append(f"## {sec_name}")
        out.append("")
        if not tasks:
            if sec_name == "Open":
                out.append("_(nessun task open)_")
            elif sec_name == "Done":
                out.append("_(nessun done negli ultimi 30 giorni)_")
            elif sec_name == "Blocked":
                out.append("_(nessun blocker attivo)_")
        else:
            for t in tasks:
                out.append(task_to_line(t))
        out.extend(data.get("_section_notes", {}).get(sec_name, []))

    expected = data.get("_revision", MISSING)
    check_revision(path, read_text(path), expected)
    if data.get("_migration") and data.get("_original") is not None:
        if path.parent.name == "wiki" and path.parent.parent.name == ".anjawiki":
            from project_recovery import snapshot_project
            snapshot_project(path.parent.parent.parent, reason="roadmap_migration")
        backup = path.with_name("." + path.name + ".pre-v2-" + expected.split(":")[-1] + ".bak")
        try:
            create_text(backup, data["_original"])
        except FileExistsError:
            if read_text(backup) != data["_original"]:
                raise PersistenceError("backup_conflict", "migration backup does not match source") from None
    new_text = "\n".join(out) + "\n"
    write_many({**(extra_changes or {}), path: (new_text, expected)})
    data["_revision"] = revision(new_text)
    data["_original"] = new_text
    data["_migration"] = False
    data["frontmatter"] = fm


def find_task(sections, task_id: str) -> tuple[str | None, int | None]:
    """Cerca un task per id. Restituisce (section_name, index) o (None, None)."""
    for sec_name, tasks in sections.items():
        for i, t in enumerate(tasks):
            if t.get("id") == task_id:
                return sec_name, i
    return None, None


def resolve_task(data: dict, task_id: str):
    sections = data["sections"]
    direct = find_task(sections, task_id)
    if direct[0] is not None:
        return direct
    aliases = json.loads(data["frontmatter"].get("legacy_task_ids", "{}"))
    candidates = aliases.get(task_id, [])
    if len(candidates) > 1:
        raise PersistenceError("ambiguous_task_id", "legacy task ID is ambiguous; use a persistent ID",
                               candidates=candidates)
    return find_task(sections, candidates[0]) if candidates else (None, None)


def move_task_to_section(sections, from_section: str, idx: int, to_section: str):
    """Sposta un task tra sezioni. Crea to_section se manca."""
    task = sections[from_section].pop(idx)
    if to_section not in sections:
        sections[to_section] = []
    sections[to_section].append(task)
    return task


def list_tasks(data, status=None, priority=None, owner=None) -> list[dict]:
    """Lista task filtrati. Aggiunge `section` al dict per riferimento."""
    out = []
    for sec_name, tasks in data["sections"].items():
        for t in tasks:
            if status and t.get("status") != status:
                continue
            if priority and t.get("priority") != priority:
                continue
            if owner and t.get("owner") != owner:
                continue
            row = dict(t)
            row["section"] = sec_name
            out.append(row)
    # Sort: status order (open, in_progress, done, blocked, cancelled), then priority (P0..P3)
    status_order = {s: i for i, s in enumerate(VALID_STATUS)}
    priority_order = {p: i for i, p in enumerate(VALID_PRIORITY)}
    out.sort(key=lambda t: (
        status_order.get(t.get("status", "open"), 99),
        priority_order.get(t.get("priority") or "P9", 99),
        t.get("added", ""),
    ))
    return out


def archive_done(data, older_than_days: int = 30) -> int:
    """Rimuove dalla sezione Done i task con `done` date più vecchia di N giorni.
    Restituisce numero di task rimossi (chiamante può scrivere su archive/)."""
    cutoff = datetime.now().astimezone().date() - timedelta(days=older_than_days)
    done_section = data["sections"].get("Done", [])
    kept = []
    archived = []
    for t in done_section:
        done_str = t.get("done", "")
        try:
            done_date = datetime.fromisoformat(done_str).date()
            if done_date < cutoff:
                archived.append(t)
                continue
        except Exception:
            pass
        kept.append(t)
    data["sections"]["Done"] = kept
    return len(archived)
