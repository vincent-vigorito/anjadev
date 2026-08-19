#!/usr/bin/env python3
"""compact_sessions.py — i journal fuori dal retrieval (F-anjadev-steward, pezzo B).

Niente LLM. Per ogni session file sotto `<root>/.anjawiki/wiki/sessions/` (project)
o `<root>/sessions/` (hub), esclusi `archive/` e i già `archived: true`:

  * **machine**  — sessione-macchina (journal_policy.is_programmatic: entrypoint sdk-*,
                   0 messaggi, 1 messaggio in <30 s): con `--purge-machine` viene
                   CANCELLATA (uno stub di una chiamata SDK da 8 s non serve a nessuno);
                   senza, è trattata come short.
  * **short**    — messages_user < 3 oppure durata < 5 min, più vecchia di
                   `--older-than` giorni (default 14): ARCHIVIATA.
  * **distilled**— `distilled: true` (lo steward ha già promosso) e più vecchia di
                   `--archive-distilled-after` giorni (default 14): ARCHIVIATA.
  * il resto (worth, recente, non distillata) NON si tocca: aspetta lo steward.

Archiviare = spostare in `sessions/archive/<date>/<id>.md` tenendo frontmatter
(+ `archived: true`), `## Summary` se c'era e il puntatore al transcript (lossless);
Stats / User prompts / Notes tagliati. `sessions.read` per id li trova ancora,
`sessions.list` / `wiki.search` / embed / `wiki.stats` non li contano.

Usage:
  python3 compact_sessions.py --root <project-o-hub> [--apply] [--purge-machine]
                              [--older-than 14] [--archive-distilled-after 14] [--json]
Dry-run di default: stampa cosa farebbe. Idempotente.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import journal_policy as jp  # noqa: E402


def sessions_root_for(root: Path) -> Path | None:
    proj = root / ".anjawiki" / "wiki" / "sessions"
    if proj.is_dir():
        return proj
    hub = root / "sessions"
    if hub.is_dir() and (root / "config" / "projects.json").is_file():
        return hub
    return proj if (root / ".anjawiki").is_dir() else None


def _transcript_entrypoint(path: str) -> str:
    """entrypoint dal transcript (prima riga che lo porta), '' se assente/sparito."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f):
                if i > 50:
                    break
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                if ev.get("entrypoint"):
                    return str(ev["entrypoint"])
    except Exception:
        pass
    return ""


def _age_days(fm: dict, path: Path, today: date) -> int:
    for key in ("date", "created"):
        v = (fm.get(key) or "").strip()
        try:
            return (today - date.fromisoformat(v[:10])).days
        except Exception:
            continue
    try:
        return (today - date.fromtimestamp(path.stat().st_mtime)).days
    except Exception:
        return 0


def classify(text: str, path: Path, today: date, older_than: int, distilled_after: int) -> tuple[str, str]:
    """→ (azione, motivo): keep | archive | machine."""
    st = jp.session_stats(text)
    fm = st["frontmatter"]
    if fm.get("archived") == "true":
        return "keep", "already-archived"
    age = _age_days(fm, path, today)
    ep = fm.get("entrypoint") or _transcript_entrypoint(fm.get("transcript_path", ""))
    why = jp.is_programmatic({}, ep, st["messages_user"], st["duration_sec"], fm.get("end_reason", ""))
    if why:
        return "machine", why
    if fm.get("distilled") == "true":
        return ("archive", f"distilled, {age}d") if age >= distilled_after else ("keep", f"distilled, recent {age}d")
    short = st["messages_user"] < 3 or st["duration_sec"] < 300
    if short:
        return ("archive", f"short ({st['messages_user']} msg, {int(st['duration_sec'])}s), {age}d") if age >= older_than \
            else ("keep", f"short, recent {age}d")
    return "keep", "worth (attende lo steward)"


