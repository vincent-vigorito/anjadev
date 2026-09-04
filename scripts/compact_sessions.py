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
  * **worth stale** — worth ma mai distillata, più vecchia di `archive_worth_after_days`
                   (default 30): ARCHIVIATA col summary (v0.30: prima restava attiva per sempre
                   se cadeva fuori dalla finestra dello steward).
  * il resto (worth recente, non distillata) NON si tocca: aspetta lo steward.
Archivio (v0.30): gli stub SENZA summary più vecchi di `purge_archive_after_days` (180)
vengono cancellati; oltre `archive_max` (500) stub, via i più vecchi senza summary (cap
soft: uno stub con summary non si cancella mai). Policy in `.anjawiki/config.json`
(`sessions: {...}`), override da flag; a ogni `--apply` scrive `last_compact` in meta.yaml
e `.anjawiki/.compact-last` (il lazy start di session_start lo usa per girare ogni 24h).

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
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import journal_policy as jp  # noqa: E402

POLICY_DEFAULTS = {
    "archive_short_after_days": 14,      # short (< 3 msg o < 5 min) → archive
    "archive_distilled_after_days": 14,  # distilled dallo steward → archive
    "archive_worth_after_days": 30,      # worth mai distillata → archive (col summary)
    "purge_archive_after_days": 180,     # stub archiviati SENZA summary → cancellati
    "archive_max": 500,                  # cap soft sull'archivio (via i più vecchi senza summary)
}


def load_policy(root: Path) -> dict:
    """POLICY_DEFAULTS ← .anjawiki/config.json["sessions"] ← env ANJA_STEWARD_ARCHIVE_AFTER (legacy,
    vale per short+distilled)."""
    pol = dict(POLICY_DEFAULTS)
    cfg = root / ".anjawiki" / "config.json"
    if cfg.is_file():
        try:
            sect = json.loads(cfg.read_text(encoding="utf-8")).get("sessions") or {}
            for k in pol:
                if isinstance(sect.get(k), int) and sect[k] >= 0:
                    pol[k] = sect[k]
        except Exception:
            pass
    legacy = os.environ.get("ANJA_STEWARD_ARCHIVE_AFTER")
    if legacy and legacy.isdigit():
        pol["archive_short_after_days"] = pol["archive_distilled_after_days"] = int(legacy)
    return pol


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


def classify(text: str, path: Path, today: date, older_than: int, distilled_after: int,
             worth_after: int | None = None) -> tuple[str, str]:
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
    if worth_after is not None and age >= worth_after:
        return "archive", f"worth stale {age}d (mai distillata, col summary)"
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


_PLACEHOLDER_RE = re.compile(r"<!--.*?-->", re.S)


