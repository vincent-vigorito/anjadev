#!/usr/bin/env python3
"""
mcp_memory_server.py — MCP server "anja_memory" agnostico.

Implementa JSON-RPC 2.0 over stdio (spec MCP 2025-03-26 / 2024-11-05 compat).
Esposto a Claude Code, OpenCode, e qualsiasi altro MCP host via .mcp.json:

    {
      "mcpServers": {
        "anja_memory": {
          "command": "python3",
          "args": ["/abs/path/to/mcp_memory_server.py"],
          "env": {
            "ANJA_SCOPE": "project",                    // o "hub"
            "ANJA_ROOT": "/abs/path/to/project-root"    // o hub-root
          }
        }
      }
    }

Tool esposti: 9 gruppi core (memory, sessions, soul, user, skills, wiki, roadmap,
code, graph). Vedi TOOL_GROUPS per l'elenco autoritativo e ANJA_TOOL_GROUPS env
per il filtraggio runtime (default: tutti i core).

F-AnjadevCoreSplit (v0.21): i tool hub-only (agents/tasks/workspace/kanban/
goals/pp) NON vivono più qui — stanno in AnjaHub `anja-hub/scripts/
mcp_hub_runtime.py` (server `anja_hub_runtime`, stessi nomi tool). Questo
server non importa nulla dalla webapp anja-hub: è un plugin CLI puro.

Tool storicamente "futuri" non implementati (parking):
    - sessions.spawn      — crea nuova session per agent (mai necessitato)
    - memory.summarize    — aggregate cross-session summaries (post auto-summary)

Auto-summary per singola sessione: vedi `sessions.summarize` (spawn claude CLI
subprocess on-demand, sostituisce placeholder nella sezione `## Summary`).

Stdlib pure, no deps esterne.
"""

import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


# ============================================================
# config
# ============================================================

PROTO_VERSION = "2024-11-05"
SERVER_NAME = "anja_memory"
SERVER_VERSION = "2.0.0"

SCOPE = os.environ.get("ANJA_SCOPE", "project")  # project | hub | agent
ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()
_MAX_IMAGE_BYTES = 25 * 1024 * 1024  # cap download wiki.attach_image (anti file enorme da URL ostile)


def _load_secrets_env() -> int:
    """Auto-load `.secrets.env` via secrets_loader (modulo condiviso con CLI scripts)."""
    try:
        import importlib.util
        sp = Path(__file__).resolve().parent / "secrets_loader.py"
        spec = importlib.util.spec_from_file_location("secrets_loader", sp)
        sl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sl)
        return sl.load_secrets(ROOT, scope=SCOPE)
    except Exception:
        return 0


_SECRETS_LOADED = _load_secrets_env()


def _wiki_root() -> Path:
    """Ritorna la directory wiki in base allo scope.

    Hub/agent: gli hub post-hoist (init_hub recenti) hanno il wiki in
    `.anjawiki/wiki` come i project; i legacy in `<root>/wiki`. Probe del
    layout reale — il bug era assumere il legacy e fallire sugli hub nuovi."""
    if SCOPE == "project":
        return ROOT / ".anjawiki" / "wiki"
    hoisted = ROOT / ".anjawiki" / "wiki"
    return hoisted if hoisted.is_dir() else ROOT / "wiki"


def _raw_root() -> Path:
    if SCOPE == "project":
        return ROOT / ".anjawiki" / "raw"
    hoisted = ROOT / ".anjawiki" / "raw"
    return hoisted if hoisted.is_dir() else ROOT / "raw"


def _soul_path() -> Path:
    return ROOT / "SOUL.md"


def _sessions_root() -> Path:
    """Path canonico delle session in base allo scope.

    - project: <root>/.anjawiki/wiki/sessions/
    - hub:     <root>/sessions/                   (NON sotto wiki/ — l'hub non ha wiki/)
    - agent:   <root>/sessions/                   (idem agent dir)
    """
    if SCOPE == "project":
        return ROOT / ".anjawiki" / "wiki" / "sessions"
    return ROOT / "sessions"


# ============================================================
# tool implementations
# ============================================================

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
        except Exception:
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
    if out.exists():
        # avoid overwrite: append timestamp
        out = notes_dir / f"{date}-{datetime.now().strftime('%H%M%S')}-{slug}.md"

    header = (
        "---\n"
        f"category: {category}\n"
        f"created: {datetime.now().isoformat()}\n"
        f"source: anja_memory.write\n"
        "---\n\n"
    )
    if title:
        header += f"# {title}\n\n"
    out.write_text(header + content + "\n", encoding="utf-8")
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
    except Exception:
        return {"error": f"to date invalid: {to_str}"}
    if from_str:
        try:
            from_date = datetime.fromisoformat(from_str).date()
        except Exception:
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
                except Exception:
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
                except Exception:
                    continue
                if not (from_date <= d <= to_date):
                    continue
                for f in date_dir.glob("*.md"):
                    fm = {}
                    try:
                        fm, _ = _parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
                    except Exception:
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