def stub_for(text: str) -> str:
    """Stub archiviato: frontmatter + archived: true, Summary se c'era, puntatore transcript."""
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    fm_block = m.group(1) if m else ""
    fm_lines = [ln for ln in fm_block.splitlines() if not ln.startswith("archived:")]
    fm_lines.append("archived: true")
    fm_lines.append(f"archived_at: {datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S%z')}")
    title = next((ln[len("title:"):].strip() for ln in fm_lines if ln.startswith("title:")), "Session")
    out = ["---", *fm_lines, "---", "", f"# {title} (archived)", ""]
    sm = re.search(r"^## Summary\s*\n(.*?)(?=\n## |\Z)", text, re.M | re.S)
    body = (sm.group(1).strip() if sm else "")
    if body and not body.startswith("<!--"):
        out += ["## Summary", "", body, ""]
    tm = re.search(r"^transcript_path:\s*(.+)$", fm_block, re.M)
    if tm:
        out += ["## Transcript (drill-down lossless)", "", f"> `{tm.group(1).strip()}`", ""]
    return "\n".join(out)


def run(root: Path, apply: bool, purge_machine: bool, older_than: int, distilled_after: int) -> dict:
    sroot = sessions_root_for(root)
    rep = {"root": str(root), "sessions_root": str(sroot) if sroot else None, "apply": apply,
           "archived": [], "purged": [], "kept": 0, "by_reason": {}, "errors": []}
    if sroot is None or not sroot.is_dir():
        rep["errors"].append("sessions dir non trovata")
        return rep
    today = date.today()
    files = [f for f in sroot.rglob("*.md")
             if f.is_file() and not f.name.startswith(".") and "archive" not in f.relative_to(sroot).parts]
    for f in sorted(files):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            rep["errors"].append(f"{f}: {e}")
            continue
        action, why = classify(text, f, today, older_than, distilled_after)
        key = why.split(",")[0].split(" (")[0]
        rep["by_reason"][key] = rep["by_reason"].get(key, 0) + 1
        rel = str(f.relative_to(sroot))
        if action == "machine":
            if purge_machine:
                rep["purged"].append({"file": rel, "why": why})
                if apply:
                    f.unlink()
                continue
            # senza purge: trattata come short (stub)
            action = "archive"
            why = "machine→archive: " + why
        if action == "archive":
            day = f.parent.name if f.parent != sroot else (jp.parse_frontmatter(text).get("date") or "undated")
            dest = sroot / "archive" / day / f.name
            rep["archived"].append({"file": rel, "why": why})
            if apply:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(stub_for(text), encoding="utf-8")
                f.unlink()
            continue
        rep["kept"] += 1
    if apply:
        # cartelle-data rimaste vuote: via
        for d in sorted(sroot.iterdir()):
            if d.is_dir() and d.name != "archive" and not any(d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description="Compact dei journal (archive/purge), niente LLM. Dry-run di default.")
    ap.add_argument("--root", required=True, help="project (con .anjawiki/) o hub")
    ap.add_argument("--apply", action="store_true", help="scrivi (default: dry-run)")
    ap.add_argument("--purge-machine", action="store_true", help="CANCELLA le sessioni-macchina invece di archiviarle")
    ap.add_argument("--older-than", type=int, default=14, help="giorni: le short più vecchie vengono archiviate (14)")
    ap.add_argument("--archive-distilled-after", type=int, default=14, help="giorni: le distilled più vecchie vengono archiviate (14)")
    ap.add_argument("--json", action="store_true", help="report JSON su stdout")
    args = ap.parse_args()
    rep = run(Path(args.root).expanduser().resolve(), args.apply, args.purge_machine,
              args.older_than, args.archive_distilled_after)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        tag = "APPLY" if args.apply else "DRY-RUN"
        for it in rep["purged"]:
            print(f"  purge   {it['file']}  ({it['why']})")
        for it in rep["archived"]:
            print(f"  archive {it['file']}  ({it['why']})")
        print(f"[compact {tag}] archived={len(rep['archived'])} purged={len(rep['purged'])} kept={rep['kept']} "
              f"by_reason={rep['by_reason']} errors={len(rep['errors'])}")
        for e in rep["errors"]:
            print("  !", e)
    sys.exit(1 if rep["errors"] else 0)


if __name__ == "__main__":
    main()
