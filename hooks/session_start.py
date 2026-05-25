#!/usr/bin/env python3
"""
session_start.py — hook eseguito a SessionStart di Claude Code.

NOOP funzionale (non scrive file): tutto il lavoro è in session_end.py.
Stampa info di benvenuto a stdout — CC potrebbe includerlo nel system context.

Schema sessions: file-per-session in wiki/sessions/<date>/<id>.md, scritto a SessionEnd
con metadata reali estratti dal transcript JSONL.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path


LOG_HEADER_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\w[\w-]*) \| (.+?)$", re.M)

# Auto-summary sweep config
_SUMMARY_MIN_USER_MSGS = int(os.environ.get("ANJA_SUMMARY_MIN_MSGS", "15"))
_SUMMARY_MAX_AGE_H = 48      # solo session recenti (evita backlog infinito)
_SUMMARY_MAX_SPAWN = 3       # cap spawn per SessionStart (no flood)


def find_anja_root(start: Path):
    """Risale dalla cwd cercando un anja root.
    Ritorna (root, kind, log_path) o None. Kind: project | hub | agent.
    """
    import json
    cur = start.resolve()
    for parent in [cur] + list(cur.parents):
        # Agent marker
        if parent.parent.name == "agents" and (parent / "config.json").is_file():
            try:
                cfg = json.loads((parent / "config.json").read_text(encoding="utf-8"))
                if cfg.get("name") == parent.name or cfg.get("scope") in ("hub", "agent"):
                    # Agent log: prefer hub-level cross/log.md (l'agent vede log hub)
                    hub_log = parent.parent.parent / "cross" / "log.md"
                    return (parent, "agent", hub_log)
            except Exception:
                pass
        anjawiki = parent / ".anjawiki"
        if anjawiki.is_dir() and (anjawiki / "meta.yaml").is_file():
            return (parent, "project", anjawiki / "wiki" / "log.md")
        if (parent / "config" / "projects.json").is_file():
            return (parent, "hub", parent / "cross" / "log.md")
    return None


def _load_roadmap_focus(wiki_root: Path) -> list[str]:
    """Carica top-5 task P0/P1 open + 2 in-progress da roadmap.md (se esiste).

    Restituisce lista di righe formattate pronte da stampare. Lista vuota se
    roadmap.md non esiste o roadmap_io non è importabile.
    """
    roadmap_file = wiki_root / "roadmap.md"
    if not roadmap_file.is_file():
        return []
    try:
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        import roadmap_io as rio
    except Exception:
        return []

    try:
        data = rio.parse_roadmap(roadmap_file)
    except Exception:
        return []

    # Top-5 P0/P1 open + 2 in-progress
    open_p01 = []
    in_progress = []
    for sec_tasks in data["sections"].values():
        for t in sec_tasks:
            status = t.get("status", "open")
            priority = t.get("priority")
            if status == "open" and priority in ("P0", "P1"):
                open_p01.append(t)
            elif status == "in_progress":
                in_progress.append(t)

    # Sort: P0 prima, poi P1, poi added asc
    open_p01.sort(key=lambda t: (t.get("priority") or "P9", t.get("added", "")))
    in_progress.sort(key=lambda t: t.get("started", ""))

    lines = []
    if open_p01[:5]:
        lines.append("  Focus aperti (top-5 P0/P1):")
        for t in open_p01[:5]:
            prio = t.get("priority", "")
            owner = f" @{t['owner']}" if t.get("owner") else ""
            est = f" ({t['est']})" if t.get("est") else ""
            lines.append(f"    [{prio}] {t.get('id')} — {t['title']}{est}{owner}")
    if in_progress[:2]:
        lines.append("  In-progress:")
        for t in in_progress[:2]:
            owner = f" @{t['owner']}" if t.get("owner") else ""
            started = f" (started {t['started']})" if t.get("started") else ""
            lines.append(f"    [~] {t.get('id')} — {t['title']}{started}{owner}")
    return lines


def _print_skills_catalog(root: Path, kind: str) -> None:
    """Stampa Level 0 catalog skill (project + user-global) per injection nel context.

    Hermes-aligned: la lista è ~5-15 righe, body completo via tool skill.load on-demand.
    """
    try:
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        import skill_parser  # type: ignore
    except Exception:
        return

    sources: list[tuple[str, Path]] = []
    if kind == "project":
        sources.append(("project", root / ".anjawiki" / "skills"))
    elif kind == "hub":
        sources.append(("hub", root / "skills"))
    sources.append(("user-global", Path.home() / ".anja" / "skills"))

    seen: dict[str, tuple[str, dict]] = {}
    for label, src in sources:
        if not src.is_dir():
            continue
        for sub in sorted(src.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            md = sub / "SKILL.md"
            if not md.is_file():
                continue
            parsed = skill_parser.parse_skill_md(md)
            name = parsed.get("name")
            if not name or name in seen:
                continue
            seen[name] = (label, parsed)

    if not seen:
        return
    print("  Skill anja disponibili (use `skill.load <name>` per il body):")
    for name in sorted(seen):
        label, parsed = seen[name]
        desc = (parsed.get("description") or "")[:80]
        cat = parsed.get("category") or ""
        cat_part = f" [{cat}]" if cat else ""
        print(f"    - [{label}]{cat_part} {name} — {desc}")


def _suggest_anja_init(cwd: Path) -> None:
    """Print onboarding nudge se cwd è un progetto plausibile (git repo o code presence)
    senza .anjawiki/. Idempotente per cwd: marker in ~/.anja-nudged/.

    Skip se:
    - cwd è $HOME o root dir
    - già nudgato per quel cwd
    - non sembra un progetto (no .git, no file di codice)
    """
    import hashlib
    cwd = cwd.resolve()
    if cwd == Path.home() or str(cwd) == "/":
        return
    # Indicatori "progetto reale": git repo o file di codice
    if not ((cwd / ".git").exists() or any(
        cwd.glob(f"*.{ext}") for ext in ("py", "ts", "tsx", "js", "go", "rs", "java")
    )):
        return
    nudge_dir = Path.home() / ".anja-nudged"
    nudge_dir.mkdir(exist_ok=True)
    cwd_hash = hashlib.sha1(str(cwd).encode()).hexdigest()[:12]
    marker = nudge_dir / cwd_hash
    if marker.exists():
        return
    marker.write_text(str(cwd) + "\n", encoding="utf-8")
    print(f"[anja] Questo progetto ({cwd.name}) non ha ancora un wiki anja.", file=sys.stderr)
    print(f"[anja] Per inizializzarlo: /anja-init --type dev", file=sys.stderr)
    print(f"[anja] (suggerimento mostrato 1 volta sola — marker in ~/.anja-nudged/)", file=sys.stderr)


def _sessions_root_for(root: Path, kind: str) -> Path:
    """Deriva la sessions dir dallo scope. Mirror della logica in session_end.py."""
    if kind == "project":
        return root / ".anjawiki" / "wiki" / "sessions"
    return root / "sessions"  # hub + agent


def _summary_is_placeholder(text: str) -> bool:
    m = re.search(r"^## Summary\s*\n(.*?)(?=\n## |\Z)", text, re.M | re.DOTALL)
    if not m:
        return False  # niente sezione Summary → non è un nostro session file
    body = m.group(1).strip()
    return (not body) or body.startswith("<!--")


def _sweep_pending_summaries(root: Path, kind: str) -> None:
    """Recupera i summary mancanti: spawn bg summarize per session recenti, lunghe,
    e ancora con placeholder. Robusto vs il detached da session_end (che CC killa a
    /exit): qui l'ambiente è stabile (sessione vecchia già morta, nuova in boot).

    Filtri: mtime < 48h, messages_user >= soglia (default 15), Summary placeholder.
    Cap a 3 spawn per SessionStart. Opt-out via ANJA_AUTO_SUMMARY=0.
    """
    if os.environ.get("ANJA_AUTO_SUMMARY", "1") == "0":
        return
    script = Path(__file__).resolve().parent.parent / "scripts" / "summarize_session_bg.py"
    if not script.is_file():
        return
    sessions_root = _sessions_root_for(root, kind)
    if not sessions_root.is_dir():
        return

    cutoff = time.time() - _SUMMARY_MAX_AGE_H * 3600
    candidates = []
    for f in sessions_root.rglob("*.md"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if not _summary_is_placeholder(text):
            continue
        m = re.search(r"^messages_user:\s*(\d+)", text, re.M)
        n_user = int(m.group(1)) if m else 0
        if n_user < _SUMMARY_MIN_USER_MSGS:
            continue
        candidates.append((f.stat().st_mtime, f))

    # Più recenti prima, cap a MAX_SPAWN
    candidates.sort(reverse=True)
    for _, f in candidates[:_SUMMARY_MAX_SPAWN]:
        try:
            subprocess.Popen(
                [sys.executable, str(script), "--session-file", str(f)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except Exception:
            pass


def main() -> None:
    found = find_anja_root(Path.cwd())
    if found is None:
        _suggest_anja_init(Path.cwd())
        sys.exit(0)
    root, kind, log_file = found

    # Safety net: recupera summary mancanti delle sessioni recenti lunghe.
    # Spostato qui da session_end perché il detached spawn a /exit veniva killato da CC.
    _sweep_pending_summaries(root, kind)

    last_entries = []
    if log_file.is_file():
        text = log_file.read_text(encoding="utf-8")
        entries = LOG_HEADER_RE.findall(text)
        last_entries = entries[-5:]

    print(f"[anja] Sessione aperta ({kind}): {root.name}")
    if last_entries:
        print("  Ultime 5 entry log:")
        for d, t, desc in last_entries:
            print(f"    [{d}] {t} | {desc}")

    # F-TaskMgmt-Plugin: focus roadmap (top-5 P0/P1 open + 2 in-progress)
    wiki_root = log_file.parent if log_file.parent.name == "wiki" else None
    if wiki_root:
        focus_lines = _load_roadmap_focus(wiki_root)
        for line in focus_lines:
            print(line)

    # Level 0 catalog skill anja (project + user-global)
    _print_skills_catalog(root, kind)


if __name__ == "__main__":
    main()