def tool_sessions_list(args: dict) -> dict:
    """Lista session files dal filesystem.

    Supporta entrambi i layout:
    - Legacy: wiki/sessions/<date>.md (file-per-day)
    - Target (M-Mem 2+): wiki/sessions/<date>/<HHMMSS-kind-agent-hash>.md
    """
    limit = int(args.get("limit", 20))
    sessions_root = _sessions_root()
    if not sessions_root.is_dir():
        return {"sessions": []}

    entries = []
    # File-per-session (target schema)
    for date_dir in sorted(sessions_root.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
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
    except Exception:
        return info

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

    return {
        "id": target_file.stem,
        "path": str(target_file.relative_to(ROOT)),
        "content": target_file.read_text(encoding="utf-8", errors="replace"),
    }


def tool_sessions_summarize(args: dict) -> dict:
    """Genera auto-summary per una sessione spawnando `claude` CLI subprocess.

    Sostituisce il placeholder `## Summary` del session file con 3-5 bullet
    point sintetizzati dal session content (frontmatter + stats + user prompts).

    args:
      session_id: filename stem del session file (es. '194849-cli-claude-d9e6')
      model:     opzionale, 'haiku'|'sonnet'|'opus' (default 'haiku', veloce)
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

    content = target_file.read_text(encoding="utf-8")

    summary_re = re.compile(r"(^## Summary\s*\n)(.*?)(?=\n## |\Z)", re.M | re.DOTALL)
    m = summary_re.search(content)
    existing = (m.group(2).strip() if m else "")
    is_placeholder = (not existing) or existing.startswith("<!--")
    if existing and not is_placeholder and not force:
        return {
            "error": "Summary already populated. Use force=true to overwrite.",
            "existing_preview": existing[:200],
        }

    prompt = (
        "Leggi il seguente file di sessione di Claude Code (markdown con "
        "frontmatter + stats + lista user prompts). Produci un summary conciso "
        "in italiano: 3-5 bullet point che coprano cosa è stato fatto, decisioni "
        "chiave, e outcome. NIENTE preambolo, NIENTE 'ecco il summary'. Solo "
        "bullet diretti, niente headings.\n\n---\n" + content + "\n---"
    )

    claude_bin = os.environ.get("ANJA_CLAUDE_BIN", "claude")
    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--model", model],
            capture_output=True, timeout=180, text=True,
        )
    except FileNotFoundError:
        return {"error": f"claude CLI not in PATH (tried '{claude_bin}'). Set ANJA_CLAUDE_BIN."}
    except subprocess.TimeoutExpired:
        return {"error": "claude CLI timeout (>180s)"}

    if result.returncode != 0:
        return {"error": f"claude CLI rc={result.returncode}: {result.stderr[:500]}"}

    summary = result.stdout.strip()
    if not summary:
        return {"error": "claude returned empty summary"}

    new_block = f"## Summary\n\n{summary}\n"
    if m:
        new_content = content[:m.start()] + new_block + content[m.end():]
    else:
        new_content = content.rstrip() + "\n\n" + new_block

    target_file.write_text(new_content, encoding="utf-8")

    return {
        "summary": summary,
        "session_file": str(target_file.relative_to(ROOT)),
        "written": True,
        "model_used": model,
    }


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
    import subprocess
    here = Path(__file__).resolve()
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
        except Exception:
            pass


# ============================================================
# User profile tools (Fase 12 M-Id 5)
# ============================================================

def _resolve_default_user_slug(hub: Path) -> Optional[str]:
    """Read default_user da <hub>/config.json."""
    cfg = hub / "config.json"
    if not cfg.is_file():
        return None
    try:
        return json.loads(cfg.read_text(encoding="utf-8")).get("default_user")
    except Exception:
        return None


def _resolve_user_files(slug: Optional[str], detail: bool = False) -> tuple[Optional[Path], Optional[Path]]:
    """Risolvi (hub, file_path) per user HOT (default) o DETAIL."""
    hub = _hub_root_from_scope()
    if not hub:
        return None, None
    if not slug:
        slug = _resolve_default_user_slug(hub)
        if not slug:
            return hub, None
    suffix = "-detail" if detail else ""
    return hub, hub / "users" / f"{slug}{suffix}.md"


# User dir globale (cross-progetto) del plugin standalone: ~/.anja/{user,user-detail}.md.
# `~/.anjahub` è il path pre-split: letto ancora come alias (deprecato, warning una volta).
_USER_GLOBAL_DIR = Path.home() / ".anja"
_USER_GLOBAL_DIR_LEGACY = Path.home() / ".anjahub"
_LEGACY_USER_WARNED = False






def _resolve_user_global_fallback(detail: bool) -> Optional[Path]:
    """User file globale se esiste, None altrimenti (singleton, no multi-user):
      ~/.anja/user.md          → HOT
      ~/.anja/user-detail.md   → DETAIL
    Alias legacy ~/.anjahub/… (pre-split): ancora letto, con warning su stderr."""
    global _LEGACY_USER_WARNED
    name = "user-detail.md" if detail else "user.md"
    fp = _USER_GLOBAL_DIR / name
    if fp.is_file():
        return fp
    legacy = _USER_GLOBAL_DIR_LEGACY / name
    if legacy.is_file():
        if not _LEGACY_USER_WARNED:
            print(f"[anja_memory] DEPRECATED: {legacy} → sposta il profilo in {_USER_GLOBAL_DIR}/ "
                  f"(~/.anjahub resta letto come alias per un ciclo)", file=sys.stderr, flush=True)
            _LEGACY_USER_WARNED = True
        return legacy
    return None


def tool_user_read(args: dict) -> dict:
    """Read user profile HOT (default) o DETAIL.

    args:
      slug:   str = default_user dal hub config.json
      detail: bool = False  → True per leggere USER-detail.md

    Fallback in scope=project senza slug: legge ~/.anja/user.md se presente
    (profilo globale del plugin standalone; ~/.anjahub legacy come alias).
    """
    slug = args.get("slug")
    detail = bool(args.get("detail", False))

    if SCOPE == "project" and not slug:
        gfp = _resolve_user_global_fallback(detail)
        if gfp:
            return {
                "path": str(gfp),
                "slug": "global",
                "kind": "detail" if detail else "hot",
                "content": gfp.read_text(encoding="utf-8"),
                "source": "hub-global",
            }

    hub, fp = _resolve_user_files(slug, detail=detail)
    if not hub:
        return {"error": "hub root not determinable", "hint": "create ~/.anja/user.md (global profile) or set ANJA_HUB env"}
    if not fp or not fp.is_file():
        return {"error": f"user profile not found: {fp}", "hint": "create with users_init.py or ~/.anja/user.md for global"}
    return {
        "path": str(fp.relative_to(hub)),
        "slug": slug or _resolve_default_user_slug(hub),
        "kind": "detail" if detail else "hot",
        "content": fp.read_text(encoding="utf-8"),
    }


def tool_user_update(args: dict) -> dict:
    """Append/replace a section nel user profile (HOT default, DETAIL se detail=true).

    args:
      section: str  — heading-level-2 (es. "Gusti e preferenze"), creato se mancante
      content: str  — markdown da inserire
      mode:    'append' (default) | 'replace'
      detail:  bool  — True per scrivere su USER-detail.md (consigliato per dettagli)
      slug:    str   — override default_user
    """
    section = (args.get("section") or "").strip()
    content = (args.get("content") or "").strip()
    mode = (args.get("mode") or "append").lower()
    detail = bool(args.get("detail", False))
    slug = args.get("slug")

    if not section:
        return {"error": "section required"}
    if not content:
        return {"error": "content required"}
    if mode not in ("append", "replace"):
        return {"error": f"invalid mode '{mode}'. Use 'append' or 'replace'."}

    # Fallback hub-global in scope=project senza slug esplicito
    hub = None
    fp = None
    if SCOPE == "project" and not slug:
        gfp = _resolve_user_global_fallback(detail)
        if gfp:
            hub, fp = gfp.parent, gfp

    if fp is None:
        hub, fp = _resolve_user_files(slug, detail=detail)
        if not hub:
            return {"error": "hub root not determinable", "hint": "create ~/.anja/user.md (global profile) or set ANJA_HUB env"}
        if not fp:
            return {"error": "no default_user set in hub config.json. Run users_init.py first."}
        if not fp.is_file():
            return {"error": f"user file not found: {fp}", "hint": "create with users_init.py or ~/.anja/user.md for global"}

    today = datetime.now().strftime("%Y-%m-%d")
    text = fp.read_text(encoding="utf-8")
    lines = text.split("\n")
    section_header = f"## {section}"

    # Trova sezione (case-insensitive su nome dopo ##)
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip().lower() == section_header.lower():
            idx = i
            break

    if idx is None:
        # Append nuova sezione a fine file
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(section_header)
        lines.append("")
        lines.append(content)
        lines.append("")
    else:
        # Trova fine sezione (prossimo "## " heading o EOF)
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        if mode == "replace":
            # Rimuovi tutto tra header e end (escluso header)
            lines = lines[:idx + 1] + ["", content, ""] + lines[end:]
        else:
            # Append: trova ultima riga non vuota prima di end e inserisci dopo
            last_content = idx + 1
            for j in range(idx + 1, end):
                if lines[j].strip():
                    last_content = j + 1
            insertion = [f"- [{today}] {content}"] if "\n" not in content else [content]
            lines = lines[:last_content] + insertion + lines[last_content:]

    fp.write_text("\n".join(lines), encoding="utf-8")
    # Update frontmatter `updated:`
    text2 = fp.read_text(encoding="utf-8")
    text2 = re.sub(r"^updated:\s*.*$", f"updated: {today}", text2, count=1, flags=re.M)
    fp.write_text(text2, encoding="utf-8")

    return {
        "path": str(fp.relative_to(hub)),
        "section": section,
        "mode": mode,
        "kind": "detail" if detail else "hot",
        "status": "updated",
    }


# ============================================================
# ============================================================

def _hub_root_from_scope() -> Optional[Path]:
    """Risale alla hub root partendo da ANJA_ROOT.

    - SCOPE=hub:    ROOT è già il hub
    - SCOPE=agent:  ROOT è <hub>/agents/<name>/, quindi parent.parent = hub
    - SCOPE=project: ROOT è la project root, hub non determinabile direttamente
                     → prova ANJA_HUB env, altrimenti None
    """
    if SCOPE == "hub":
        return ROOT
    if SCOPE == "agent":
        # Resolve agent dir → parent (agents/) → parent (hub)
        if ROOT.parent.name == "agents":
            return ROOT.parent.parent
    # project: try env or fallback
    env_hub = os.environ.get("ANJA_HUB")
    if env_hub:
        return Path(env_hub).expanduser().resolve()
    return None














# ============================================================
# ============================================================













# ============================================================
# ============================================================































# ============================================================
# Skill catalog (Hermes-aligned) — stdlib only via skill_parser
# ============================================================

def _skill_parser():
    """Lazy import skill_parser locale al plugin (stdlib only)."""
    import importlib.util
    sp = Path(__file__).resolve().parent / "skill_parser.py"
    spec = importlib.util.spec_from_file_location("skill_parser", sp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _skill_sources() -> list[tuple[str, Path]]:
    """Lista (scope_label, path) da scansionare per SKILL.md.

    Ordine = precedenza dedup (first wins):
      1. project anja  → ${ANJA_ROOT}/.anjawiki/skills/   (se SCOPE=project)
      2. hub anja      → ${ANJA_ROOT}/skills/             (se SCOPE=hub)
      3. user-global   → ~/.anja/skills/
      4. plugin        → ${CLAUDE_PLUGIN_ROOT}/skills/    (bundled)
      5. cc:project    → ${ANJA_ROOT}/.claude/skills/     (legacy)
      6. cc:user       → ~/.claude/skills/                (legacy)
    """
    sources: list[tuple[str, Path]] = []
    if SCOPE == "project":
        sources.append(("project", ROOT / ".anjawiki" / "skills"))
        sources.append(("cc:project", ROOT / ".claude" / "skills"))
    elif SCOPE == "hub":
        sources.append(("hub", ROOT / "skills"))
    sources.append(("user-global", Path.home() / ".anja" / "skills"))
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        sources.append(("plugin", Path(plugin_root) / "skills"))
    else:
        sources.append(("plugin", Path(__file__).resolve().parent.parent / "skills"))
    sources.append(("cc:user", Path.home() / ".claude" / "skills"))
    return sources


def _scan_skill_dir(scope_label: str, path: Path, sp_mod) -> list[dict]:
    if not path.is_dir():
        return []
    out = []
    for sub in sorted(path.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.is_file():
            continue
        parsed = sp_mod.parse_skill_md(skill_md)
        if not parsed.get("name"):
            continue
        out.append({
            "name": parsed["name"],
            "description": parsed["description"][:200],
            "version": parsed["version"],
            "category": parsed["category"],
            "tags": parsed["tags"],
            "platforms": parsed["platforms"],
            "scope": scope_label,
            "path": parsed["path"],
        })
    return out


def tool_skill_list(args: dict) -> dict:
    """Level 0 catalog: lista skill con name + description + scope.

    Dedup per name (first wins per ordine sources).
    """
    sp_mod = _skill_parser()
    seen: dict[str, dict] = {}
    for scope_label, path in _skill_sources():
        for s in _scan_skill_dir(scope_label, path, sp_mod):
            seen.setdefault(s["name"], s)
    skills = sorted(seen.values(), key=lambda x: x["name"])
    return {"skills": skills, "total": len(skills)}


def tool_skill_load(args: dict) -> dict:
    """Level 1: carica body completo SKILL.md per name. Cerca in tutte le source."""
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name required"}
    sp_mod = _skill_parser()
    for scope_label, path in _skill_sources():
        skill_md = path / name / "SKILL.md"
        if skill_md.is_file():
            parsed = sp_mod.parse_skill_md(skill_md)
            if parsed.get("name"):
                parsed["scope"] = scope_label
                return parsed
    return {"error": f"skill '{name}' not found"}


def tool_skill_read_file(args: dict) -> dict:
    """Level 2: leggi file in references/scripts/templates della skill.

    args:
      name: str — skill name
      path: str — relative path inside skill dir (es. 'references/foo.md')
    """
    name = (args.get("name") or "").strip()
    rel_path = (args.get("path") or "").strip()
    if not name:
        return {"error": "name required"}
    if not rel_path:
        return {"error": "path required"}
    for scope_label, src in _skill_sources():
        skill_dir = src / name
        if not (skill_dir / "SKILL.md").is_file():
            continue
        candidate = (skill_dir / rel_path).resolve()
        try:
            candidate.relative_to(skill_dir.resolve())
        except ValueError:
            return {"error": "path escapes skill directory"}
        if not candidate.is_file():
            return {"error": f"file not found: {rel_path}"}
        try:
            return {
                "name": name,
                "scope": scope_label,
                "path": str(candidate),
                "content": candidate.read_text(encoding="utf-8"),
            }
        except OSError as e:
            return {"error": f"read failed: {e}"}
    return {"error": f"skill '{name}' not found"}


# ============================================================
# Skill agent-managed (Hermes skill_manage analog): write-side
# ============================================================

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_WRITABLE_SCOPES = ("project", "hub", "user-global")


def _default_write_scope() -> str:
    if SCOPE == "project":
        return "project"
    if SCOPE == "hub":
        return "hub"
    return "user-global"


def _resolve_skill_write_dir(scope: str, name: str) -> Optional[Path]:
    if not _SKILL_NAME_RE.match(name):
        return None
    if scope == "project":
        return ROOT / ".anjawiki" / "skills" / name
    if scope == "hub":
        return ROOT / "skills" / name
    if scope == "user-global":
        return Path.home() / ".anja" / "skills" / name
    return None


def _find_skill_dir_writable(name: str) -> Optional[tuple[str, Path]]:
    """Trova la skill esistente in uno scope writable. Ritorna (scope, dir) o None."""
    for scope_label, src in _skill_sources():
        if scope_label not in _WRITABLE_SCOPES:
            continue
        skill_dir = src / name
        if (skill_dir / "SKILL.md").is_file():
            return (scope_label, skill_dir)
    return None


def tool_skill_save(args: dict) -> dict:
    """Crea una nuova skill da zero. Errore se esiste già (usa skill.edit per riscrivere).

    args:
      name: str (kebab-case)
      content: str — intero SKILL.md (frontmatter + body)
      scope: 'project' | 'hub' | 'user-global' — default da SCOPE env
    """
    name = (args.get("name") or "").strip()
    content = args.get("content") or ""
    scope = (args.get("scope") or _default_write_scope()).strip()

    if not _SKILL_NAME_RE.match(name):
        return {"error": "name must be kebab-case (lowercase, digits, dash, underscore)"}
    if not content.strip():
        return {"error": "content required"}
    if scope not in _WRITABLE_SCOPES:
        return {"error": f"scope must be one of {_WRITABLE_SCOPES}"}

    target = _resolve_skill_write_dir(scope, name)
    if not target:
        return {"error": "cannot resolve target directory"}
    if target.exists():
        return {"error": f"skill already exists: {scope}/{name} (use skill.edit or skill.patch)"}

    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(content, encoding="utf-8")
    return {
        "status": "created",
        "scope": scope,
        "name": name,
        "path": str(target / "SKILL.md"),
    }


def _backup_skill_md(skill_dir: Path) -> Optional[Path]:
    """Salva copia di SKILL.md in <skill>/.history/<ts>.SKILL.md prima di una modifica.
    Ritorna path del backup o None se SKILL.md non esiste."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    history_dir = skill_dir / ".history"
    history_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    bak = history_dir / f"{ts}.SKILL.md"
    bak.write_text(skill_md.read_text(encoding="utf-8"), encoding="utf-8")
    # Cap history: keep last 20
    backups = sorted(history_dir.glob("*.SKILL.md"))
    for old in backups[:-20]:
        try:
            old.unlink()
        except Exception:
            pass
    return bak


def tool_skill_patch(args: dict) -> dict:
    """Patch mirato del SKILL.md via find/replace (Hermes-style, preferito a edit).
    Backup automatico in <skill>/.history/<ts>.SKILL.md prima della modifica.

    args:
      name: str
      old_string: str — testo esatto da sostituire (deve essere unique)
      new_string: str — nuovo testo
    """
    name = (args.get("name") or "").strip()
    old = args.get("old_string") or ""
    new = args.get("new_string") or ""

    if not name:
        return {"error": "name required"}
    if not old:
        return {"error": "old_string required"}

    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found

    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    if old not in text:
        return {"error": "old_string not found in SKILL.md"}
    if text.count(old) > 1:
        return {"error": "old_string is not unique — include more surrounding context"}
    bak = _backup_skill_md(skill_dir)
    skill_md.write_text(text.replace(old, new, 1), encoding="utf-8")
    return {"status": "patched", "scope": scope_label, "name": name,
            "backup": str(bak) if bak else None}


def tool_skill_history(args: dict) -> dict:
    """Lista i backup disponibili di una skill (in .history/).

    args:
      name: str — skill name
    """
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name required"}
    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    _, skill_dir = found
    history_dir = skill_dir / ".history"
    if not history_dir.is_dir():
        return {"name": name, "backups": []}
    backups = []
    for f in sorted(history_dir.glob("*.SKILL.md")):
        try:
            backups.append({
                "timestamp": f.stem.replace(".SKILL", ""),
                "path": str(f),
                "size_bytes": f.stat().st_size,
            })
        except Exception:
            continue
    return {"name": name, "backups": backups, "total": len(backups)}


def tool_skill_rollback(args: dict) -> dict:
    """Ripristina SKILL.md da un backup in .history/.

    args:
      name: str — skill name
      timestamp: str — opzionale, formato YYYYMMDD-HHMMSS. Se omesso usa l'ultimo.
    """
    name = (args.get("name") or "").strip()
    ts_filter = (args.get("timestamp") or "").strip() or None
    if not name:
        return {"error": "name required"}
    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    _, skill_dir = found
    history_dir = skill_dir / ".history"
    if not history_dir.is_dir():
        return {"error": "no .history/ directory — nothing to rollback"}
    backups = sorted(history_dir.glob("*.SKILL.md"))
    if not backups:
        return {"error": "no backups available"}
    target = None
    if ts_filter:
        for b in backups:
            if b.stem.startswith(ts_filter):
                target = b
                break
        if target is None:
            return {"error": f"backup with timestamp '{ts_filter}' not found"}
    else:
        target = backups[-1]
    # Backup state corrente PRIMA di rollback (così è anche reversibile)
    _backup_skill_md(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    return {"status": "rolled_back", "name": name,
            "restored_from": str(target),
            "current_snapshot_saved": True}


def tool_skill_edit(args: dict) -> dict:
    """Riscrive l'intero SKILL.md. Usa skill.patch quando possibile (più sicuro).

    args:
      name: str
      content: str — nuovo SKILL.md completo
    """
    name = (args.get("name") or "").strip()
    content = args.get("content") or ""
    if not name:
        return {"error": "name required"}
    if not content.strip():
        return {"error": "content required"}

    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return {"status": "edited", "scope": scope_label, "name": name}


def tool_skill_delete(args: dict) -> dict:
    """Cancella una skill (rimuove la directory). Solo in scope writable."""
    import shutil
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name required"}
    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found
    shutil.rmtree(skill_dir)
    return {"status": "deleted", "scope": scope_label, "name": name}


def tool_skill_write_file(args: dict) -> dict:
    """Scrive un file di reference dentro la skill (references/, scripts/, templates/).

    args:
      name: str — skill name
      path: str — relative path inside skill dir (es. 'references/api.md')
      content: str
    """
    name = (args.get("name") or "").strip()
    rel = (args.get("path") or "").strip()
    content = args.get("content") or ""
    if not name or not rel:
        return {"error": "name and path required"}
    if rel == "SKILL.md":
        return {"error": "use skill.edit or skill.patch to modify SKILL.md"}

    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found
    target = (skill_dir / rel).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError:
        return {"error": "path escapes skill directory"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"status": "written", "scope": scope_label, "name": name, "path": str(target)}


def tool_skill_remove_file(args: dict) -> dict:
    """Rimuove un file di reference. Usa skill.delete per cancellare l'intera skill."""
    name = (args.get("name") or "").strip()
    rel = (args.get("path") or "").strip()
    if not name or not rel:
        return {"error": "name and path required"}
    if rel == "SKILL.md":
        return {"error": "use skill.delete to remove the entire skill"}

    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found
    target = (skill_dir / rel).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError:
        return {"error": "path escapes skill directory"}
    if not target.is_file():
        return {"error": f"file not found: {rel}"}
    target.unlink()
    return {"status": "removed", "scope": scope_label, "name": name, "path": str(target)}










def tool_wiki_search(args: dict) -> dict:
    """Search nelle pagine wiki filtrando per type (entity/concept/source/analysis/all).

    Differenza vs memory.recall: filtra per cartella, ritorna metadati strutturati
    (type, updated, slug) utili per follow-up con wiki.read(slug).
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query required"}
    type_filter = (args.get("type") or "all").strip().lower()
    limit = int(args.get("limit", 10))

    valid_types = ("all", "entity", "concept", "source", "analysis", "session", "overview", "index")
    if type_filter not in valid_types:
        return {"error": f"type must be one of {valid_types}"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"results": [], "_warning": f"wiki dir not found: {wiki}"}

    keywords = [w.lower() for w in re.findall(r"\b\w{3,}\b", query)]
    if not keywords:
        return {"results": []}

    # Pluralized folder names per type
    folder_map = {
        "entity": "entities", "concept": "concepts", "source": "sources",
        "analysis": "analysis", "session": "sessions",
    }
    if type_filter == "all":
        scan_paths = [wiki]
    elif type_filter in folder_map:
        sub = wiki / folder_map[type_filter]
        scan_paths = [sub] if sub.is_dir() else []
    else:
        # overview/index — file singolo a root wiki
        single = wiki / f"{type_filter}.md"
        scan_paths = [single] if single.is_file() else []

    matches = []
    for path in scan_paths:
        files = [path] if path.is_file() else list(path.rglob("*.md"))
        for f in files:
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            tl = text.lower()
            score = sum(tl.count(kw) for kw in keywords)
            if score == 0:
                continue
            # frontmatter
            title = f.stem
            ftype = ""
            updated = ""
            mt = re.search(r"^title:\s*(.+?)$", text, re.M)
            if mt:
                title = mt.group(1).strip().strip('"').strip("'")
            mty = re.search(r"^type:\s*(.+?)$", text, re.M)
            if mty:
                ftype = mty.group(1).strip().strip('"').strip("'")
            mu = re.search(r"^updated:\s*(.+?)$", text, re.M)
            if mu:
                updated = mu.group(1).strip().strip('"').strip("'")
            # preview: prima riga matchante
            preview = ""
            for line in text.split("\n"):
                if any(kw in line.lower() for kw in keywords):
                    preview = line.strip()[:160]
                    break
            try:
                rel = str(f.relative_to(ROOT))
            except ValueError:
                rel = str(f)
            matches.append({
                "slug": f.stem,
                "title": title,
                "type": ftype or (f.parent.name.rstrip("s") if f.parent.name in folder_map.values() else "page"),
                "path": rel,
                "score": score,
                "updated": updated,
                "preview": preview,
            })

    matches.sort(key=lambda x: x["score"], reverse=True)
    return {"results": matches[:limit], "total_matches": len(matches)}


def _rrf_fuse(ranked_lists, k=60, weights=None):
    """Reciprocal Rank Fusion. Ogni lista è una sequenza di chiavi ordinate per rank
    (rank 1 = migliore). Fonde per POSIZIONE, non per score assoluto (BM25 e cosine
    non sono comparabili): score(key) = Σ w / (k + rank). Ritorna {key: rrf_score}."""
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    scores = {}
    for lst, w in zip(ranked_lists, weights):
        for rank, key in enumerate(lst, start=1):
            scores[key] = scores.get(key, 0.0) + w / (k + rank)
    return scores


def _rel_to_root(p):
    try:
        return str(Path(p).resolve().relative_to(ROOT.resolve()))
    except (ValueError, OSError):
        return str(p)


def _wiki_vector_search(query, type_filter, limit, include_sessions):
    """Canale vector del wiki: embedda la query e fa k-NN su kind='wiki'.
    Ritorna (hits, note). note != None se il canale non è disponibile (no provider/index)."""
    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db
        import embed_providers
    except ImportError as e:
        return [], f"module missing: {e}"

    provider = embed_providers.get_provider()
    if provider is None:
        return [], "no embed provider (set ANJA_EMBED_PROVIDER + API key)"
    anjawiki = ROOT / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return [], "vector index not built (run wiki.embed / code.reindex)"
    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        return [], f"db open failed: {e}"
    try:
        qv = provider.embed([query])
        if not qv:
            return [], "empty query embedding"
        hits = code_db.vector_search(db, qv[0], limit=limit, kind_filter="wiki")
    except Exception as e:
        return [], f"vector search failed: {e}"
    finally:
        try:
            db.close()
        except Exception:
            pass

    out = []
    for h in hits:
        page_type = h.get("lang") or "page"
        if not include_sessions and page_type == "session":
            continue
        if type_filter != "all" and page_type != type_filter:
            continue
        slug_full = h.get("func_name") or ""
        out.append({
            "key": str(Path(h["file_path"]).resolve()),
            "slug": slug_full.split(":")[-1],
            "title": slug_full.split(":")[-1],
            "type": page_type,
            "path": _rel_to_root(h["file_path"]),
            "preview": (h.get("content") or "")[:160].replace("\n", " ").strip(),
        })
    return out, None


def tool_wiki_search_hybrid(args: dict) -> dict:
    """🔀 Ricerca wiki IBRIDA (keyword + vector) fusa via Reciprocal Rank Fusion.

    Usa SEMPRE entrambi i canali quando disponibili e li fonde per rango: robusta sia
    su query esatte (nomi/comandi → keyword) sia concettuali (riformulazioni → vector).
    Degrada a solo keyword se manca embed provider/index.

    args: query (req), type ('all'|entity|concept|source|analysis|session), limit (10),
    mode ('hybrid'|'keyword'|'vector', default 'hybrid'), k (60, costante RRF),
    include_sessions (false), weight_keyword/weight_vector (1.0).
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query required"}
    type_filter = (args.get("type") or "all").strip().lower()
    limit = int(args.get("limit", 10))
    mode = (args.get("mode") or "hybrid").strip().lower()
    k = int(args.get("k", 60))
    include_sessions = bool(args.get("include_sessions", False))
    wk = float(args.get("weight_keyword", 1.0))
    wv = float(args.get("weight_vector", 1.0))

    pool = max(limit * 3, 20)
    meta = {}
    kw_rank = []
    vec_rank = []
    note = None

    if mode in ("hybrid", "keyword"):
        kw = tool_wiki_search({"query": query, "type": type_filter, "limit": pool})
        for r in kw.get("results", []):
            if not include_sessions and r.get("type") == "session":
                continue
            key = str((ROOT / r["path"]).resolve())
            m = meta.setdefault(key, {"slug": r["slug"], "title": r["title"],
                                      "type": r["type"], "path": r["path"],
                                      "preview": r.get("preview", ""), "channels": []})
            m["channels"].append("keyword")
            kw_rank.append(key)

    if mode in ("hybrid", "vector"):
        hits, vnote = _wiki_vector_search(query, type_filter, pool, include_sessions)
        if vnote:
            note = vnote
        for h in hits:
            key = h["key"]
            m = meta.setdefault(key, {"slug": h["slug"], "title": h["title"],
                                      "type": h["type"], "path": h["path"],
                                      "preview": h["preview"], "channels": []})
            m["channels"].append("vector")
            vec_rank.append(key)

    if not meta:
        out = {"results": [], "count": 0, "method": f"rrf_{mode}"}
        if note:
            out["_note"] = note
        return out

    fused = _rrf_fuse([kw_rank, vec_rank], k=k, weights=[wk, wv])
    ranked = sorted(fused.keys(), key=lambda key: -fused[key])[:limit]
    results = []
    for key in ranked:
        m = meta[key]
        results.append({
            "slug": m["slug"], "title": m["title"], "type": m["type"],
            "path": m["path"], "rrf_score": round(fused[key], 5),
            "channels": sorted(set(m["channels"])), "preview": m["preview"],
        })
    channels_used = []
    if kw_rank:
        channels_used.append("keyword")
    if vec_rank:
        channels_used.append("vector")
    out = {"results": results, "count": len(results),
           "method": f"rrf_{mode}", "k": k, "channels_used": channels_used}
    if note:
        out["_note"] = note
    return out


def tool_wiki_find_duplicates(args: dict) -> dict:
    """🔎 Trova coppie di pagine wiki semanticamente troppo simili (candidati duplicati
    / da fondere, o potenzialmente contraddittorie) via gli embeddings condivisi
    (code-index.db). Vede ciò che il match esatto NON vede (es. 'auth-service' vs
    'authentication'). Complementa il lint.

    args: threshold (default 0.85 similarity coseno), types (lista page_type, default
    ['entity','concept']), limit (default 20 coppie).
    """
    threshold = float(args.get("threshold", 0.85))
    types = args.get("types") or ["entity", "concept"]
    if isinstance(types, str):
        types = [types]
    limit = int(args.get("limit", 20))

    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db
        import embed_providers
    except ImportError as e:
        return {"error": f"module missing: {e}"}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embed provider available (set ANJA_EMBED_PROVIDER + API key)"}
    anjawiki = ROOT / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return {"error": "vector index not built — run wiki.embed / code.reindex first"}
    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        return {"error": f"db open failed: {e}"}

    try:
        pages = [p for p in code_db.list_wiki_pages(db)
                 if (p.get("page_type") or "") in types]
        seen_pairs = {}
        for p in pages:
            vec = code_db.get_embedding_vector(db, p["id"])
            if vec is None:
                continue
            neighbors = code_db.vector_search(db, vec, limit=4, kind_filter="wiki", exclude_id=p["id"])
            for n in neighbors:
                if (n.get("lang") or "") not in types:
                    continue
                score = 1.0 - float(n["distance"])
                if score < threshold:
                    continue
                a = (p.get("slug") or "").split(":")[-1]
                b = (n.get("func_name") or "").split(":")[-1]
                if not a or not b or a == b:
                    continue
                key = tuple(sorted([a, b]))
                if key not in seen_pairs or score > seen_pairs[key]["score"]:
                    seen_pairs[key] = {"pages": list(key), "score": round(score, 4),
                                       "type": p.get("page_type")}
    finally:
        try:
            db.close()
        except Exception:
            pass

    pairs = sorted(seen_pairs.values(), key=lambda x: -x["score"])[:limit]
    return {
        "duplicates": pairs,
        "count": len(pairs),
        "threshold": threshold,
        "types": types,
        "_hint": "Coppie sopra soglia = candidati merge/conflitto. Verifica con wiki.read prima di unire.",
    }


def tool_wiki_read(args: dict) -> dict:
    """Legge una pagina wiki per slug. Ricerca breadth-first in wiki/ + sottocartelle."""
    slug = (args.get("slug") or "").strip()
    if not slug:
        return {"error": "slug required"}
    # Strip .md if user passes filename
    if slug.endswith(".md"):
        slug = slug[:-3]

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    target = None
    for f in wiki.rglob(f"{slug}.md"):
        target = f
        break
    if not target:
        return {"error": f"page not found: {slug}"}

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": f"read error: {e}"}

    # Cap a 10k chars (~2500 token) per evitare context blow-up
    max_chars = int(args.get("max_chars", 10000))
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... [troncato a {max_chars} chars, totale {len(text)}]"

    try:
        rel = str(target.relative_to(ROOT))
    except ValueError:
        rel = str(target)
    return {"slug": slug, "path": rel, "content": text, "size": len(text)}


# ============================================================
# Wiki write helpers + tools (upsert entity/concept, log append)
# ============================================================

def _today_iso() -> str:
    return datetime.now().astimezone().date().isoformat()


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plugin_version() -> str:
    """Versione del plugin dal manifest (per l'actor `anjadev/<version>`)."""
    global _PLUGIN_VERSION_CACHE
    if _PLUGIN_VERSION_CACHE is None:
        try:
            pj = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
            _PLUGIN_VERSION_CACHE = json.loads(pj.read_text(encoding="utf-8")).get("version", "unknown")
        except Exception:
            _PLUGIN_VERSION_CACHE = "unknown"
    return _PLUGIN_VERSION_CACHE


_PLUGIN_VERSION_CACHE = None


def _actor() -> str:
    """Convenzione attori OKF §7: <producer>/<version> | human:<id> | process:<id>.
    Override via ANJA_ACTOR (es. l'hub può stampare process:routine-x)."""
    return os.environ.get("ANJA_ACTOR") or f"anjadev/{_plugin_version()}"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter naive (no PyYAML dep). Restituisce (fm_dict, body).

    Supporta: scalar str, lista inline `[a, b, c]`. Niente nested o multi-line.
    Sufficiente per il nostro schema wiki.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm_raw = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fm: dict = {}
    for line in fm_raw.split("\n"):
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if "{" in inner:
                # flow-list di mappe (es. verified: [{ by, at }, ...]): lo split
                # sulle virgole la corromperebbe — opaca, round-trip verbatim
                fm[key] = val
            else:
                items = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
                fm[key] = items
        else:
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            fm[key] = val
    return fm, body


def _fm_kv(key: str, val) -> str:
    if isinstance(val, bool):
        return f"{key}: {'true' if val else 'false'}"
    if isinstance(val, list):
        if not val:
            return f"{key}: []"
        return f"{key}: [{', '.join(str(x) for x in val)}]"
    return f"{key}: {val}"


def _compose_frontmatter(fm: dict) -> str:
    """Serializza frontmatter dict in YAML. Preserva ordine canonico: title,
    type, subtype, created, updated, sources, tags, poi resto."""
    order = ["title", "type", "subtype", "created", "updated", "sources", "tags"]
    lines = ["---"]
    written = set()
    for key in order:
        if key in fm:
            lines.append(_fm_kv(key, fm[key]))
            written.add(key)
    for key in fm:
        if key not in written:
            lines.append(_fm_kv(key, fm[key]))
    lines.append("---")
    return "\n".join(lines) + "\n"


def _parse_sections(body: str):
    """Parse body markdown in OrderedDict {section_heading: content}.

    Sezioni = headings di livello 2 (`## Title`). Tutto prima del primo `##`
    finisce nella chiave '' (preambolo, tipicamente `# Title`).
    """
    from collections import OrderedDict
    sections: "OrderedDict[str, str]" = OrderedDict()
    current_key = ""
    current_lines: list = []
    for line in body.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            sections[current_key] = "\n".join(current_lines).strip("\n")
            current_key = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current_key] = "\n".join(current_lines).strip("\n")
    return sections


def _compose_sections(sections) -> str:
    """Inverse di _parse_sections: produce body markdown coerente."""
    parts = []
    for heading, content in sections.items():
        if heading == "":
            if content:
                parts.append(content)
        else:
            block = f"## {heading}\n\n{content}".rstrip()
            parts.append(block)
    return "\n\n".join(parts) + "\n"


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_LOG_TYPE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


_EXTRA_FM_FIELDS = ("source_path", "subtype", "git_sha", "analyzed_at", "question", "transient",
                    "status", "stale_after")

# Schema 1.1 (semantica OKF v0.2 §5.4/§5.5)
_VALID_STATUS = ("draft", "stable", "deprecated")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_CANONICAL_SECTIONS = {
    "entity": ["Sintesi", "Dettagli", "Apparizioni", "Connessioni"],
    "concept": ["Definizione", "Perché conta in questo progetto", "Esempi nel progetto", "Riferimenti"],
    "source": ["Punti chiave", "Pagine wiki coinvolte"],
    "analysis": ["Domanda", "Risposta", "Pagine usate"],
}


def _compute_canonical_warnings(sections_keys: list[str], page_type: str) -> list[str]:
    """Verifica sezioni canoniche del template anjadev. Lista warning strings,
    vuota se tutte presenti. Case-insensitive, strip whitespace."""
    canonical = _CANONICAL_SECTIONS.get(page_type, [])
    if not canonical:
        return []
    existing_norm = {s.strip().lower() for s in sections_keys if s and s.strip()}
    missing = [s for s in canonical if s.lower() not in existing_norm]
    if not missing:
        return []
    return [f"missing canonical section '{s}' (recommended for type={page_type})" for s in missing]


def _wiki_upsert_page(args: dict, page_type: str, folder: str) -> dict:
    """Upsert generico entity/concept/source/analysis. Merge sezioni replace-by-name.
    Frontmatter extra opt-in: source_path, subtype, git_sha, analyzed_at,
    question, transient (vengono scritti solo se presenti in args)."""
    from collections import OrderedDict

    slug = (args.get("slug") or "").strip().strip("/")
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return {"error": "slug required"}
    if not _SLUG_RE.match(slug):
        return {"error": f"slug must be kebab-case lowercase ([a-z0-9-]+): '{slug}'"}

    sections_in = args.get("sections") or {}
    if not isinstance(sections_in, dict) or not sections_in:
        return {"error": "sections must be a non-empty dict {section_name: content}"}

    # Schema 1.1: lifecycle opzionale, validato writer-side (siamo noi a scrivere)
    if args.get("status") is not None and args["status"] not in _VALID_STATUS:
        return {"error": f"status must be one of {_VALID_STATUS}"}
    if args.get("stale_after") is not None and not _DATE_RE.match(str(args["stale_after"])):
        return {"error": "stale_after must be an absolute ISO date (YYYY-MM-DD)"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    target_dir = wiki / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{slug}.md"

    today = _today_iso()
    title_in = (args.get("title") or "").strip()
    title_default = slug.replace("-", " ").strip().title()
    sources_in = args.get("sources") or []
    tags_in = args.get("tags") or []

    if target_file.is_file():
        text = target_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        sections = _parse_sections(body)
        for sec_name, sec_content in sections_in.items():
            sections[sec_name] = (sec_content or "").strip()
        fm["updated"] = today
        fm["type"] = page_type
        if title_in:
            fm["title"] = title_in
        elif "title" not in fm:
            fm["title"] = title_default
        if sources_in:
            existing = fm.get("sources", []) if isinstance(fm.get("sources"), list) else []
            for s in sources_in:
                if s not in existing:
                    existing.append(s)
            fm["sources"] = existing
        if tags_in:
            existing = fm.get("tags", []) if isinstance(fm.get("tags"), list) else []
            for t in tags_in:
                if t not in existing:
                    existing.append(t)
            fm["tags"] = existing
        for fname in _EXTRA_FM_FIELDS:
            if fname in args and args[fname] is not None:
                fm[fname] = args[fname]
        action = "updated"
    else:
        title_final = title_in or title_default
        fm = {
            "title": title_final,
            "type": page_type,
            "created": today,
            "updated": today,
            "sources": list(sources_in),
            "tags": list(tags_in),
        }
        for fname in _EXTRA_FM_FIELDS:
            if fname in args and args[fname] is not None:
                fm[fname] = args[fname]
        sections = OrderedDict()
        sections[""] = f"# {title_final}"
        for sec_name, sec_content in sections_in.items():
            sections[sec_name] = (sec_content or "").strip()
        action = "created"

    # Schema 1.1 (OKF §5.2): chi ha prodotto l'ultima modifica di contenuto.
    # Stampato a OGNI write — generated ≠ verified (chi scrive non è chi conferma).
    fm["generated"] = f"{{ by: {_actor()}, at: {_now_iso_utc()} }}"

    new_text = _compose_frontmatter(fm) + "\n" + _compose_sections(sections)
    target_file.write_text(new_text, encoding="utf-8")

    # Trigger re-embed in background (fire-and-forget) — abilita semantic graph k-NN
    _trigger_wiki_embed_bg(target_file)

    # Validation soft: warning se mancano sezioni canoniche post-write (no block)
    final_sections = [k for k in sections.keys() if k]
    warnings = _compute_canonical_warnings(final_sections, page_type)

    result = {
        "slug": slug,
        "path": str(target_file.relative_to(ROOT)),
        "action": action,
        "type": page_type,
        "sections_modified": list(sections_in.keys()),
    }
    if warnings:
        result["_warnings"] = warnings
    return result


def _trigger_wiki_embed_bg(md_path: Path) -> None:
    """Fire-and-forget background re-embed di una singola pagina wiki.

    Pattern identico a hooks/session_end.py:spawn_bg_summarize. Detach via
    start_new_session=True così il subprocess sopravvive al tool dispatch.
    Skip silenzioso se ANJA_WIKI_EMBED=0 (opt-out).
    """
    if os.environ.get("ANJA_WIKI_EMBED", "1") == "0":
        return
    script = Path(__file__).resolve().parent / "wiki_embed.py"
    if not script.is_file():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(script), str(ROOT), "--single", str(md_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def tool_wiki_upsert_entity(args: dict) -> dict:
    """Crea o aggiorna una pagina entity nel wiki."""
    return _wiki_upsert_page(args, page_type="entity", folder="entities")


def tool_wiki_upsert_concept(args: dict) -> dict:
    """Crea o aggiorna una pagina concept nel wiki."""
    return _wiki_upsert_page(args, page_type="concept", folder="concepts")


def tool_wiki_upsert_source(args: dict) -> dict:
    """Crea o aggiorna una pagina source. Accetta source_path, subtype,
    git_sha, analyzed_at come campi frontmatter extra (per codebase-snapshot)."""
    return _wiki_upsert_page(args, page_type="source", folder="sources")


def tool_wiki_upsert_analysis(args: dict) -> dict:
    """Crea o aggiorna una pagina analysis. Accetta question, transient come
    campi frontmatter extra (transient=true per lint report cancellabili)."""
    return _wiki_upsert_page(args, page_type="analysis", folder="analysis")


def tool_wiki_update_overview(args: dict) -> dict:
    """Update `wiki/overview.md` con merge sezioni replace-by-name.

    Crea il file se mancante (frontmatter type=overview + heading `# Overview`).
    Mai aggressivo: aggiorna solo le sezioni passate, lascia intatte le altre.

    args:
      sections: dict {section_name: markdown_content} — sezioni da scrivere
      title: opt, default "Overview"
    """
    from collections import OrderedDict

    sections_in = args.get("sections") or {}
    if not isinstance(sections_in, dict) or not sections_in:
        return {"error": "sections must be a non-empty dict {section_name: content}"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    overview_file = wiki / "overview.md"
    today = _today_iso()
    title_in = (args.get("title") or "Overview").strip()

    if overview_file.is_file():
        text = overview_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        sections = _parse_sections(body)
        for sec_name, sec_content in sections_in.items():
            sections[sec_name] = (sec_content or "").strip()
        fm["updated"] = today
        fm["type"] = "overview"
        if title_in and "title" not in fm:
            fm["title"] = title_in
        action = "updated"
    else:
        fm = {
            "title": title_in,
            "type": "overview",
            "created": today,
            "updated": today,
        }
        sections = OrderedDict()
        sections[""] = f"# {title_in}"
        for sec_name, sec_content in sections_in.items():
            sections[sec_name] = (sec_content or "").strip()
        action = "created"

    new_text = _compose_frontmatter(fm) + "\n" + _compose_sections(sections)
    overview_file.write_text(new_text, encoding="utf-8")
    _trigger_wiki_embed_bg(overview_file)

    return {
        "path": str(overview_file.relative_to(ROOT)),
        "action": action,
        "sections_modified": list(sections_in.keys()),
    }


def tool_wiki_index_update(args: dict) -> dict:
    """Update `wiki/index.md`: per una `category` (heading di livello 2) fa
    append o replace della lista entries (markdown bullets).

    Crea l'index se mancante. Crea la category se non esiste. Mode 'append'
    dedupa per riga esatta (no duplicati). Mode 'replace' sostituisce l'intera
    sezione.

    args:
      category: str — nome sezione (es. 'Sources', 'Entities', 'Concepts', 'Analysis')
      entries: list[str] — righe markdown da appendere (es. '- [[auth-service]] — servizio JWT')
      mode: 'append' (default) | 'replace'
    """
    from collections import OrderedDict

    category = (args.get("category") or "").strip()
    if not category:
        return {"error": "category required"}
    entries = args.get("entries") or []
    if not isinstance(entries, list) or not entries:
        return {"error": "entries must be a non-empty list of strings"}
    mode = (args.get("mode") or "append").strip().lower()
    if mode not in ("append", "replace"):
        return {"error": "mode must be 'append' or 'replace'"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    index_file = wiki / "index.md"
    today = _today_iso()

    if index_file.is_file():
        text = index_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        sections = _parse_sections(body)
        fm["updated"] = today
        if "type" not in fm:
            fm["type"] = "index"
        if "title" not in fm:
            fm["title"] = "Index"
    else:
        fm = {"title": "Index", "type": "index", "created": today, "updated": today}
        sections = OrderedDict()
        sections[""] = "# Index"

    if mode == "replace" or category not in sections:
        sections[category] = "\n".join(entries).strip()
        added = entries
    else:
        existing = sections[category]
        existing_lines = [ln for ln in existing.split("\n") if ln.strip()]
        added = []
        for e in entries:
            if e not in existing_lines:
                existing_lines.append(e)
                added.append(e)
        sections[category] = "\n".join(existing_lines).strip()

    new_text = _compose_frontmatter(fm) + "\n" + _compose_sections(sections)
    index_file.write_text(new_text, encoding="utf-8")

    return {
        "path": str(index_file.relative_to(ROOT)),
        "category": category,
        "mode": mode,
        "entries_added": added,
        "entries_total": len(sections[category].split("\n")) if sections.get(category) else 0,
    }


def tool_wiki_log_append(args: dict) -> dict:
    """Append entry strict-format `## [YYYY-MM-DD] type | description` a wiki/log.md."""
    log_type = (args.get("type") or "").strip()
    if not log_type:
        return {"error": "type required"}
    if not _LOG_TYPE_RE.match(log_type):
        return {"error": f"type must match [a-z][a-z0-9-]*: '{log_type}'"}

    description = (args.get("description") or "").strip()
    if not description:
        return {"error": "description required"}
    description = description.replace("\n", " ").replace("\r", " ")[:200]

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    log_file = wiki / "log.md"
    today = _today_iso()
    entry = f"## [{today}] {log_type} | {description}"

    if log_file.is_file():
        existing = log_file.read_text(encoding="utf-8").rstrip() + "\n"
        new_content = existing + "\n" + entry + "\n"
    else:
        new_content = f"# Log\n\n{entry}\n"

    log_file.write_text(new_content, encoding="utf-8")
    return {
        "entry": entry,
        "path": str(log_file.relative_to(ROOT)),
        "type": log_type,
    }


# ============================================================
# Wiki maintenance: backlinks, lint, rename, delete (v1.3.0)
# ============================================================

_WIKILINK_RE = re.compile(r"\[\[([^\]|#\s]+)(#[^\]|]+)?(\|[^\]]+)?\]\]")


def _iter_wiki_md(wiki: Path):
    """Iter su tutti i file .md sotto wiki/ (esclude . files)."""
    for f in wiki.rglob("*.md"):
        if f.is_file() and not f.name.startswith("."):
            yield f


def _slug_of(f: Path) -> str:
    return f.stem


def tool_wiki_backlinks(args: dict) -> dict:
    """Trova tutte le pagine che linkano allo slug specificato via [[link]].

    Riconosce: [[slug]], [[slug|label]], [[slug#section]], [[slug#section|label]].
    Restituisce: {target_slug, backlinks: [{from_slug, from_path, from_type, occurrences, contexts: [str]}]}
    """
    slug = (args.get("slug") or "").strip()
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return {"error": "slug required"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    backlinks = []
    for f in _iter_wiki_md(wiki):
        if _slug_of(f) == slug:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        contexts = []
        occ = 0
        for line_no, line in enumerate(text.split("\n"), start=1):
            for m in _WIKILINK_RE.finditer(line):
                if m.group(1) == slug:
                    occ += 1
                    if len(contexts) < 3:
                        snippet = line.strip()[:160]
                        contexts.append(f"L{line_no}: {snippet}")
        if occ > 0:
            fm, _ = _parse_frontmatter(text)
            try:
                rel = str(f.relative_to(ROOT))
            except ValueError:
                rel = str(f)
            backlinks.append({
                "from_slug": _slug_of(f),
                "from_path": rel,
                "from_type": fm.get("type", f.parent.name.rstrip("s")),
                "occurrences": occ,
                "contexts": contexts,
            })

    backlinks.sort(key=lambda x: -x["occurrences"])
    return {"target_slug": slug, "count": len(backlinks), "backlinks": backlinks}


def tool_wiki_verify(args: dict) -> dict:
    """Registra un evento di verifica su una pagina wiki (schema 1.1, OKF §5.2/§5.3).

    `verified` è SEPARATO da `generated`: chi scrive non è chi conferma. Append,
    mai replace: più eventi = più conferme indipendenti. Il trust tier derivato:
    nessun verified → unverified; solo attori non-human → machine-confirmed;
    almeno un human:<id> → human-reviewed.

    args:
      slug: pagina da verificare (required)
      by:   attore (opt) — default `human:<ANJA_USER|utente os>`, perché una
            verifica invocata dall'agente su richiesta è una conferma dell'umano.
            Passa `process:<id>` esplicito per check automatici.
    """
    slug = (args.get("slug") or "").strip()
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return {"error": "slug required"}

    by = (args.get("by") or "").strip()
    if not by:
        import getpass
        by = "human:" + (os.environ.get("ANJA_USER") or getpass.getuser())
    if not re.match(r"^(human:|process:)[\w.-]+$|^[\w.-]+/[\w.@-]+$", by):
        return {"error": f"by must follow the actor convention "
                         f"(human:<id> | process:<id> | <producer>/<version>): '{by}'"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    target = None
    for f in wiki.rglob(f"{slug}.md"):
        target = f
        break
    if not target:
        return {"error": f"page not found: {slug}"}

    text = target.read_text(encoding="utf-8", errors="replace")
    fm, body = _parse_frontmatter(text)
    if not fm:
        return {"error": f"page '{slug}' has no frontmatter — cannot verify"}

    entry = f"{{ by: {by}, at: {_now_iso_utc()} }}"
    cur = fm.get("verified")
    if not cur:
        fm["verified"] = f"[{entry}]"
    elif isinstance(cur, str) and cur.startswith("[") and cur.endswith("]"):
        inner = cur[1:-1].strip()
        fm["verified"] = f"[{inner}, {entry}]" if inner else f"[{entry}]"
    else:
        # bare mapping (consumer OKF: mapping nudo ≡ lista da 1) o formato legacy
        fm["verified"] = f"[{cur}, {entry}]"

    sections = _parse_sections(body)
    target.write_text(_compose_frontmatter(fm) + "\n" + _compose_sections(sections),
                      encoding="utf-8")

    tier = "human-reviewed" if "human:" in fm["verified"] else "machine-confirmed"
    try:
        rel = str(target.relative_to(ROOT))
    except ValueError:
        rel = str(target)
    return {"slug": slug, "path": rel, "verified_by": by, "trust_tier": tier,
            "verifications": fm["verified"].count("{ by:")}


def tool_wiki_lint(args: dict) -> dict:
    """Health check del wiki: orfani, link rotti, pagine stale, frontmatter mancante.

    args:
      categories: opt list[str] subset di ['orphans', 'broken_links', 'stale', 'frontmatter']
                  (default tutte)
      stale_days: opt int (default 90) — pagine con `updated` più vecchie sono stale SE attive
    """
    cats_in = args.get("categories")
    all_cats = ("orphans", "broken_links", "stale", "frontmatter", "trust")
    categories = tuple(cats_in) if isinstance(cats_in, list) and cats_in else all_cats
    for c in categories:
        if c not in all_cats:
            return {"error": f"unknown category '{c}'. Valid: {all_cats}"}
    stale_days = int(args.get("stale_days", 90))

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    # Pass 1: catalog all pages (escludendo i 3 file speciali da orphan/frontmatter check)
    # MA scansionando TUTTI i file (inclusi i speciali) per il linkmap — altrimenti i
    # link da index/overview/log a una page non contano come backlink (bug fix v1.4.1).
    pages = {}  # slug -> {path, type, fm, body}
    all_files = []  # tutti i file md per scan link
    for f in _iter_wiki_md(wiki):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        all_files.append((f, text))
        if f.parent == wiki and f.name in ("log.md", "index.md", "overview.md"):
            continue
        fm, body = _parse_frontmatter(text)
        pages[_slug_of(f)] = {"path": f, "type": fm.get("type", ""), "fm": fm, "body": body, "text": text}

    # Build linkmap: slug -> list of slug-or-file-stem che linka.
    # Scansiona ANCHE i file speciali (index/overview/log) per i loro link.
    linkmap = {s: [] for s in pages}
    broken = []  # (from_slug, target_slug, line_no)
    for f, text in all_files:
        from_slug = _slug_of(f)
        for line_no, line in enumerate(text.split("\n"), start=1):
            for m in _WIKILINK_RE.finditer(line):
                target = m.group(1)
                if target in pages:
                    if from_slug not in linkmap[target]:
                        linkmap[target].append(from_slug)
                elif from_slug in pages:
                    # Solo i broken_links DA file non-speciali sono riportati
                    # (link rotti in log.md sono storia, non vanno fixati)
                    broken.append({"from_slug": from_slug, "target": target, "line": line_no, "context": line.strip()[:160]})

    result = {"summary": {}}

    if "orphans" in categories:
        orphans = []
        for slug, p in pages.items():
            if not linkmap.get(slug):
                try:
                    rel = str(p["path"].relative_to(ROOT))
                except ValueError:
                    rel = str(p["path"])
                orphans.append({"slug": slug, "type": p["type"], "path": rel})
        orphans.sort(key=lambda x: (x["type"], x["slug"]))
        result["orphans"] = orphans
        result["summary"]["orphans"] = len(orphans)

    if "broken_links" in categories:
        result["broken_links"] = broken
        result["summary"]["broken_links"] = len(broken)

    if "stale" in categories:
        cutoff = datetime.now().astimezone().date() - timedelta(days=stale_days)
        stale = []
        for slug, p in pages.items():
            upd = p["fm"].get("updated")
            if not upd or not isinstance(upd, str):
                continue
            try:
                upd_date = datetime.fromisoformat(upd).date()
            except Exception:
                continue
            if upd_date < cutoff and linkmap.get(slug):
                age_days = (datetime.now().astimezone().date() - upd_date).days
                try:
                    rel = str(p["path"].relative_to(ROOT))
                except ValueError:
                    rel = str(p["path"])
                stale.append({"slug": slug, "type": p["type"], "path": rel, "updated": upd, "age_days": age_days, "backlinks_count": len(linkmap[slug])})
        stale.sort(key=lambda x: -x["age_days"])
        result["stale"] = stale
        result["summary"]["stale"] = len(stale)

    if "frontmatter" in categories:
        required = ("title", "type", "created", "updated")
        issues = []
        for slug, p in pages.items():
            missing = [k for k in required if k not in p["fm"]]
            if missing:
                try:
                    rel = str(p["path"].relative_to(ROOT))
                except ValueError:
                    rel = str(p["path"])
                issues.append({"slug": slug, "type": p["type"], "path": rel, "missing_fields": missing})
        issues.sort(key=lambda x: x["slug"])
        result["frontmatter_issues"] = issues
        result["summary"]["frontmatter_issues"] = len(issues)

    if "trust" in categories:
        # Schema 1.1: freshness contrattuale (stale_after) + trust tier (verified)
        today = datetime.now().astimezone().date()
        expired = []
        tiers = {"unverified": 0, "machine_confirmed": 0, "human_reviewed": 0}
        deprecated = []
        for slug, p in pages.items():
            fmp = p["fm"]
            ver = fmp.get("verified") or ""
            if not ver:
                tiers["unverified"] += 1
            elif "human:" in str(ver):
                tiers["human_reviewed"] += 1
            else:
                tiers["machine_confirmed"] += 1
            if str(fmp.get("status", "")).strip() == "deprecated":
                deprecated.append(slug)
            sa = str(fmp.get("stale_after", "")).strip()
            if sa:
                try:
                    sa_date = datetime.fromisoformat(sa).date()
                except Exception:
                    continue
                if today >= sa_date:
                    expired.append({"slug": slug, "type": p["type"], "stale_after": sa,
                                    "days_expired": (today - sa_date).days})
        expired.sort(key=lambda x: -x["days_expired"])
        result["stale_after_expired"] = expired
        result["deprecated_pages"] = sorted(deprecated)
        result["summary"]["stale_after_expired"] = len(expired)
        result["summary"]["trust_tiers"] = tiers

    result["summary"]["pages_scanned"] = len(pages)
    result["summary"]["stale_threshold_days"] = stale_days
    return result


_WIKI_SPECIALS = ("index.md", "log.md", "overview.md", "roadmap.md")
_WIKI_CATEGORIES = ("entities", "concepts", "sources", "analysis", "sessions")


def tool_wiki_tree(args: dict) -> dict:
    """Struttura ad albero del wiki: 4 file speciali + 5 categorie con file list.

    args:
      max_per_category: opt int (default 50) — tronca liste lunghe con count residuo
      include_files: opt bool (default True) — se False ritorna solo counts senza nomi
    """
    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    max_per_cat = int(args.get("max_per_category", 50))
    include_files = args.get("include_files", True)

    specials = []
    for name in _WIKI_SPECIALS:
        p = wiki / name
        specials.append({"name": name, "exists": p.is_file(), "size_bytes": p.stat().st_size if p.is_file() else 0})

    categories = {}
    for cat in _WIKI_CATEGORIES:
        d = wiki / cat
        if not d.is_dir():
            categories[cat] = {"count": 0, "files": []}
            continue
        # Recursive: sessions/ ha sub-cartelle date, le altre categorie no, ma rglob copre entrambi
        files = sorted([
            str(f.relative_to(d)) for f in d.rglob("*.md")
            if f.is_file() and not f.name.startswith(".")
        ])
        entry = {"count": len(files)}
        if include_files:
            if len(files) > max_per_cat:
                entry["files"] = files[:max_per_cat]
                entry["truncated"] = len(files) - max_per_cat
            else:
                entry["files"] = files
        categories[cat] = entry

    # Render markdown leggibile
    lines = [f"# Wiki tree — {wiki.relative_to(ROOT) if ROOT in wiki.parents or wiki.parent == ROOT else wiki}", ""]
    lines.append("## Special files")
    for s in specials:
        mark = "✓" if s["exists"] else "✗"
        lines.append(f"- {mark} `{s['name']}` ({s['size_bytes']} B)" if s["exists"] else f"- {mark} `{s['name']}` (missing)")
    lines.append("")
    for cat in _WIKI_CATEGORIES:
        e = categories[cat]
        lines.append(f"## {cat}/ ({e['count']})")
        if include_files and e.get("files"):
            for f in e["files"]:
                lines.append(f"- {f}")
            if e.get("truncated"):
                lines.append(f"- … +{e['truncated']} altri")
        lines.append("")

    return {
        "wiki_root": str(wiki),
        "specials": specials,
        "categories": categories,
        "rendered": "\n".join(lines).rstrip(),
    }


def tool_wiki_stats(args: dict) -> dict:
    """Statistiche del wiki: counts per type, top-linked, last-updated, size, log/session counts.

    args:
      top_n: opt int (default 10) — quanti elementi nelle top list
    """
    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    top_n = max(1, int(args.get("top_n", 10)))

    # Pass 1: catalog pagine non-speciali + linkmap + size
    pages = {}  # slug -> {path, type, updated, size}
    all_files = []
    type_counts = {}
    total_size = 0

    for f in _iter_wiki_md(wiki):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        size = f.stat().st_size
        total_size += size
        all_files.append((f, text))
        if f.parent == wiki and f.name in _WIKI_SPECIALS:
            continue
        fm, _ = _parse_frontmatter(text)
        ptype = fm.get("type") or f.parent.name.rstrip("s")
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
        pages[_slug_of(f)] = {
            "path": f,
            "type": ptype,
            "updated": fm.get("updated") if isinstance(fm.get("updated"), str) else None,
            "size": size,
        }

    # Linkmap: target_slug -> count of incoming links
    incoming = {s: 0 for s in pages}
    for f, text in all_files:
        from_slug = _slug_of(f)
        if from_slug in pages:
            seen_targets = set()
            for m in _WIKILINK_RE.finditer(text):
                target = m.group(1)
                if target in pages and target != from_slug:
                    seen_targets.add(target)
            for t in seen_targets:
                incoming[t] += 1

    top_linked = sorted(
        ({"slug": s, "type": pages[s]["type"], "backlinks": c} for s, c in incoming.items() if c > 0),
        key=lambda x: -x["backlinks"],
    )[:top_n]

    updated_pages = [
        {"slug": s, "type": p["type"], "updated": p["updated"]}
        for s, p in pages.items() if p["updated"]
    ]
    updated_pages.sort(key=lambda x: x["updated"], reverse=True)
    last_updated = updated_pages[:top_n]

    # Log entry count (parse "## [YYYY-MM-DD] ...")
    log_path = wiki / "log.md"
    log_entries = 0
    if log_path.is_file():
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            log_entries = sum(1 for line in log_text.split("\n") if re.match(r"^## \[\d{4}-\d{2}-\d{2}\]", line))
        except Exception:
            pass

    # Session count = file .md ricorsivi in sessions/ (struttura sub-cartelle date)
    sessions_dir = wiki / "sessions"
    session_count = 0
    if sessions_dir.is_dir():
        session_count = sum(1 for f in sessions_dir.rglob("*.md") if f.is_file() and not f.name.startswith("."))

    return {
        "wiki_root": str(wiki),
        "total_pages": len(pages),
        "total_size_bytes": total_size,
        "total_size_kb": round(total_size / 1024, 1),
        "by_type": dict(sorted(type_counts.items(), key=lambda kv: -kv[1])),
        "top_linked": top_linked,
        "last_updated": last_updated,
        "log_entries": log_entries,
        "session_count": session_count,
        "orphan_count": sum(1 for c in incoming.values() if c == 0),
    }


def tool_wiki_attach_image(args: dict) -> dict:
    """Allega un'immagine a una pagina wiki (entity/concept/source/analysis).

    Workflow: copia l'immagine in raw/<topic>/ + append markdown link nella pagina
    (sezione 'Diagrammi' o 'Screenshots'). Update frontmatter.updated.

    args:
      slug:       str — slug pagina target (deve esistere)
      image_path: str — path locale immagine (o url http/https)
      topic:      opt str — sotto-cartella raw/ (default: slug stesso)
      alt_text:   opt str — alt text del markdown link (default: filename)
      section:    opt str — section dove appendere ('Diagrammi' default, 'Screenshots' alt)
    """
    import shutil as _shutil
    from urllib.request import urlopen

    slug = (args.get("slug") or "").strip()
    image_path_arg = (args.get("image_path") or "").strip()
    if not slug or not image_path_arg:
        return {"error": "slug and image_path required"}

    wiki = _wiki_root()
    raw = _raw_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    # Find target page in entities/concepts/sources/analysis
    target_file = None
    for folder in ("entities", "concepts", "sources", "analysis"):
        candidate = wiki / folder / f"{slug}.md"
        if candidate.is_file():
            target_file = candidate
            break
    if target_file is None:
        return {"error": f"page not found: {slug} (cerco in entities/concepts/sources/analysis)"}

    # Download or copy image
    # topic finisce in raw/<topic>: sanitizza per impedire '../' traversal, poi confina
    # (F-Sec-Anjadev-CategoryTopicTraversal).
    topic = re.sub(r"[^a-zA-Z0-9_-]", "", (args.get("topic") or slug).strip().strip("/")) or slug
    raw_topic_dir = raw / topic
    try:
        raw_topic_dir.resolve().relative_to(raw.resolve())
    except ValueError:
        return {"error": "invalid topic"}
    raw_topic_dir.mkdir(parents=True, exist_ok=True)

    if image_path_arg.startswith(("http://", "https://")):
        # Download — basename + cap size (no filename traversal, no file enorme da URL ostile)
        filename = os.path.basename(image_path_arg.rsplit("/", 1)[-1].split("?")[0].strip())
        if filename in ("", ".", ".."):
            filename = "image.png"
        dest = raw_topic_dir / filename
        try:
            with urlopen(image_path_arg, timeout=15) as resp:
                data = resp.read(_MAX_IMAGE_BYTES + 1)
            if len(data) > _MAX_IMAGE_BYTES:
                return {"error": f"image too large (> {_MAX_IMAGE_BYTES // (1024 * 1024)}MB)"}
            dest.write_bytes(data)
        except Exception as e:
            return {"error": f"download failed: {type(e).__name__}: {e}"}
    else:
        src = Path(image_path_arg).expanduser().resolve()
        if not src.is_file():
            return {"error": f"image file not found: {src}"}
        filename = src.name
        dest = raw_topic_dir / filename
        try:
            _shutil.copy2(src, dest)
        except Exception as e:
            return {"error": f"copy failed: {type(e).__name__}: {e}"}

    # Compute relative path da target_file a dest
    # target_file = wiki/entities/<slug>.md → ../../raw/<topic>/<filename>
    rel_path = f"../../raw/{topic}/{filename}"
    alt_text = (args.get("alt_text") or filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")).strip()
    section_name = (args.get("section") or "Diagrammi").strip()

    # Read page, parse sections, append immagine in section target
    text = target_file.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    sections = _parse_sections(body)

    image_md = f"![{alt_text}]({rel_path})"
    if section_name in sections:
        existing = sections[section_name].rstrip()
        sections[section_name] = (existing + "\n\n" + image_md) if existing else image_md
    else:
        sections[section_name] = image_md

    fm["updated"] = _today_iso()
    new_text = _compose_frontmatter(fm) + "\n" + _compose_sections(sections)
    target_file.write_text(new_text, encoding="utf-8")

    return {
        "slug": slug,
        "page_path": str(target_file.relative_to(ROOT)),
        "image_path": str(dest.relative_to(ROOT)),
        "section": section_name,
        "alt_text": alt_text,
        "markdown_inserted": image_md,
    }


def tool_wiki_export(args: dict) -> dict:
    """Esporta il wiki in formato md (zip), json (dump strutturato) o html (static render).

    args:
      format: 'md' | 'json' | 'html'
      output_path: opt, default = .anjawiki/exports/wiki-export-<date>.<ext>
      include_sessions: opt bool (default False) — include session files (volume alto)
    """
    import zipfile

    fmt = (args.get("format") or "json").lower()
    if fmt not in ("md", "json", "html"):
        return {"error": f"format must be one of md|json|html, got '{fmt}'"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    include_sessions = bool(args.get("include_sessions", False))
    today = _today_iso()
    default_dir = ROOT / ".anjawiki" / "exports" if SCOPE == "project" else ROOT / "exports"
    default_dir.mkdir(parents=True, exist_ok=True)
    out_path_arg = args.get("output_path")
    if out_path_arg:
        # Confina a ROOT: senza il check si esporta tutto il wiki (prompt utente, SOUL,
        # frontmatter) verso un path arbitrario (F-Sec-Anjadev-ExportTraversal).
        out_path = Path(out_path_arg).expanduser().resolve()
        try:
            out_path.relative_to(ROOT.resolve())
        except ValueError:
            return {"error": "output_path must be inside the project root"}
    else:
        ext = {"md": "zip", "json": "json", "html": "zip"}[fmt]
        out_path = default_dir / f"wiki-export-{today}.{ext}"

    pages = []
    for f in _iter_wiki_md(wiki):
        rel = f.relative_to(wiki)
        if not include_sessions and "sessions" in rel.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        fm, body = _parse_frontmatter(text)
        pages.append({
            "slug": _slug_of(f),
            "path": str(rel),
            "frontmatter": fm,
            "body": body,
            "raw": text,
        })

    if fmt == "json":
        payload = {
            "wiki_root": str(wiki),
            "exported_at": today,
            "schema_version": _read_schema_version(),
            "page_count": len(pages),
            "pages": [{k: v for k, v in p.items() if k != "raw"} for p in pages],
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {"format": "json", "output_path": str(out_path), "page_count": len(pages), "size_bytes": out_path.stat().st_size}

    if fmt == "md":
        # Zip dei .md preservando struttura
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in pages:
                zf.writestr(p["path"], p["raw"])
        return {"format": "md", "output_path": str(out_path), "page_count": len(pages), "size_bytes": out_path.stat().st_size}

    # html: static render con wikilinks risolti
    slug_to_path = {p["slug"]: p["path"] for p in pages}
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in pages:
            html_body = _render_html_body(p["body"], slug_to_path)
            html = _render_html_page(p["frontmatter"].get("title", p["slug"]), html_body, p["frontmatter"])
            html_path = p["path"].replace(".md", ".html")
            zf.writestr(html_path, html)
        # Index page top-level (nome differenziato per evitare collisione con wiki/index.md)
        index_html = _render_html_index(pages)
        zf.writestr("_export_index.html", index_html)
    return {"format": "html", "output_path": str(out_path), "page_count": len(pages), "size_bytes": out_path.stat().st_size}


def _read_schema_version() -> str:
    """Legge .anjawiki/.schema-version se presente."""
    if SCOPE == "project":
        sv = ROOT / ".anjawiki" / ".schema-version"
        if sv.is_file():
            return sv.read_text(encoding="utf-8").strip()
    return "unknown"


def _render_html_body(body: str, slug_to_path: dict) -> str:
    """Render markdown→html basico: paragrafi + heading + wikilinks risolti.
    Niente markdown lib esterna, render minimale (no tables/code-block parser ricco)."""
    import html as _html
    out_lines = []
    in_pre = False
    for line in body.split("\n"):
        if line.startswith("```"):
            if in_pre:
                out_lines.append("</pre>")
                in_pre = False
            else:
                out_lines.append("<pre><code>")
                in_pre = True
            continue
        if in_pre:
            out_lines.append(_html.escape(line))
            continue
        # Heading
        m_h = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m_h:
            lvl = len(m_h.group(1))
            txt = _render_inline(m_h.group(2), slug_to_path)
            out_lines.append(f"<h{lvl}>{txt}</h{lvl}>")
            continue
        if not line.strip():
            out_lines.append("")
            continue
        out_lines.append(f"<p>{_render_inline(line, slug_to_path)}</p>")
    return "\n".join(out_lines)


_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _render_inline(text: str, slug_to_path: dict) -> str:
    """Resolve [[slug]], [[slug|label]], [![alt](url)], [text](url) inline."""
    import html as _html
    text_safe = _html.escape(text, quote=False)
    # Wikilinks: [[slug]] o [[slug|label]] o [[slug#anchor]]
    def _wl(m):
        target = m.group(1)
        anchor = m.group(2) or ""
        label = (m.group(3) or "").lstrip("|") or target
        if target in slug_to_path:
            href = slug_to_path[target].replace(".md", ".html")
            return f'<a href="/{href}{anchor}">{label}</a>'
        return f'<span class="broken-link" title="missing: {target}">{label}</span>'
    text_safe = _WIKILINK_RE.sub(_wl, text_safe)
    # Markdown inline links: [text](url)
    text_safe = _INLINE_LINK_RE.sub(r'<a href="\2">\1</a>', text_safe)
    # Inline code: `code`
    text_safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", text_safe)
    # Bold + italic markdown semplice
    text_safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text_safe)
    text_safe = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text_safe)
    return text_safe


def _render_html_page(title: str, body_html: str, fm: dict) -> str:
    """Wrapper HTML minimale, stile inline."""
    import html as _html
    css = """
    body { font-family: -apple-system, system-ui, sans-serif; max-width: 760px; margin: 2em auto; padding: 0 1em; line-height: 1.6; color: #222; }
    h1, h2, h3 { line-height: 1.25; }
    h1 { border-bottom: 2px solid #444; padding-bottom: 0.3em; }
    a { color: #0366d6; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .broken-link { color: #c00; text-decoration: line-through; }
    pre { background: #f6f8fa; padding: 1em; overflow-x: auto; border-radius: 4px; }
    code { background: #f6f8fa; padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.9em; }
    .frontmatter { background: #f6f8fa; border-left: 3px solid #0366d6; padding: 0.5em 1em; margin: 1em 0; font-size: 0.9em; color: #555; }
    """
    fm_block = ""
    if fm:
        items = "<br>".join(f"<strong>{_html.escape(str(k))}:</strong> {_html.escape(str(v))}" for k, v in fm.items() if k not in ("title",))
        fm_block = f'<div class="frontmatter">{items}</div>'
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_html.escape(title)}</title><style>{css}</style></head>
<body>{fm_block}{body_html}</body></html>"""


def _render_html_index(pages: list) -> str:
    """Index page con lista pages raggruppate per type."""
    from collections import defaultdict
    by_type = defaultdict(list)
    for p in pages:
        ptype = p["frontmatter"].get("type") or "page"
        by_type[ptype].append(p)
    css = """
    body { font-family: -apple-system, system-ui, sans-serif; max-width: 900px; margin: 2em auto; padding: 0 1em; line-height: 1.6; }
    h2 { border-bottom: 1px solid #ddd; padding-bottom: 0.3em; margin-top: 2em; text-transform: capitalize; }
    a { color: #0366d6; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .meta { color: #888; font-size: 0.9em; }
    """
    sections = []
    for ptype in sorted(by_type.keys()):
        items = by_type[ptype]
        items.sort(key=lambda p: p["slug"])
        rows = "\n".join(
            f'<li><a href="/{p["path"].replace(".md", ".html")}">{p["frontmatter"].get("title", p["slug"])}</a> <span class="meta">— {p["slug"]}</span></li>'
            for p in items
        )
        sections.append(f'<h2>{ptype} ({len(items)})</h2><ul>{rows}</ul>')
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Wiki index</title><style>{css}</style></head>
<body><h1>Wiki index</h1>{''.join(sections)}</body></html>"""


def tool_wiki_rename(args: dict) -> dict:
    """Rinomina una pagina wiki preservando tutti i [[link]] cross-wiki.

    args:
      old_slug: slug attuale (req)
      new_slug: nuovo slug kebab-case (req)
    """
    old_slug = (args.get("old_slug") or "").strip()
    new_slug = (args.get("new_slug") or "").strip()
    if old_slug.endswith(".md"):
        old_slug = old_slug[:-3]
    if new_slug.endswith(".md"):
        new_slug = new_slug[:-3]
    if not old_slug or not new_slug:
        return {"error": "old_slug and new_slug required"}
    if old_slug == new_slug:
        return {"error": "old_slug == new_slug, no-op"}
    if not _SLUG_RE.match(new_slug):
        return {"error": f"new_slug must be kebab-case ([a-z0-9-]+): '{new_slug}'"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    source_file = None
    for f in _iter_wiki_md(wiki):
        if _slug_of(f) == old_slug:
            source_file = f
            break
    if not source_file:
        return {"error": f"page not found: {old_slug}"}

    target_file = source_file.parent / f"{new_slug}.md"
    if target_file.exists():
        return {"error": f"target already exists: {target_file.name}"}

    # Replace links: [[old]], [[old|label]], [[old#section]], [[old#section|label]]
    files_touched = []
    links_updated = 0
    link_re = re.compile(r"\[\[" + re.escape(old_slug) + r"((?:#[^\]|]+)?(?:\|[^\]]+)?)\]\]")
    for f in _iter_wiki_md(wiki):
        if f == source_file:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new_text, n = link_re.subn(lambda m: f"[[{new_slug}{m.group(1)}]]", text)
        if n > 0:
            f.write_text(new_text, encoding="utf-8")
            files_touched.append(str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f))
            links_updated += n

    # Rename file (preserve content, optionally bump title if matches)
    source_file.rename(target_file)

    return {
        "renamed_from": old_slug,
        "renamed_to": new_slug,
        "new_path": str(target_file.relative_to(ROOT)) if target_file.is_relative_to(ROOT) else str(target_file),
        "links_updated": links_updated,
        "files_touched": files_touched,
    }


def tool_wiki_replace_links(args: dict) -> dict:
    """Replace `[[old]]` → `[[new]]` cross-wiki SENZA rinominare file.

    Utile per fixare convenzioni inconsistenti (es. `[[entity-X]]` → `[[X]]` in massa)
    o per sostituire link a pagine ancora da creare. Preserva label e anchor:
    `[[old|label]]` → `[[new|label]]`, `[[old#section]]` → `[[new#section]]`.

    args:
      old: slug attuale nei [[link]]
      new: slug nuovo da scrivere nei [[link]]
      dry_run: bool (default false) — true per preview senza scrivere
    """
    old = (args.get("old") or "").strip()
    new = (args.get("new") or "").strip()
    if not old or not new:
        return {"error": "old and new required"}
    if old == new:
        return {"error": "old == new, no-op"}
    dry_run = bool(args.get("dry_run", False))

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    link_re = re.compile(r"\[\[" + re.escape(old) + r"((?:#[^\]|]+)?(?:\|[^\]]+)?)\]\]")
    files_touched = []
    links_replaced = 0

    for f in _iter_wiki_md(wiki):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new_text, n = link_re.subn(lambda m: f"[[{new}{m.group(1)}]]", text)
        if n > 0:
            if not dry_run:
                f.write_text(new_text, encoding="utf-8")
            try:
                rel = str(f.relative_to(ROOT))
            except ValueError:
                rel = str(f)
            files_touched.append({"path": rel, "occurrences": n})
            links_replaced += n

    return {
        "old": old,
        "new": new,
        "dry_run": dry_run,
        "links_replaced": links_replaced,
        "files_touched": files_touched,
    }


def tool_wiki_delete(args: dict) -> dict:
    """Cancella una pagina wiki. Safety: confirm=false ritorna preview con backlinks.

    args:
      slug: slug della pagina (req)
      confirm: bool (default false) — true per eseguire
    """
    slug = (args.get("slug") or "").strip()
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return {"error": "slug required"}
    confirm = bool(args.get("confirm", False))

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    target_file = None
    for f in _iter_wiki_md(wiki):
        if _slug_of(f) == slug:
            target_file = f
            break
    if not target_file:
        return {"error": f"page not found: {slug}"}

    # Compute backlinks (would become broken)
    backlinks_result = tool_wiki_backlinks({"slug": slug})
    backlinks = backlinks_result.get("backlinks", [])
    rel = str(target_file.relative_to(ROOT)) if target_file.is_relative_to(ROOT) else str(target_file)

    if not confirm:
        return {
            "slug": slug,
            "path": rel,
            "action": "preview",
            "would_break_links": len(backlinks),
            "backlinks_preview": backlinks[:5],
            "hint": "Pass confirm=true to actually delete. Consider wiki.rename instead if you want to preserve links.",
        }

    target_file.unlink()
    return {
        "slug": slug,
        "path": rel,
        "action": "deleted",
        "broken_links_now": len(backlinks),
        "backlinks_affected": [b["from_slug"] for b in backlinks],
    }


# ============================================================
# Roadmap tools (F-TaskMgmt-Plugin, gruppo `roadmap`)
# ============================================================

def _roadmap_module():
    """Lazy-load roadmap_io dal medesimo dir di questo file."""
    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import roadmap_io  # noqa
        return roadmap_io
    except Exception:
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
        except Exception:
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


# ============================================================
# Code search tools (F-CodeSearch, gruppo `code`)
# ============================================================

def _code_search_module():
    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_search  # noqa
        return code_search
    except Exception:
        return None


def _code_index_module():
    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_index  # noqa
        return code_index
    except Exception:
        return None


def tool_code_search(args: dict) -> dict:
    """Wrapper MCP per code_search.code_search(). Vedi code_search.py per logica 3 livelli."""
    cs = _code_search_module()
    if cs is None:
        return {"error": "code_search module not available"}
    query = (args.get("query") or "").strip()
    smart_level = args.get("smart_level")
    limit = int(args.get("limit", 10))
    lang = args.get("lang")
    return cs.code_search(query=query, smart_level=smart_level, limit=limit, lang=lang)


def tool_code_reindex(args: dict) -> dict:
    """Wrapper MCP per code_index.index(). Build/refresh vector index."""
    ci = _code_index_module()
    if ci is None:
        return {"error": "code_index module not available"}
    force = bool(args.get("force", False))
    limit = args.get("limit")
    if limit is not None:
        limit = int(limit)
    return ci.index(target=ROOT, force=force, limit=limit, verbose=False)


def tool_code_status(args: dict) -> dict:
    """Stato dell'index: chunks totali, by-lang, provider, last_indexed_sha, size."""
    anjawiki = ROOT / ".anjawiki"
    db_path = anjawiki / "code-index.db"
    if not db_path.exists():
        return {
            "indexed": False,
            "hint": "Run code.reindex or /anja-index-code to build the vector index.",
        }
    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db, embed_providers
    except ImportError as e:
        return {"error": f"module missing: {e}"}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embed provider available (set ANJA_EMBED_PROVIDER + API key)"}

    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        return {"error": f"db open failed: {e}"}

    s = code_db.stats(db)
    s["indexed"] = True
    s["db_path"] = str(db_path.relative_to(ROOT))
    s["db_size_mb"] = round(db_path.stat().st_size / (1024 * 1024), 2)
    return s


# ============================================================
# Wiki embedding + semantic graph tools
# ============================================================

def _wiki_embed_module():
    """Lazy import wiki_embed.py locale al plugin."""
    import importlib.util
    sp = Path(__file__).resolve().parent / "wiki_embed.py"
    spec = importlib.util.spec_from_file_location("wiki_embed", sp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tool_wiki_embed(args: dict) -> dict:
    """Embed incrementale delle pagine wiki del progetto corrente.

    args:
      force: bool = False        — re-embed tutto, ignora dirty check
      include_sessions: bool = True
      single_page: str = ""      — path assoluto a una singola .md (più rapido)

    Ritorna stats: scanned, embedded, skipped_unchanged, deleted_orphans, errors, ms.
    """
    try:
        we = _wiki_embed_module()
    except Exception as e:
        return {"error": f"wiki_embed module unavailable: {e}"}

    single = (args.get("single_page") or "").strip()
    if single:
        return we.embed_single_page(ROOT, Path(single))

    return we.embed_wiki(
        ROOT,
        force=bool(args.get("force", False)),
        include_sessions=bool(args.get("include_sessions", True)),
    )


def tool_graph_report(args: dict) -> dict:
    """Compute knowledge graph report e scrivi GRAPH_REPORT.md nel wiki.

    args:
      top_god: int = 8                   — top-N god nodes
      surprise_threshold: float = 0.72   — similarity sopra → candidato surprise edge
      anchor_threshold: float = 0.6      — similarity wiki→code per "anchor"
      k_per_node: int = 5                — neighbors per pagina
      include_sessions: bool = False     — include wiki/sessions/ nel grafo
      write: bool = True                 — scrive GRAPH_REPORT.md (False=ritorna solo dict)
    """
    import importlib.util
    sp = Path(__file__).resolve().parent / "graph_report.py"
    spec = importlib.util.spec_from_file_location("graph_report", sp)
    gr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gr)

    report = gr.build_report(
        ROOT,
        top_n_god=int(args.get("top_god", 8)),
        surprise_threshold=float(args.get("surprise_threshold", 0.72)),
        anchor_threshold=float(args.get("anchor_threshold", 0.6)),
        k_per_node=int(args.get("k_per_node", 5)),
        include_sessions=bool(args.get("include_sessions", False)),
    )
    if "error" in report:
        return report

    if args.get("write", True):
        target = gr.write_report(ROOT, report)
        report["report_path"] = str(target.relative_to(ROOT))

    # Compact response: skip i field grossi se non richiesti
    if not args.get("verbose", False):
        report.pop("semantic_neighbors", None)
        report.pop("explicit_edges", None)
    return report


def tool_graph_search_text(args: dict) -> dict:
    """Search semantico cross-kind via query libera.

    Embedda la query con il provider corrente e fa k-NN nello spazio condiviso
    wiki + code. Filtri opzionali: kind (wiki|code|all), page_type per sotto-filtrare
    pagine wiki (entity|concept|source|analysis|session).

    args:
      query: str (required)
      filter: 'wiki' | 'code' | 'sessions' | 'all' = 'all'
      k: int = 10
      min_score: float = 0.5
      page_type: str — filtro extra per pagine wiki (es. 'entity', 'session')
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query required"}

    filter_kind = (args.get("filter") or "all").strip()
    page_type = (args.get("page_type") or "").strip() or None
    k = int(args.get("k", 10))
    min_score = float(args.get("min_score", 0.5))

    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db
        import embed_providers
    except ImportError as e:
        return {"error": f"module missing: {e}"}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embed provider configured (set ANJA_EMBED_PROVIDER + API key)"}

    anjawiki = ROOT / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return {"error": "index not built — run code.reindex and/or wiki.embed first"}

    # Embedda la query (1 call al provider)
    try:
        vecs = provider.embed([query])
    except Exception as e:
        return {"error": f"embedding failed: {e}"}
    if not vecs:
        return {"error": "empty embedding response"}
    query_vec = vecs[0]

    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        return {"error": f"db open failed: {e}"}

    try:
        # Filter → kind_filter + page_type post-filter
        if filter_kind == "sessions":
            kind_filter = "wiki"
            page_type_required = "session"
        elif filter_kind == "all":
            kind_filter = None
            page_type_required = page_type
        elif filter_kind in ("wiki", "code"):
            kind_filter = filter_kind
            page_type_required = page_type if filter_kind == "wiki" else None
        else:
            return {"error": f"invalid filter: {filter_kind}"}

        # Over-fetch per consentire post-filter su page_type
        raw = code_db.vector_search(
            db, query_vec=query_vec, limit=k * 3, kind_filter=kind_filter,
        )

        results = []
        for r in raw:
            if page_type_required and r["lang"] != page_type_required:
                continue
            score = 1.0 - float(r["distance"])
            if score < min_score:
                continue
            item = {
                "kind": r["kind"],
                "score": round(score, 4),
                "preview": (r["content"] or "")[:200].replace("\n", " ").strip(),
            }
            if r["kind"] == "wiki":
                item["slug"] = r["func_name"]
                item["page_type"] = r["lang"]
                item["file_path"] = r["file_path"]
            else:
                item["file_path"] = r["file_path"]
                item["func_name"] = r["func_name"]
                item["line_range"] = [r["line_start"], r["line_end"]]
                item["lang"] = r["lang"]
            results.append(item)
            if len(results) >= k:
                break

        return {
            "query": query,
            "filter": filter_kind,
            "page_type": page_type,
            "results": results,
            "count": len(results),
        }
    finally:
        db.close()


def tool_wiki_search_semantic(args: dict) -> dict:
    """Sugar wrapper: semantic search ristretto al wiki (escluse sessions di default).

    args identici a graph.search_text ma filter è forzato a 'wiki'.
    """
    forced = dict(args)
    forced["filter"] = "wiki"
    # Esclude sessions di default a meno che non si chieda esplicitamente
    if not forced.get("page_type") and not forced.get("include_sessions"):
        # Strategia: prima passata sopra-filter, poi escludi session in Python
        # Lo facciamo via _SOURCE_FILTER post-process per semplicità
        forced["_exclude_session_default"] = True
    result = tool_graph_search_text(forced)
    if "_exclude_session_default" in forced and "results" in result:
        result["results"] = [r for r in result["results"] if r.get("page_type") != "session"]
        result["count"] = len(result["results"])
    return result


def tool_sessions_search_semantic(args: dict) -> dict:
    """Sugar wrapper: semantic search ristretto a session journals.

    USE FOR: 'ricorda di cosa abbiamo parlato', 'session passata su X', 'quando
    ho discusso Y'. Richiede embed di sessions abilitato (default in wiki.embed).
    """
    forced = dict(args)
    forced["filter"] = "sessions"
    return tool_graph_search_text(forced)


def tool_graph_html(args: dict) -> dict:
    """Genera `<wiki>/graph.html` standalone Cytoscape visualizer.

    Single-file output (Cytoscape da CDN + dati embedded). Apri nel browser.
    Sidebar sx con search + filtri kind/type/edge, pannello dx dettagli su click.
    """
    import importlib.util
    sp = Path(__file__).resolve().parent / "graph_html.py"
    spec = importlib.util.spec_from_file_location("graph_html", sp)
    gh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gh)

    target = args.get("target")
    target_path = Path(target).expanduser() if target else None
    return gh.build_and_write_html(ROOT, target=target_path)


def tool_graph_semantic_neighbors(args: dict) -> dict:
    """k-NN cross-kind nello spazio embedding condiviso (wiki + code).

    args:
      source: str (required)     — slug pagina wiki (es. 'auth-service' o 'entities:auth-service')
                                   oppure file path codice (es. 'src/auth.py')
      kind: 'auto'|'wiki'|'code' = 'auto' — kind del source per lookup
      filter: 'all'|'wiki'|'code' = 'all' — quali neighbors ritornare
      k: int = 10
      min_score: float = 0.55    — cosine similarity threshold (0.0 = niente filter)

    Ritorna: {query: {...}, neighbors: [...], stats: {...}}
    Score = 1 - cosine_distance (1.0 = identico, 0.0 = ortogonale).
    """
    source = (args.get("source") or "").strip()
    if not source:
        return {"error": "source required"}
    kind = (args.get("kind") or "auto").strip()
    filter_kind = (args.get("filter") or "all").strip()
    k = int(args.get("k", 10))
    min_score = float(args.get("min_score", 0.55))

    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db
        import embed_providers
    except ImportError as e:
        return {"error": f"module missing: {e}"}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embed provider available (set ANJA_EMBED_PROVIDER + API key)"}

    anjawiki = ROOT / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return {"error": "index not built yet — run code.reindex and/or wiki.embed first"}

    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        return {"error": f"db open failed: {e}"}

    try:
        # 1. Lookup source — può essere slug (wiki, esatto o con prefisso) o file_path (code)
        candidates = []
        if kind in ("auto", "wiki"):
            # Wiki slug: try exact match first, then suffix match (entities:foo vs foo)
            row = db.execute(
                "SELECT id, file_path, func_name, kind FROM chunks "
                "WHERE kind = 'wiki' AND (func_name = ? OR func_name LIKE ?) LIMIT 1",
                (source, f"%:{source}"),
            ).fetchone()
            if row:
                candidates.append(dict(row))
        if kind in ("auto", "code") and not candidates:
            row = db.execute(
                "SELECT id, file_path, func_name, kind FROM chunks "
                "WHERE kind = 'code' AND file_path = ? LIMIT 1",
                (source,),
            ).fetchone()
            if row:
                candidates.append(dict(row))

        if not candidates:
            return {"error": f"source '{source}' not found in index (kind={kind})"}

        self_row = candidates[0]
        self_vec = code_db.get_embedding_vector(db, self_row["id"])
        if self_vec is None:
            return {"error": "embedding vector missing for source"}

        # 2. k-NN cross-kind
        kind_filter = None if filter_kind == "all" else filter_kind
        results = code_db.vector_search(
            db,
            query_vec=self_vec,
            limit=k + 1,  # +1 perché probabilmente self è in top
            kind_filter=kind_filter,
            exclude_id=self_row["id"],
        )

        # 3. Score = 1 - distance + filter min_score + preview
        neighbors = []
        for r in results:
            score = 1.0 - float(r["distance"])
            if score < min_score:
                continue
            edge_type = "semantic_strong" if score >= 0.8 else (
                "semantic_medium" if score >= 0.65 else "semantic_weak"
            )
            preview = (r["content"] or "")[:200].replace("\n", " ").strip()
            item = {
                "kind": r["kind"],
                "score": round(score, 4),
                "edge_type": edge_type,
                "preview": preview,
            }
            if r["kind"] == "wiki":
                item["slug"] = r["func_name"]
                item["page_type"] = r["lang"]
                item["file_path"] = r["file_path"]
            else:
                item["file_path"] = r["file_path"]
                item["func_name"] = r["func_name"]
                item["line_range"] = [r["line_start"], r["line_end"]]
                item["lang"] = r["lang"]
            neighbors.append(item)
            if len(neighbors) >= k:
                break

        return {
            "query": {
                "source": source,
                "kind": self_row["kind"],
                "resolved_path": self_row["file_path"],
                "self_id": self_row["id"],
            },
            "neighbors": neighbors,
            "stats": {
                "candidates_scanned": len(results),
                "above_threshold": len(neighbors),
                "min_score": min_score,
                "filter": filter_kind,
            },
        }
    finally:
        db.close()




# ============================================================
# ============================================================





















# ============================================================
# tool registry — JSON Schema per MCP tools/list
# ============================================================

# Fase 16 — Tool grouping for env-var-driven filtering.
# Set env ANJA_TOOL_GROUPS=memory,sessions,soul,user (comma-sep) to filter tools/list.
# Default (env vuoto): tutti i gruppi core. I gruppi hub rimossi nel v0.21
# (agents/tasks/workspace/kanban/goals/pp) in ANJA_TOOL_GROUPS → warning, ignorati:
# i .mcp.json vecchi avviano comunque il server (senza quei tool).
TOOL_GROUPS = {
    "memory": ["memory.recall", "memory.write", "memory.timeline"],
    "sessions": ["sessions.list", "sessions.read", "sessions.summarize"],
    "soul": ["soul.show", "soul.update"],
    "user": ["user.read", "user.update"],
    # Fase 16-bis — Skill lazy disclosure (Hermes-style)
    "skills": [
        "skill.list", "skill.load", "skill.read_file",
        "skill.save", "skill.patch", "skill.edit", "skill.delete",
        "skill.write_file", "skill.remove_file",
        "skill.history", "skill.rollback",
    ],
    # Fase P-Plugin — Wiki tools (read + 4 upsert + 2 special-file + log + 4 maintenance)
    "wiki": [
        "wiki.search", "wiki.read",
        "wiki.upsert_entity", "wiki.upsert_concept",
        "wiki.upsert_source", "wiki.upsert_analysis",
        "wiki.update_overview", "wiki.index_update",
        "wiki.log_append",
        "wiki.backlinks", "wiki.lint", "wiki.verify",
        "wiki.rename", "wiki.replace_links", "wiki.delete",
        "wiki.tree", "wiki.stats", "wiki.export", "wiki.attach_image",
    ],
    # F-TaskMgmt-Plugin — Roadmap task management (4° file speciale del wiki)
    "roadmap": [
        "roadmap.list", "roadmap.add", "roadmap.update",
        "roadmap.complete", "roadmap.block", "roadmap.archive",
    ],
    # F-CodeSearch — ricerca nel codebase ospitante (3 livelli: ripgrep/LLM rerank/vector)
    "code": ["code.search", "code.reindex", "code.status"],
    # Wiki embedding + semantic graph (cross-kind k-NN wiki ↔ code) + report + html viz
    "graph": [
        "wiki.embed", "graph.semantic_neighbors", "graph.report", "graph.html",
        "graph.search_text", "wiki.search_semantic", "sessions.search_semantic",
    ],
}


_HUB_GROUPS_MOVED = ("agents", "tasks", "workspace", "kanban", "goals", "pp")
_ALLOWED_CACHE: Optional[set] = None


def _allowed_tool_names() -> set:
    """Filter set basato su env ANJA_TOOL_GROUPS (calcolato una volta). Vuoto = tutti i core.
    Gruppo hub spostato o sconosciuto → warning su stderr, ignorato (mai crash)."""
    global _ALLOWED_CACHE
    if _ALLOWED_CACHE is not None:
        return _ALLOWED_CACHE
    raw = os.environ.get("ANJA_TOOL_GROUPS", "").strip()
    if not raw:
        _ALLOWED_CACHE = {t for g in TOOL_GROUPS.values() for t in g}
        return _ALLOWED_CACHE
    names = set()
    for g in [x.strip() for x in raw.split(",") if x.strip()]:
        if g in TOOL_GROUPS:
            names.update(TOOL_GROUPS[g])
        elif g in _HUB_GROUPS_MOVED:
            print(f"[anja_memory] WARN ANJA_TOOL_GROUPS: gruppo '{g}' non vive più in anjadev "
                  f"(v0.21): monta `anja_hub_runtime` (anja-hub/scripts/mcp_hub_runtime.py) — ignorato",
                  file=sys.stderr, flush=True)
        else:
            print(f"[anja_memory] WARN ANJA_TOOL_GROUPS: gruppo '{g}' sconosciuto (ignorato); "
                  f"validi: {','.join(TOOL_GROUPS)}", file=sys.stderr, flush=True)
    _ALLOWED_CACHE = names
    return names


TOOLS = [
    {
        "name": "memory.recall",
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
    {
        "name": "sessions.list",
        "description": "Lista sessioni recenti (chat + routine) ordinate per data desc. Ogni entry ha id, path, summary breve.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "Numero massimo di sessioni"},
            },
        },
    },
    {
        "name": "sessions.read",
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
    {
        "name": "soul.show",
        "description": "Read SOUL.md (identity + user preferences + memorable feedback + relationship facts).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "soul.update",
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
    {
        "name": "user.read",
        "description": "Read profilo utente — HOT (default, ~500 token, sempre sapevi questo già) o DETAIL on-demand. Usa DETAIL quando l'utente menziona qualcosa di personale che potrebbe essere registrato (gusti, hobby, persone, episodi). Se torni vuoto: profilo non esiste ancora.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Override del default_user (es. 'vincent'). Se omesso usa hub config.json default_user."},
                "detail": {"type": "boolean", "default": False, "description": "True per leggere USER-detail.md (gusti, hobby, persone, episodi); default False legge USER.md HOT."},
            },
        },
    },
    {
        "name": "user.update",
        "description": "Aggiorna profilo utente: append (default) o replace di una sezione. Per fatti permanenti core (ruolo, lingua, contesto operativo) usa detail=False. Per gusti/hobby/persone/episodi/preferenze granulari usa detail=true. Esempi: 'mi piace il jazz' → section='Gusti e preferenze', detail=true, mode='append'. 'cambio lingua a inglese' → section='Preferenze di comunicazione', detail=false, mode='replace'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Heading-level-2 (es. 'Gusti e preferenze', 'Persone importanti'). Creato se mancante."},
                "content": {"type": "string", "description": "Contenuto markdown da inserire."},
                "mode": {"type": "string", "enum": ["append", "replace"], "default": "append"},
                "detail": {"type": "boolean", "default": False, "description": "True per scrivere su USER-detail.md (gusti/hobby/persone); False per HOT (profilo core, raro)."},
                "slug": {"type": "string", "description": "Override default_user."},
            },
            "required": ["section", "content"],
        },
    },
    # ============================================================
    # ============================================================
    # Fase 16-bis — Skill lazy disclosure
    {
        "name": "skill.list",
        "description": "Catalog skills disponibili (workflow plugin/hub/workspace). Ritorna nomi + 1-line desc. Auto-iniettato nel system prompt; usa skill.load per body completo on-demand.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "skill.load",
        "description": "Carica body SKILL.md completo per uno skill specifico. Usa DOPO aver visto il catalog quando ti serve eseguire un workflow specifico (es. ingest, query, lint).",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name (es. 'ingest', 'query', 'lint')"}},
            "required": ["name"],
        },
    },
    {
        "name": "skill.read_file",
        "description": "Level 2: leggi un file di reference dentro la skill (references/, scripts/, templates/). Usa quando la SKILL.md menziona un file specifico (es. 'vedere references/api.md').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name"},
                "path": {"type": "string", "description": "Relative path inside skill dir (es. 'references/api.md')"},
            },
            "required": ["name", "path"],
        },
    },
    {
        "name": "skill.save",
        "description": "Crea una nuova skill (Hermes skill_manage analog). Usa quando un workflow non-triviale merita di essere salvato come memoria procedurale (5+ tool call, scoperta di pattern, correzione utente). Scope default da SCOPE env.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case slug (es. 'deploy-staging')"},
                "content": {"type": "string", "description": "intero SKILL.md (frontmatter YAML + body markdown)"},
                "scope": {"type": "string", "enum": ["project", "hub", "user-global"], "description": "default da ANJA_SCOPE"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "skill.patch",
        "description": "Patch mirato del SKILL.md via find/replace (preferito a edit, più sicuro). old_string deve essere unico nel file. Backup automatico in <skill>/.history/<ts>.SKILL.md prima della modifica (recoverable via skill.rollback).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["name", "old_string", "new_string"],
        },
    },
    {
        "name": "skill.history",
        "description": "Lista backup disponibili di una skill (file in <skill>/.history/). Backup creati automaticamente prima di ogni skill.patch/skill.edit/skill.save. Max 20 backup tenuti per skill (LRU).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "skill.rollback",
        "description": "Ripristina SKILL.md da un backup in .history/. Default: ultimo backup. Opzionale: timestamp specifico (formato YYYYMMDD-HHMMSS). Lo stato corrente viene a sua volta backuppato prima del rollback (reversibile).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "timestamp": {"type": "string", "description": "Opzionale, formato YYYYMMDD-HHMMSS. Vedi output skill.history per timestamps disponibili."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "skill.edit",
        "description": "Riscrive l'intero SKILL.md. Usa skill.patch quando possibile (più sicuro per modifiche piccole).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "skill.delete",
        "description": "Cancella una skill (rimuove la directory intera). Solo scope writable (project/hub/user-global).",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "skill.write_file",
        "description": "Scrive un file di reference dentro la skill (references/, scripts/, templates/). Usa per aggiungere doc esempi, template, script helper.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string", "description": "relative path inside skill dir"},
                "content": {"type": "string"},
            },
            "required": ["name", "path", "content"],
        },
    },
    {
        "name": "skill.remove_file",
        "description": "Rimuove un file di reference dalla skill. NON cancella la skill (per quello usa skill.delete).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["name", "path"],
        },
    },
    # Fase P-Plugin — Wiki tools (full-text search + read by slug, scope wiki/)
    {
        "name": "wiki.search",
        "description": (
            "📚 Cerca nelle pagine del wiki di questo scope (project/hub/workspace). "
            "IBRIDA di default: fonde keyword + ricerca semantica via Reciprocal Rank "
            "Fusion (trova sia match esatti di parole sia affinità concettuale). "
            "Degrada a solo keyword se manca l'embed index. Ritorna metadati strutturati "
            "(slug, type, path, rrf_score, channels, preview). "
            "USE FOR: 'cerca le entità che parlano di X', 'che concetti abbiamo su Y', 'fonti su Z'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query (parole chiave o frase concettuale)"},
                "type": {"type": "string", "enum": ["all", "entity", "concept", "source", "analysis", "session", "overview", "index"], "description": "Filtra per tipo pagina (default 'all')"},
                "limit": {"type": "integer", "description": "Max risultati (default 10)"},
                "mode": {"type": "string", "enum": ["hybrid", "keyword", "vector"], "description": "Canale di ricerca (default 'hybrid')"},
                "include_sessions": {"type": "boolean", "description": "Includi le pagine session nei risultati (default false)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wiki.find_duplicates",
        "description": (
            "🔎 Trova coppie di pagine wiki semanticamente troppo simili (candidati duplicati / "
            "da fondere o contraddittorie) via embeddings condivisi. Vede ciò che il match esatto "
            "non vede (es. 'auth-service' vs 'authentication'). USE per cleanup wiki / pre-ingest dedup."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "description": "Similarity coseno minima (default 0.85)"},
                "types": {"type": "array", "items": {"type": "string"}, "description": "page_type da considerare (default [entity, concept])"},
                "limit": {"type": "integer", "description": "Max coppie (default 20)"},
            },
        },
    },
    {
        "name": "wiki.read",
        "description": (
            "📚 Legge una pagina wiki per slug. Usa DOPO wiki.search per leggere il contenuto pieno. "
            "Cap a 10k chars (~2500 token) di default; passa max_chars per override."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug della pagina (es. 'auth-service'), .md opzionale"},
                "max_chars": {"type": "integer", "description": "Default 10000"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "wiki.upsert_entity",
        "description": (
            "📝 WIKI write: crea o aggiorna una entity page (modulo, servizio, persona, "
            "prodotto, sistema esterno) in wiki/entities/<slug>.md. Se la pagina esiste, "
            "fa MERGE delle sezioni passate (sostituisce solo quelle fornite, lascia "
            "intatte le altre, bump `updated`). Se non esiste, la crea con frontmatter "
            "completo. Usa per persistere decisioni strutturate su entità del progetto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Kebab-case (es. 'auth-service')"},
                "title": {"type": "string", "description": "Titolo leggibile (opt, default da slug humanized)"},
                "sections": {
                    "type": "object",
                    "description": "Dict {section_name: markdown_content}. Es. {'Sintesi': '...', 'Apparizioni': '- [[source-x]]: ...'}",
                    "additionalProperties": {"type": "string"},
                },
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Source slug da aggiungere a `sources` frontmatter (dedupe)"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "enum": ["draft", "stable", "deprecated"], "description": "Lifecycle (schema 1.1). Assente = stable"},
                "stale_after": {"type": "string", "description": "Data ISO YYYY-MM-DD oltre cui il contenuto va ri-verificato (schema 1.1). Usa per pagine volatili"},
            },
            "required": ["slug", "sections"],
        },
    },
    {
        "name": "wiki.upsert_concept",
        "description": (
            "📝 WIKI write: crea o aggiorna una concept page (pattern, idea, architettura, "
            "convenzione) in wiki/concepts/<slug>.md. Stesso MERGE-pattern di "
            "wiki.upsert_entity. Usa per pattern architetturali, convenzioni di progetto, "
            "idee astratte ricorrenti."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "sections": {"type": "object", "additionalProperties": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "enum": ["draft", "stable", "deprecated"], "description": "Lifecycle (schema 1.1). Assente = stable"},
                "stale_after": {"type": "string", "description": "Data ISO YYYY-MM-DD oltre cui il contenuto va ri-verificato (schema 1.1). Usa per pagine volatili"},
            },
            "required": ["slug", "sections"],
        },
    },
    {
        "name": "wiki.upsert_source",
        "description": (
            "📝 WIKI write: crea o aggiorna una source page in wiki/sources/<slug>.md "
            "(riassunto di una fonte ingerita: articolo, paper, doc, codebase-snapshot). "
            "Stesso MERGE-pattern di upsert_entity. Campi frontmatter extra opt-in: "
            "`source_path` (path al file in raw/), `subtype` (es. 'codebase-snapshot'), "
            "`git_sha`, `analyzed_at`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Es. '2026-04-26-karpathy-llm-wiki' o 'codebase-snapshot-2026-04-26'"},
                "title": {"type": "string"},
                "sections": {"type": "object", "additionalProperties": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_path": {"type": "string", "description": "Path al file originale in raw/ (es. '../../raw/articoli/x.md')"},
                "subtype": {"type": "string", "description": "Sottotipo (es. 'codebase-snapshot')"},
                "git_sha": {"type": "string", "description": "Solo per codebase-snapshot"},
                "analyzed_at": {"type": "string", "description": "ISO timestamp analisi (solo codebase-snapshot)"},
                "status": {"type": "string", "enum": ["draft", "stable", "deprecated"], "description": "Lifecycle (schema 1.1). Assente = stable"},
                "stale_after": {"type": "string", "description": "Data ISO YYYY-MM-DD oltre cui il contenuto va ri-verificato (schema 1.1). Usa per pagine volatili"},
            },
            "required": ["slug", "sections"],
        },
    },
    {
        "name": "wiki.upsert_analysis",
        "description": (
            "📝 WIKI write: crea o aggiorna una analysis page in wiki/analysis/<slug>.md "
            "(query trasformata in pagina, confronti, lint report). Stesso MERGE-pattern. "
            "Campi frontmatter extra: `question` (la query originale), `transient` "
            "(true per report cancellabili tipo lint)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "sections": {"type": "object", "additionalProperties": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Pagine wiki usate per sintetizzare"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "question": {"type": "string", "description": "La domanda originale che ha generato l'analisi"},
                "transient": {"type": "boolean", "description": "True se cancellabile (es. lint report)"},
                "status": {"type": "string", "enum": ["draft", "stable", "deprecated"], "description": "Lifecycle (schema 1.1). Assente = stable"},
                "stale_after": {"type": "string", "description": "Data ISO YYYY-MM-DD oltre cui il contenuto va ri-verificato (schema 1.1). Usa per pagine volatili"},
            },
            "required": ["slug", "sections"],
        },
    },
    {
        "name": "wiki.update_overview",
        "description": (
            "📝 WIKI write: aggiorna `wiki/overview.md` (sintesi di alto livello — 'cosa "
            "abbiamo capito'). Stesso MERGE-pattern: replace per sezione, lascia intatte "
            "le altre. Crea il file se manca. Usa SOLO quando la tesi corrente cambia in "
            "modo significativo (anti-pattern: aggiornarlo per ogni piccola cosa)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sections": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Dict {section_name: markdown_content}"},
                "title": {"type": "string", "description": "Default 'Overview'"},
            },
            "required": ["sections"],
        },
    },
    {
        "name": "wiki.index_update",
        "description": (
            "📝 WIKI write: manutenzione di `wiki/index.md`. Per una `category` (heading "
            "di livello 2, es. 'Sources', 'Entities', 'Concepts', 'Analysis') fa append "
            "(default, dedupe per riga esatta) o replace della lista entries. Crea index "
            "+ category se mancanti."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Nome sezione (es. 'Sources', 'Entities')"},
                "entries": {"type": "array", "items": {"type": "string"}, "description": "Righe markdown bullets (es. '- [[auth-service]] — servizio JWT')"},
                "mode": {"type": "string", "enum": ["append", "replace"], "default": "append"},
            },
            "required": ["category", "entries"],
        },
    },
    {
        "name": "wiki.backlinks",
        "description": (
            "🔍 WIKI nav: trova tutte le pagine che linkano allo slug via [[link]]. "
            "Riconosce [[slug]], [[slug|label]], [[slug#section]], [[slug#section|label]]. "
            "Usa per: capire connessioni, decidere se cancellare una pagina (vedere chi "
            "diventerebbe broken), navigare la rete del wiki."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug della pagina target"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "wiki.lint",
        "description": (
            "🔍 WIKI health check: orfani (pagine non linkate da nessuno), broken_links "
            "([[X]] dove X non esiste), stale (updated > N giorni ma ancora attive), "
            "frontmatter_issues (campi obbligatori mancanti: title/type/created/updated), "
            "trust (schema 1.1: pagine oltre stale_after + conteggio trust tier da verified). "
            "Usa periodicamente per non lasciar degradare il wiki."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["orphans", "broken_links", "stale", "frontmatter", "trust"]},
                    "description": "Subset di check (default: tutti)",
                },
                "stale_days": {"type": "integer", "default": 90, "description": "Soglia stale (default 90 giorni)"},
            },
        },
    },
    {
        "name": "wiki.verify",
        "description": (
            "\u2705 WIKI trust (schema 1.1): registra un evento di verifica su una pagina "
            "(frontmatter `verified`, append). USE quando l'utente CONFERMA che una pagina "
            "\u00e8 corretta/aggiornata ('s\u00ec \u00e8 giusto', 'confermo', review fatta). "
            "Separato da generated: chi scrive non \u00e8 chi conferma. Trust tier derivato: "
            "unverified \u2192 machine-confirmed \u2192 human-reviewed (se by \u00e8 human:<id>)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug della pagina da verificare"},
                "by": {"type": "string", "description": "Attore (default human:<utente>). Convenzione: human:<id> | process:<id> | <producer>/<version>"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "wiki.rename",
        "description": (
            "✏️ WIKI maintenance: rinomina una pagina preservando TUTTI i [[link]] "
            "cross-wiki (replace `[[old]]`, `[[old|label]]`, `[[old#section]]` "
            "→ `[[new...]]`). Validato kebab-case sul new_slug. Errore se target esiste."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "old_slug": {"type": "string"},
                "new_slug": {"type": "string"},
            },
            "required": ["old_slug", "new_slug"],
        },
    },
    {
        "name": "wiki.replace_links",
        "description": (
            "✏️ WIKI maintenance: replace `[[old]]` → `[[new]]` cross-wiki SENZA "
            "rinominare file. Utile per fixare convenzioni inconsistenti in massa "
            "(es. `[[entity-X]]` → `[[X]]`). Preserva label/anchor. dry_run=true "
            "per preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "old": {"type": "string"},
                "new": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["old", "new"],
        },
    },
    {
        "name": "wiki.delete",
        "description": (
            "🗑️ WIKI maintenance: cancella una pagina. SAFETY: confirm=false (default) "
            "ritorna preview con backlinks che diventerebbero rotti. confirm=true esegue. "
            "Suggerimento: se ha backlinks, considera wiki.rename per preservarli."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "confirm": {"type": "boolean", "default": False, "description": "Default false = preview. true = delete reale."},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "wiki.tree",
        "description": (
            "🌳 WIKI explore: struttura ad albero del wiki. Mostra 4 file speciali "
            "(index/log/overview/roadmap) + 5 categorie (entities/concepts/sources/analysis/sessions) "
            "con count e file list. Usa per orientarti veloce in un wiki sconosciuto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_per_category": {"type": "integer", "default": 50, "description": "Tronca liste lunghe"},
                "include_files": {"type": "boolean", "default": True, "description": "False = solo counts"},
            },
        },
    },
    {
        "name": "wiki.stats",
        "description": (
            "📊 WIKI explore: statistiche di salute del wiki. Counts per type, top-N "
            "pagine più linkate, top-N più recenti aggiornate, size totale, count entry "
            "log + count session. Usa per dashboard veloce dello stato del wiki."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "default": 10, "description": "Quanti item nelle top list"},
            },
        },
    },
    {
        "name": "wiki.attach_image",
        "description": (
            "🖼️ WIKI write: allega immagine a pagina entity/concept/source/analysis. "
            "Copia/scarica l'immagine in raw/<topic>/ + append `![alt](rel-path)` "
            "nella sezione target (default 'Diagrammi'). Supporta path locale o URL "
            "http/https. Update frontmatter.updated. Pattern QoL per ingest visuali: "
            "diagrammi architetturali, screenshots UI, foto whiteboarding, ecc."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug pagina target (deve esistere)"},
                "image_path": {"type": "string", "description": "Path locale o URL http/https"},
                "topic": {"type": "string", "description": "Sotto-cartella raw/ (default: slug stesso)"},
                "alt_text": {"type": "string", "description": "Alt text markdown (default: filename)"},
                "section": {"type": "string", "default": "Diagrammi", "description": "Sezione dove appendere (es. 'Diagrammi', 'Screenshots')"},
            },
            "required": ["slug", "image_path"],
        },
    },
    {
        "name": "wiki.export",
        "description": (
            "📦 WIKI export: dump dell'intero wiki in formato md (zip), json "
            "(dump strutturato per import/training/tool esterni), o html "
            "(static site con wikilinks risolti, browsable offline). Output "
            "default in .anjawiki/exports/wiki-export-<date>.<ext>. Sessions "
            "escluse di default (alto volume), abilita con include_sessions=true. "
            "Usa per backup atomico, sharing wiki snapshot, generazione static site."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["md", "json", "html"], "default": "json"},
                "output_path": {"type": "string", "description": "Path file output (opt). Default in .anjawiki/exports/"},
                "include_sessions": {"type": "boolean", "default": False, "description": "Include session files (alto volume)"},
            },
        },
    },
    {
        "name": "wiki.log_append",
        "description": (
            "📝 WIKI write: append entry strict-format a wiki/log.md (memoria episodica). "
            "Format auto: `## [YYYY-MM-DD] type | description`. Tipi convenzionali: "
            "init, init-analyze, ingest, query, refresh, lint, session, decision, "
            "milestone, note (free-form ma kebab-case enforced). Usa quando un evento "
            "merita tracciamento permanente nella storia del progetto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Tipo evento, kebab-case (es. 'decision', 'milestone')"},
                "description": {"type": "string", "description": "Descrizione 1-riga, max 200 char"},
            },
            "required": ["type", "description"],
        },
    },
    # F-TaskMgmt-Plugin — Roadmap tools (4° file speciale del wiki)
    {
        "name": "roadmap.list",
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
    # F-CodeSearch — code search nel codebase ospitante (3 livelli hybrid)
    {
        "name": "code.search",
        "description": (
            "🔎 CODE.SEARCH: ricerca nel codebase del progetto ospitante. "
            "USE PRIMA di Grep/Glob quando la query è SEMANTICA/CONCETTUALE: "
            "'dove gestiamo l'autenticazione', 'logica di retry', 'qualcosa "
            "che fa X', 'il code che parla con il DB', 'trova pattern simili'. "
            "USE quando l'utente cerca 'il codice che fa X' senza conoscere "
            "nomi esatti, o per codebase >5k LOC dove Grep porterebbe troppi hit. "
            "SKIP (usa Grep) quando la query è un NOME ESATTO di funzione/"
            "variabile/classe (es. 'trova authenticate()', 'usi di FOO_CONST'). "
            "3 livelli: 0=ripgrep+smart ranking (filename/func boost + git "
            "recency), 1=ripgrep top-50 + LLM haiku rerank semantico, 2=vector "
            "via sqlite-vec + embed provider (richiede `code.reindex`). "
            "Auto-detect: index disponibile→2, <5k LOC→0, altrimenti→1. "
            "Graceful fallback se livello superiore non disponibile."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query keyword o semantica"},
                "smart_level": {"type": "integer", "enum": [0, 1, 2], "description": "Override default auto-detect"},
                "limit": {"type": "integer", "default": 10},
                "lang": {"type": "string", "description": "Filtra per linguaggio (es. 'python', 'typescript')"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "code.reindex",
        "description": (
            "🔎 CODE: build/refresh vector index per il codebase del progetto in "
            "`.anjawiki/code-index.db`. Incremental di default (git diff vs last_indexed_sha), "
            "force=true per full re-index (drop & rebuild). Usa il provider configurato "
            "via ANJA_EMBED_PROVIDER (default openrouter)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False, "description": "true=full rebuild, false=incremental"},
                "limit": {"type": "integer", "description": "Max file da processare (debug)"},
            },
        },
    },
    {
        "name": "code.status",
        "description": (
            "🔎 CODE: stato del vector index del codebase. Restituisce: chunks totali, "
            "by-lang, provider/model usato, last_indexed_sha, size DB su disco. "
            "Restituisce indexed=false con hint se l'index non esiste ancora."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    # Wiki embedding + semantic graph cross-kind (wiki ↔ code)
    {
        "name": "wiki.embed",
        "description": (
            "🔗 GRAPH: embed incrementale delle pagine wiki nello stesso spazio vettoriale "
            "del code-index → abilita k-NN cross-kind (wiki ↔ code) via "
            "graph.semantic_neighbors. Dirty detection via content hash: re-run è no-op "
            "se nulla cambia. Per re-embed singolo file usa `single_page`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False, "description": "Re-embed all, ignore dirty check"},
                "include_sessions": {"type": "boolean", "default": True, "description": "Include wiki/sessions/"},
                "single_page": {"type": "string", "description": "Path assoluto a una singola .md (più rapido per refresh post-modifica)"},
            },
        },
    },
    {
        "name": "graph.report",
        "description": (
            "🔗 GRAPH: compute knowledge graph report (god nodes + clusters + surprise edges + "
            "wiki↔code anchors + orphans). Scrive `wiki/GRAPH_REPORT.md` agent-readable. "
            "USE FOR: 'panoramica del wiki', 'cosa è centrale qui?', 'cosa va consolidato?', "
            "'mappa code→entity automatica'. **Read this report instead of scanning the whole wiki** "
            "quando ti serve orientamento su un progetto sconosciuto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_god": {"type": "integer", "default": 8, "description": "Top-N god nodes per degree centrality"},
                "surprise_threshold": {"type": "number", "default": 0.72},
                "anchor_threshold": {"type": "number", "default": 0.6},
                "k_per_node": {"type": "integer", "default": 5},
                "include_sessions": {"type": "boolean", "default": False},
                "write": {"type": "boolean", "default": True, "description": "Scrive GRAPH_REPORT.md"},
                "verbose": {"type": "boolean", "default": False, "description": "Include semantic_neighbors + explicit_edges nel response"},
            },
        },
    },
    {
        "name": "graph.search_text",
        "description": (
            "🔗 GRAPH: semantic search cross-kind via query libera. Embedda la query "
            "nello stesso spazio del wiki+code → k-NN. USE FOR: 'trova pagine/file su X', "
            "domande senza nomi esatti, ricerca per intenzione. Filter: 'wiki' | 'code' | "
            "'sessions' | 'all'. Score = 1 - cosine_distance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query libera (lingua naturale)"},
                "filter": {"type": "string", "enum": ["wiki", "code", "sessions", "all"], "default": "all"},
                "k": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.5},
                "page_type": {"type": "string", "description": "Filtro extra wiki: 'entity' | 'concept' | 'source' | 'analysis' | 'session'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wiki.search_semantic",
        "description": (
            "📚 WIKI: semantic search del wiki (sessions escluse di default). "
            "Sugar di graph.search_text con filter='wiki'. USE FOR: 'pagine sul concetto X', "
            "'dove abbiamo discusso Y nel wiki'. Complementare a wiki.search (FTS testuale): "
            "wiki.search trova match esatti di parole, wiki.search_semantic trova affinità "
            "concettuale anche con vocabolario diverso."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.5},
                "page_type": {"type": "string", "description": "'entity' | 'concept' | 'source' | 'analysis'"},
                "include_sessions": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "sessions.search_semantic",
        "description": (
            "🧠 SESSIONS: semantic search nelle session journal. USE FOR: 'ricorda di cosa "
            "abbiamo parlato', 'session passata su X', 'quando ho discusso Y'. Richiede che "
            "wiki.embed sia stato fatto con include_sessions=True (default)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "graph.html",
        "description": (
            "🔗 GRAPH: genera `<wiki>/graph.html` standalone visualizer (Cytoscape). "
            "Single-file con dati embedded, sidebar search/filtri, click su nodo per dettagli. "
            "Apri nel browser, no server. Suggerisci all'utente di aprire il file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Output path override (default: <wiki>/graph.html)"},
            },
        },
    },
    {
        "name": "graph.semantic_neighbors",
        "description": (
            "🔗 GRAPH: k-NN nello spazio embedding unificato wiki+code. "
            "Trova pagine wiki e/o file di codice semanticamente simili a una source data. "
            "USE FOR: 'pagine simili a [[X]]', 'quale codice descrive questa entity?', "
            "'questa entity ha file di codice mappabili?', 'duplicati semantici nel wiki', "
            "'surprise edges (no [[wikilink]] esplicito ma alta similarity)'. "
            "Score = 1 - cosine_distance (1.0 identico, 0.0 ortogonale)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "slug pagina wiki ('auth-service' o 'entities:auth-service') OR file path codice ('src/auth.py')"},
                "kind": {"type": "string", "enum": ["auto", "wiki", "code"], "default": "auto"},
                "filter": {"type": "string", "enum": ["all", "wiki", "code"], "default": "all"},
                "k": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.55, "description": "Cosine similarity threshold"},
            },
            "required": ["source"],
        },
    },
]

TOOL_HANDLERS = {
    "memory.recall": tool_memory_recall,
    "memory.write": tool_memory_write,
    "memory.timeline": tool_memory_timeline,
    "sessions.list": tool_sessions_list,
    "sessions.read": tool_sessions_read,
    "sessions.summarize": tool_sessions_summarize,
    "soul.show": tool_soul_show,
    "soul.update": tool_soul_update,
    "user.read": tool_user_read,
    "user.update": tool_user_update,
    # Fase 16-bis — Skill lazy
    "skill.list": tool_skill_list,
    "skill.load": tool_skill_load,
    "skill.read_file": tool_skill_read_file,
    "skill.save": tool_skill_save,
    "skill.patch": tool_skill_patch,
    "skill.history": tool_skill_history,
    "skill.rollback": tool_skill_rollback,
    "skill.edit": tool_skill_edit,
    "skill.delete": tool_skill_delete,
    "skill.write_file": tool_skill_write_file,
    "skill.remove_file": tool_skill_remove_file,
    # Fase P-Plugin — Wiki tools
    "wiki.search": tool_wiki_search_hybrid,
    "wiki.search_keyword": tool_wiki_search,
    "wiki.find_duplicates": tool_wiki_find_duplicates,
    "wiki.read": tool_wiki_read,
    "wiki.upsert_entity": tool_wiki_upsert_entity,
    "wiki.upsert_concept": tool_wiki_upsert_concept,
    "wiki.upsert_source": tool_wiki_upsert_source,
    "wiki.upsert_analysis": tool_wiki_upsert_analysis,
    "wiki.update_overview": tool_wiki_update_overview,
    "wiki.index_update": tool_wiki_index_update,
    "wiki.log_append": tool_wiki_log_append,
    "wiki.backlinks": tool_wiki_backlinks,
    "wiki.lint": tool_wiki_lint,
    "wiki.verify": tool_wiki_verify,
    "wiki.rename": tool_wiki_rename,
    "wiki.replace_links": tool_wiki_replace_links,
    "wiki.delete": tool_wiki_delete,
    "wiki.tree": tool_wiki_tree,
    "wiki.stats": tool_wiki_stats,
    "wiki.export": tool_wiki_export,
    "wiki.attach_image": tool_wiki_attach_image,
    # F-TaskMgmt-Plugin — Roadmap tools
    "roadmap.list": tool_roadmap_list,
    "roadmap.add": tool_roadmap_add,
    "roadmap.update": tool_roadmap_update,
    "roadmap.complete": tool_roadmap_complete,
    "roadmap.block": tool_roadmap_block,
    "roadmap.archive": tool_roadmap_archive,
    # F-CodeSearch — Code search tools (3 livelli + index)
    "code.search": tool_code_search,
    "code.reindex": tool_code_reindex,
    "code.status": tool_code_status,
    "wiki.embed": tool_wiki_embed,
    "graph.semantic_neighbors": tool_graph_semantic_neighbors,
    "graph.report": tool_graph_report,
    "graph.html": tool_graph_html,
    "graph.search_text": tool_graph_search_text,
    "wiki.search_semantic": tool_wiki_search_semantic,
    "sessions.search_semantic": tool_sessions_search_semantic,
}


# ============================================================
# JSON-RPC 2.0 dispatcher
# ============================================================

def handle_request(req: dict) -> dict:
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")

    # initialize
    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTO_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        })

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "tools/list":
        allowed = _allowed_tool_names()
        filtered = [t for t in TOOLS if t["name"] in allowed]
        return _ok(req_id, {"tools": filtered})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        # Fase 16: rispetta filter group per call (security: client non può chiamare tool nascosti)
        if name not in _allowed_tool_names():
            return _err(req_id, -32601, f"tool '{name}' not available in this server instance")
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return _err(req_id, -32601, f"unknown tool: {name}")
        try:
            result = handler(args)
            # MCP tools/call response format: content array of TextContent
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            return _ok(req_id, {"content": content, "isError": "error" in result})
        except Exception as e:
            return _err(req_id, -32603, f"tool '{name}' failed: {type(e).__name__}: {e}")

    if method == "ping":
        return _ok(req_id, {})

    return _err(req_id, -32601, f"method not found: {method}")


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ============================================================
# stdio loop
# ============================================================

def main():
    # Stderr per debug; stdout solo JSON-RPC
    groups_env = os.environ.get("ANJA_TOOL_GROUPS", "")
    active_count = len(_allowed_tool_names())
    print(f"[anja_memory] starting (scope={SCOPE} root={ROOT} "
          f"groups={groups_env or 'ALL'} tools={active_count} "
          f"secrets_loaded={_SECRETS_LOADED})",
          file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = _err(None, -32700, f"parse error: {e}")
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue

        resp = handle_request(req)
        if resp is None:
            continue  # notification, no response
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
