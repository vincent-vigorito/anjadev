"""Gruppo `memory`: recall/write/timeline."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from .common import _parse_frontmatter, _raw_root, _sessions_root, _wiki_root
from .config import ROOT, log_exc
from .persistence import create_text


def _slugify(s: str, max_len: int = 60) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:max_len] or "note"


def tool_memory_recall(args: dict) -> dict:
    """grep su wiki/, ranking per occurrence count."""
    topic = (args.get("topic") or "").strip()
    max_pages = int(args.get("max_pages", 5))
    if not topic:
        return {"error": "topic required"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"pages": [], "_warning": f"wiki dir not found: {wiki}"}

    # split topic in keywords (parole 3+ char), lowercase
    keywords = [w.lower() for w in re.findall(r"\b\w{3,}\b", topic)]
    if not keywords:
        return {"pages": []}

    matches = []
    for f in wiki.rglob("*.md"):
        if f.name.startswith(".") or "raw/" in str(f.relative_to(ROOT)):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as _exc:
            log_exc("memory.tool_memory_recall", _exc)
            continue
        text_lower = text.lower()
        score = sum(text_lower.count(kw) for kw in keywords)
        if score == 0:
            continue
        # estrai title da frontmatter o prima # heading
        title = f.stem
        m = re.search(r"^title:\s*(.+?)$", text, re.M)
        if m:
            title = m.group(1).strip().strip('"').strip("'")
        # snippet: prima riga che contiene un keyword
        snippet = ""
        for line in text.split("\n"):
            if any(kw in line.lower() for kw in keywords):
                snippet = line.strip()[:200]
                break
        rel = f.relative_to(ROOT)
        matches.append({
            "slug": f.stem,
            "title": title,
            "path": str(rel),
            "score": score,
            "snippet": snippet,
        })

    matches.sort(key=lambda x: x["score"], reverse=True)
    return {"pages": matches[:max_pages], "total_matches": len(matches)}


def tool_memory_write(args: dict) -> dict:
    """Scrive nota in <raw>/notes/<date>-<slug>.md."""
    # Sanitizza category: finisce in un path (raw/<category>) → '../../' uscirebbe da
    # .anjawiki (F-Sec-Anjadev-CategoryTopicTraversal). Solo [a-z0-9_-].
    category = re.sub(r"[^a-z0-9_-]", "", str(args.get("category", "note")).lower()) or "note"
    content = (args.get("content") or "").strip()
    title = args.get("title", "")
    if not content:
        return {"error": "content required"}

    raw = _raw_root()
    notes_dir = raw / ("notes" if category == "note" else category)
    try:  # difesa in profondità oltre la sanitizzazione
        notes_dir.resolve().relative_to(raw.resolve())
    except ValueError:
        return {"error": "invalid category"}
    notes_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.now().strftime("%Y-%m-%d")
    slug = _slugify(title) if title else _slugify(content[:60])
    fname = f"{date}-{slug}.md"
    out = notes_dir / fname

    header = (
        "---\n"
        f"category: {category}\n"
        f"created: {datetime.now().isoformat()}\n"
        f"source: anja_memory.write\n"
        "---\n\n"
    )
    if title:
        header += f"# {title}\n\n"
    while True:
        try:
            create_text(out, header + content + "\n")
            break
        except FileExistsError:
            out = notes_dir / f"{date}-{slug}-{uuid.uuid4().hex}.md"
    return {"path": str(out.relative_to(ROOT)), "status": "written"}


_LOG_ENTRY_RE = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s+(\w[\w-]*)\s*\|\s*(.+?)\s*$", re.M)


def tool_memory_timeline(args: dict) -> dict:
    """Aggregator temporale cross-source: log + sessions. Risponde a 'cosa è
    successo nel periodo X'. (kanban/goals: dal v0.21 vivono nel runtime hub —
    le categorie sono accettate e ignorate per compatibilità con i chiamanti.)

    args:
      from: ISO date (default 30 giorni fa)
      to: ISO date (default today)
      categories: opt list[str] subset di ['log', 'sessions'] (default tutti)
      limit: opt int (default 200) — cap eventi ritornati
    """
    from collections import Counter

    today_d = datetime.now().astimezone().date()
    from_str = (args.get("from") or "").strip()
    to_str = (args.get("to") or today_d.isoformat()).strip()

    try:
        to_date = datetime.fromisoformat(to_str).date() if to_str else today_d
    except Exception as _exc:
        log_exc("memory.tool_memory_timeline", _exc)
        return {"error": f"to date invalid: {to_str}"}
    if from_str:
        try:
            from_date = datetime.fromisoformat(from_str).date()
        except Exception as _exc:
            log_exc("memory.tool_memory_timeline", _exc)
            return {"error": f"from date invalid: {from_str}"}
    else:
        from_date = to_date - timedelta(days=30)

    if from_date > to_date:
        return {"error": "from > to"}

    cats_in = args.get("categories")
    all_cats = ("log", "sessions")
    categories = tuple(cats_in) if isinstance(cats_in, list) and cats_in else all_cats
    limit = int(args.get("limit", 200))

    events = []

    if "log" in categories:
        wiki = _wiki_root()
        log_file = wiki / "log.md"
        if log_file.is_file():
            text = log_file.read_text(encoding="utf-8", errors="replace")
            for m in _LOG_ENTRY_RE.finditer(text):
                d_str, etype, desc = m.group(1), m.group(2), m.group(3)
                try:
                    d = datetime.fromisoformat(d_str).date()
                except Exception as _exc:
                    log_exc("memory.tool_memory_timeline", _exc)
                    continue
                if from_date <= d <= to_date:
                    events.append({
                        "ts": d_str,
                        "type": f"log:{etype}",
                        "title": desc[:200],
                        "ref": "wiki/log.md",
                    })

    if "sessions" in categories:
        sessions_root = _sessions_root()
        if sessions_root.is_dir():
            for date_dir in sessions_root.iterdir():
                if not date_dir.is_dir():
                    continue
                try:
                    d = datetime.fromisoformat(date_dir.name).date()
                except Exception as _exc:
                    log_exc("memory.tool_memory_timeline", _exc)
                    continue
                if not (from_date <= d <= to_date):
                    continue
                for f in date_dir.glob("*.md"):
                    fm = {}
                    try:
                        fm, _ = _parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
                    except Exception as _exc:
                        log_exc("memory.tool_memory_timeline", _exc)
                        pass
                    started = fm.get("started", "") or date_dir.name
                    try:
                        rel = str(f.relative_to(ROOT))
                    except ValueError:
                        rel = str(f)
                    events.append({
                        "ts": started[:19] if isinstance(started, str) else date_dir.name,
                        "type": "session",
                        "title": fm.get("title") or f.stem,
                        "ref": rel,
                        "agent": fm.get("agent", ""),
                        "duration": fm.get("duration", ""),
                    })

    events.sort(key=lambda e: e["ts"], reverse=True)
    by_type = Counter(e["type"].split(":")[0] for e in events)

    return {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "count": len(events),
        "events": events[:limit],
        "summary": {"by_type": dict(by_type), "limit_applied": limit},
        "categories_used": list(categories),
    }


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "memory.recall",
        "group": "memory",
        "description": "Cerca pagine wiki rilevanti per un topic (keyword grep+rank). Usa per richiamare conoscenza dal wiki anja del progetto/hub corrente.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic, keyword, o domanda"},
                "max_pages": {"type": "integer", "default": 5, "description": "Numero massimo di pagine ritornate"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "memory.write",
        "group": "memory",
        "description": "Scrivi una nota in <raw>/notes/<date>-<slug>.md. Usa per salvare idee, pensieri, info da tornare a leggere.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "default": "note", "description": "Cartella di destinazione (note, idea, fact, ...)"},
                "content": {"type": "string", "description": "Contenuto markdown della nota"},
                "title": {"type": "string", "description": "Titolo opzionale (usato per slug filename)"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory.timeline",
        "group": "memory",
        "description": (
            "🕒 MEMORY aggregator temporale: combina log entries + sessions in una "
            "vista cronologica. Risponde a 'cosa è successo nel periodo X', 'cosa "
            "abbiamo fatto la settimana scorsa', 'lista decisioni di aprile'. "
            "Default: ultimi 30 giorni, tutte le categorie."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "ISO date (default 30 giorni fa)"},
                "to": {"type": "string", "description": "ISO date (default today)"},
                "categories": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["log", "sessions"]},
                    "description": "Subset categorie (default tutte)",
                },
                "limit": {"type": "integer", "default": 200, "description": "Cap eventi ritornati"},
            },
        },
    },
]

