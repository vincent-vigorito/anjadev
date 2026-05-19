#!/usr/bin/env python3
"""
session_start.py — hook eseguito a SessionStart di Claude Code.

NOOP funzionale (non scrive file): tutto il lavoro è in session_end.py.
Stampa info di benvenuto a stdout — CC potrebbe includerlo nel system context.

Schema sessions: file-per-session in wiki/sessions/<date>/<id>.md, scritto a SessionEnd
con metadata reali estratti dal transcript JSONL.
"""

import re
import sys
from pathlib import Path


LOG_HEADER_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\w[\w-]*) \| (.+?)$", re.M)


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


def main() -> None:
    found = find_anja_root(Path.cwd())
    if found is None:
        _suggest_anja_init(Path.cwd())
        sys.exit(0)
    root, kind, log_file = found

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


if __name__ == "__main__":
    main()