def _has_summary(text: str) -> bool:
    m = re.search(r"^## Summary\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    body = _PLACEHOLDER_RE.sub("", m.group(1)).strip() if m else ""
    return bool(body)


def purge_archive(sroot: Path, today: date, purge_after: int, archive_max: int, apply: bool, rep: dict,
                  budget: int | None = None) -> None:
    """Ritenzione dell'archivio: stub senza summary più vecchi di purge_after → via; oltre
    archive_max stub → via i più vecchi senza summary (cap soft)."""
    adir = sroot / "archive"
    if not adir.is_dir():
        return
    stubs = []
    for f in adir.rglob("*.md"):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = jp.parse_frontmatter(text)
        stubs.append((_age_days(fm, f, today), _has_summary(text), f))
    stubs.sort(key=lambda t: -t[0])          # dal più vecchio
    doomed = []
    for age, has_sum, f in stubs:
        if not has_sum and age >= purge_after:
            doomed.append((f, f"archive stub senza summary, {age}d >= {purge_after}"))
    doomed_paths = {d[0] for d in doomed}
    remaining = [t for t in stubs if t[2] not in doomed_paths]
    over = len(remaining) - archive_max
    if over > 0:
        for age, has_sum, f in remaining:
            if over <= 0:
                break
            if not has_sum:
                doomed.append((f, f"archive oltre il cap {archive_max} (stub senza summary, {age}d)"))
                over -= 1
        if over > 0:
            rep["archive_over_cap"] = over      # restano solo stub con summary: non si toccano
    for f, why in doomed:
        if budget is not None and rep["actions"] >= budget:
            rep["budget_exhausted"] = True
            break
        rep["purged_archive"].append({"file": str(f.relative_to(sroot)), "why": why})
        rep["actions"] += 1
        if apply:
            try:
                f.unlink()
            except Exception as e:
                rep["errors"].append(f"{f}: {e}")
    if apply:
        for d in sorted(adir.iterdir()):
            if d.is_dir() and not any(d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)


def mark_last_compact(root: Path, when: datetime | None = None) -> None:
    """`last_compact: <iso>` in .anjawiki/meta.yaml (chiave top-level, upsert) + `.compact-last` epoch."""
    when = when or datetime.now().astimezone()
    iso = when.strftime("%Y-%m-%dT%H:%M:%S%z")
    state = root / ".anjawiki" if (root / ".anjawiki").is_dir() else root
    try:
        (state / ".compact-last").write_text(str(when.timestamp()), encoding="utf-8")
    except Exception:
        pass
    meta = state / "meta.yaml"
    if not meta.is_file():
        return
    try:
        lines = [ln for ln in meta.read_text(encoding="utf-8").splitlines() if not ln.startswith("last_compact:")]
        lines.append(f'last_compact: "{iso}"')
        meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def run(root: Path, apply: bool, purge_machine: bool, older_than: int | None = None,
        distilled_after: int | None = None, worth_after: int | None = None, purge_after: int | None = None,
        archive_max: int | None = None, budget: int | None = None) -> dict:
    """Soglie None → policy (config.json / default). `budget` = max azioni per run (lazy start)."""
    pol = load_policy(root)
    older_than = pol["archive_short_after_days"] if older_than is None else older_than
    distilled_after = pol["archive_distilled_after_days"] if distilled_after is None else distilled_after
    worth_after = pol["archive_worth_after_days"] if worth_after is None else worth_after
    purge_after = pol["purge_archive_after_days"] if purge_after is None else purge_after
    archive_max = pol["archive_max"] if archive_max is None else archive_max
    sroot = sessions_root_for(root)
    rep = {"root": str(root), "sessions_root": str(sroot) if sroot else None, "apply": apply,
           "policy": {"archive_short_after_days": older_than, "archive_distilled_after_days": distilled_after,
                      "archive_worth_after_days": worth_after, "purge_archive_after_days": purge_after,
                      "archive_max": archive_max},
           "archived": [], "purged": [], "purged_archive": [], "kept": 0, "by_reason": {}, "errors": [],
           "actions": 0}
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
        action, why = classify(text, f, today, older_than, distilled_after, worth_after)
        key = why.split(",")[0].split(" (")[0]
        rep["by_reason"][key] = rep["by_reason"].get(key, 0) + 1
        rel = str(f.relative_to(sroot))
        if action != "keep" and budget is not None and rep["actions"] >= budget:
            rep["budget_exhausted"] = True
            rep["kept"] += 1
            continue
        if action != "keep":
            rep["actions"] += 1
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
    purge_archive(sroot, today, purge_after, archive_max, apply, rep, budget)
    if apply:
        # cartelle-data rimaste vuote: via
        for d in sorted(sroot.iterdir()):
            if d.is_dir() and d.name != "archive" and not any(d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)
        mark_last_compact(root)
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description="Compact dei journal (archive/purge), niente LLM. Dry-run di default.")
    ap.add_argument("--root", required=True, help="project (con .anjawiki/) o hub")
    ap.add_argument("--apply", action="store_true", help="scrivi (default: dry-run)")
    ap.add_argument("--purge-machine", action="store_true", help="CANCELLA le sessioni-macchina invece di archiviarle")
    ap.add_argument("--older-than", type=int, default=None, help="giorni: short → archive (policy: 14)")
    ap.add_argument("--archive-distilled-after", type=int, default=None, help="giorni: distilled → archive (policy: 14)")
    ap.add_argument("--archive-worth-after", type=int, default=None, help="giorni: worth mai distillata → archive (policy: 30)")
    ap.add_argument("--purge-archive-after", type=int, default=None, help="giorni: stub archiviati senza summary → cancellati (policy: 180)")
    ap.add_argument("--archive-max", type=int, default=None, help="cap soft sull'archivio (policy: 500)")
    ap.add_argument("--budget", type=int, default=None, help="max azioni per run (lazy start: 20)")
    ap.add_argument("--json", action="store_true", help="report JSON su stdout")
    args = ap.parse_args()
    rep = run(Path(args.root).expanduser().resolve(), args.apply, args.purge_machine,
              args.older_than, args.archive_distilled_after, args.archive_worth_after,
              args.purge_archive_after, args.archive_max, args.budget)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        tag = "APPLY" if args.apply else "DRY-RUN"
        for it in rep["purged"]:
            print(f"  purge   {it['file']}  ({it['why']})")
        for it in rep["archived"]:
            print(f"  archive {it['file']}  ({it['why']})")
        for it in rep["purged_archive"]:
            print(f"  purge-archive {it['file']}  ({it['why']})")
        print(f"[compact {tag}] archived={len(rep['archived'])} purged={len(rep['purged'])} "
              f"purged_archive={len(rep['purged_archive'])} kept={rep['kept']} "
              f"by_reason={rep['by_reason']} errors={len(rep['errors'])}")
        for e in rep["errors"]:
            print("  !", e)
    sys.exit(1 if rep["errors"] else 0)


if __name__ == "__main__":
    main()
