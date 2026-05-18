#!/usr/bin/env python3
"""
status.py — riepilogo dello stato del wiki anja.

Restituisce JSON con: identità, conteggi pagine, ultime entry log,
ultima fonte ingerita, ultimo codebase-snapshot, ultimo lint report.

Usato da `/anja-status` per mostrare un riepilogo veloce all'utente.
"""

import argparse
import json
import re
import sys
from pathlib import Path

WIKI_DIRS = ("entities", "concepts", "sources", "analysis", "sessions")
ROOT_PAGES = ("index", "log", "overview")
LOG_HEADER_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\w[\w-]*) \| (.+?)$", re.M)


def parse_meta_yaml(meta_path):
    if not meta_path.is_file():
        return {}
    text = meta_path.read_text(encoding="utf-8")
    info = {}
    for key in ("id", "name", "type", "created", "init_mode"):
        m = re.search(rf'^\s*{key}:\s*"?([^"\n]+?)"?\s*$', text, re.M)
        if m:
            info[key] = m.group(1)
    return info


def count_pages(wiki_root):
    counts = {}
    for sub in WIKI_DIRS:
        d = wiki_root / sub
        counts[sub] = len(list(d.glob("*.md"))) if d.is_dir() else 0
    return counts


def last_log_entries(log_path, n):
    if not log_path.is_file():
        return []
    text = log_path.read_text(encoding="utf-8")
    entries = LOG_HEADER_RE.findall(text)
    return [
        {"date": d, "type": t, "description": desc}
        for d, t, desc in entries[-n:]
    ]


def extract_title(md_path):
    text = md_path.read_text(encoding="utf-8")
    m = re.search(r'^title:\s*"?([^"\n]+?)"?\s*$', text, re.M)
    return m.group(1) if m else md_path.stem


def latest_source(sources_dir):
    if not sources_dir.is_dir():
        return None
    files = sorted(
        f for f in sources_dir.glob("*.md")
        if not f.stem.startswith("codebase-snapshot-")
    )
    if not files:
        return None
    latest = files[-1]
    return {"slug": latest.stem, "title": extract_title(latest)}


def latest_snapshot(sources_dir):
    if not sources_dir.is_dir():
        return None
    files = sorted(sources_dir.glob("codebase-snapshot-*.md"))
    if not files:
        return None
    latest = files[-1]
    text = latest.read_text(encoding="utf-8")
    sha_m = re.search(r"^git_sha:\s*([a-f0-9]+)", text, re.M)
    return {
        "slug": latest.stem,
        "git_sha": sha_m.group(1) if sha_m else None,
    }


def latest_lint(analysis_dir):
    if not analysis_dir.is_dir():
        return None
    files = sorted(analysis_dir.glob("lint-*.md"))
    if not files:
        return None
    latest = files[-1]
    text = latest.read_text(encoding="utf-8")
    summary = {}
    for key in ("Errors", "Warnings", "Suggestions"):
        m = re.search(rf"-\s*{key}:\s*<?(\d+)>?", text)
        if m:
            summary[key.lower()] = int(m.group(1))
    return {"slug": latest.stem, "summary": summary}


def main():
    p = argparse.ArgumentParser(description="Status of anja wiki.")
    p.add_argument("--target", required=True, help="path to .anjawiki/")
    p.add_argument("--log-tail", type=int, default=5)
    args = p.parse_args()

    target = Path(args.target).resolve()
    if not target.is_dir():
        sys.exit(f"ERROR: target not found: {target}")

    meta = parse_meta_yaml(target / "meta.yaml")
    wiki = target / "wiki"
    counts = count_pages(wiki)
    root_pages_present = sum(
        1 for name in ROOT_PAGES if (wiki / f"{name}.md").is_file()
    )

    out = {
        "wiki_root": str(wiki),
        "identity": meta,
        "counts": counts,
        "total_pages": sum(counts.values()) + root_pages_present,
        "last_log_entries": last_log_entries(wiki / "log.md", args.log_tail),
        "latest_source": latest_source(wiki / "sources"),
        "latest_snapshot": latest_snapshot(wiki / "sources"),
        "latest_lint": latest_lint(wiki / "analysis"),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
