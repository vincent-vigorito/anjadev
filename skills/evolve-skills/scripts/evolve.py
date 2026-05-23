#!/usr/bin/env python3
"""evolve.py — Skill evolution review workflow (F-SkillEvolution-B).

Legge inbox `~/.anja/skill_evolution_inbox.jsonl`, processa le entry non
ancora analizzate, e per ognuna invoca un LLM (claude haiku via subprocess)
che valuta se è memorabile + propone patch.

Output proposals in `~/.anja/skill_evolution_proposals.jsonl`.

Usage:
    python3 evolve.py [--batch N] [--dry-run] [--marker-reset]

--batch N: max N entries elaborate (default 5)
--dry-run: non chiama LLM, mostra solo entries da processare
--marker-reset: ricomincia dall'inizio dell'inbox

Marker: `~/.anja/skill_evolution_last_processed.txt` (timestamp ISO ultimo entry processato).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


INBOX = Path.home() / ".anja" / "skill_evolution_inbox.jsonl"
PROPOSALS = Path.home() / ".anja" / "skill_evolution_proposals.jsonl"
MARKER = Path.home() / ".anja" / "skill_evolution_last_processed.txt"

REVIEW_PROMPT = """Sei l'assistente di skill evolution di Anja. Analizzi l'invocazione di una skill (input args + output preview) e decidi se c'è un pattern memorabile da salvare nella SKILL.md per migliorare future invocazioni.

INPUT entry:
{entry_json}

CRITERI per "memorabile":
- Edge case scoperto (skill fallita o ritornata vuota su input non triviale)
- Output sorprendente, controintuitivo o particolarmente utile
- Pattern args/parameter ottimale che andrebbe documentato
- Esempio concreto che illustra l'uso

NON memorabile:
- Esecuzione di routine senza nulla di nuovo
- Output normale, atteso, generico
- Errore di configurazione (es. API key mancante) — non è learning della skill

OUTPUT JSON STRICT (no markdown fences, no preamble):
{{
  "memorable": true|false,
  "rationale": "1 frase",
  "suggested_section": "Edge case | Best practice | Example | None",
  "patch_proposal": {{
    "section_to_append": "## <section name>\\n\\n<markdown content with example>"
  }}
}}

Se NOT memorable, output:
{{"memorable": false, "rationale": "..."}}
"""


def _read_inbox() -> list[dict]:
    if not INBOX.is_file():
        return []
    out = []
    try:
        for line in INBOX.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _read_marker() -> Optional[str]:
    if not MARKER.is_file():
        return None
    try:
        return MARKER.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_marker(ts: str) -> None:
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(ts, encoding="utf-8")


def _filter_unprocessed(entries: list[dict], last_ts: Optional[str]) -> list[dict]:
    if not last_ts:
        return entries
    return [e for e in entries if e.get("ts", "") > last_ts]


def _invoke_review_llm(entry: dict, model: str = "haiku") -> Optional[dict]:
    """Invoca claude haiku via subprocess (CLI). Output JSON proposal o None."""
    prompt = REVIEW_PROMPT.format(entry_json=json.dumps(entry, ensure_ascii=False))
    try:
        # claude CLI usage: -p <prompt> --model <name> --output-format json
        r = subprocess.run(
            ["claude", "--model", model, "--print", prompt],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return {"error": f"claude CLI failed: {r.stderr[:200]}"}
        out = r.stdout.strip()
        # Strip markdown fence se presente
        if out.startswith("```"):
            lines = out.split("\n")
            out = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            return {"error": f"non-JSON output: {e}", "raw": out[:300]}
    except FileNotFoundError:
        return {"error": "claude CLI not in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": "review timeout"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _append_proposal(entry: dict, review: dict) -> None:
    PROPOSALS.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_entry_ts": entry.get("ts"),
        "skill": entry.get("skill"),
        "memorable": review.get("memorable", False),
        "rationale": review.get("rationale", ""),
        "suggested_section": review.get("suggested_section"),
        "patch_proposal": review.get("patch_proposal"),
        "review_error": review.get("error"),
        "applied": False,
    }
    with PROPOSALS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--marker-reset", action="store_true")
    ap.add_argument("--model", default="haiku")
    args = ap.parse_args()

    if args.marker_reset and MARKER.is_file():
        MARKER.unlink()

    entries = _read_inbox()
    last_ts = _read_marker()
    unprocessed = _filter_unprocessed(entries, last_ts)

    if not unprocessed:
        print(json.dumps({"status": "no_new_entries", "total_inbox": len(entries),
                           "last_processed": last_ts}, ensure_ascii=False))
        return

    batch = unprocessed[:args.batch]
    print(json.dumps({"status": "processing", "batch_size": len(batch),
                       "total_unprocessed": len(unprocessed)}, ensure_ascii=False), flush=True)

    if args.dry_run:
        for e in batch:
            print(f"  WOULD REVIEW: {e['skill']} @ {e['ts']} — args: {e.get('args_raw','')[:80]}")
        return

    new_marker = last_ts
    proposals_count = 0
    memorable_count = 0
    for e in batch:
        print(f"  reviewing: {e['skill']} @ {e['ts']}...", flush=True)
        review = _invoke_review_llm(e, model=args.model)
        if review is None:
            review = {"error": "review returned None"}
        _append_proposal(e, review)
        proposals_count += 1
        if review.get("memorable"):
            memorable_count += 1
        new_marker = max(new_marker or "", e["ts"])

    if new_marker:
        _write_marker(new_marker)

    print(json.dumps({
        "status": "done",
        "reviewed": proposals_count,
        "memorable": memorable_count,
        "marker": new_marker,
        "proposals_file": str(PROPOSALS),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
