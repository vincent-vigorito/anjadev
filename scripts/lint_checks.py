#!/usr/bin/env python3
"""
lint_checks.py — check meccanici per anja wiki.

Outputs JSON to stdout with issues found. The agent reads it and adds
semantic checks (repeated concepts without page, contradictions, index
alignment) that require LLM judgment.

Checks meccanici:
  - broken-link: [[X]] verso pagine inesistenti (severity: error)
  - orphan: pagine senza inbound link (severity: warning)
  - missing-frontmatter: pagina senza blocco YAML (severity: error)
  - incomplete-frontmatter: chiavi obbligatorie mancanti (severity: warning)
  - stale: updated > N giorni per pagine attive (severity: suggestion)
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

WIKI_DIRS = ("entities", "concepts", "sources", "analysis", "sessions")
ROOT_PAGES = ("index", "log", "overview")
SKIP_ORPHAN = {"index", "log", "overview"}
WIKILINK_RE = re.compile(r"\[\[([a-z0-9\-]+)(?:#[^\]]*)?(?:\|[^\]]*)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def collect_pages(wiki_root: Path) -> dict:
    """Map slug → (Path, subdir-name) for all wiki pages."""
    pages = {}
    for sub in WIKI_DIRS:
        d = wiki_root / sub
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            pages[f.stem] = (f, sub)
    for name in ROOT_PAGES:
        f = wiki_root / f"{name}.md"
        if f.is_file():
            pages[name] = (f, "root")
    return pages


def extract_links(text: str) -> set:
    return set(WIKILINK_RE.findall(text))


def parse_frontmatter(text: str) -> tuple:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return False, ""
    return True, m.group(1)


def check_links_and_orphans(pages: dict) -> tuple:
    inbound = {slug: 0 for slug in pages}
    broken = []
    for slug, (path, _sub) in pages.items():
        text = path.read_text(encoding="utf-8")
        for target in extract_links(text):
            if target in pages:
                inbound[target] += 1
            else:
                broken.append({
                    "severity": "error",
                    "type": "broken-link",
                    "page": slug,
                    "target": target,
                    "message": f"[[{target}]] referenced in '{slug}' but no such page exists",
                })
    orphans = []
    for slug, count in inbound.items():
        if count > 0 or slug in SKIP_ORPHAN:
            continue
        sub = pages[slug][1]
        if sub == "sessions":
            continue
        orphans.append({
            "severity": "warning",
            "type": "orphan",
            "page": slug,
            "message": f"page '{slug}' has no inbound links",
        })
    return broken, orphans


def check_frontmatter(pages: dict) -> list:
    issues = []
    required = ("title", "type")
    for slug, (path, _sub) in pages.items():
        text = path.read_text(encoding="utf-8")
        present, block = parse_frontmatter(text)
        if not present:
            issues.append({
                "severity": "error",
                "type": "missing-frontmatter",
                "page": slug,
                "message": f"page '{slug}' missing YAML frontmatter",
            })
            continue
        missing = [k for k in required if not re.search(rf"^{k}:", block, re.M)]
        if missing:
            issues.append({
                "severity": "warning",
                "type": "incomplete-frontmatter",
                "page": slug,
                "missing": missing,
                "message": f"page '{slug}' frontmatter missing keys: {', '.join(missing)}",
            })
    return issues


def check_stale(pages: dict, days: int) -> list:
    cutoff = date.today() - timedelta(days=days)
    issues = []
    for slug, (path, sub) in pages.items():
        if slug in SKIP_ORPHAN or sub == "sessions":
            continue
        text = path.read_text(encoding="utf-8")
        present, block = parse_frontmatter(text)
        if not present:
            continue
        m = re.search(r"^updated:\s*['\"]?(\d{4}-\d{2}-\d{2})", block, re.M)
        if not m:
            continue
        try:
            updated = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if updated < cutoff:
            issues.append({
                "severity": "suggestion",
                "type": "stale",
                "page": slug,
                "updated": updated.isoformat(),
                "days_old": (date.today() - updated).days,
                "message": f"page '{slug}' not updated since {updated.isoformat()} ({(date.today() - updated).days} days ago)",
            })
    return issues


def main() -> None:
    p = argparse.ArgumentParser(description="Mechanical lint checks for anja wiki.")
    p.add_argument("--wiki-root", required=True, help="path to .anjawiki/wiki/")
    p.add_argument("--stale-days", type=int, default=90)
    args = p.parse_args()

    wiki_root = Path(args.wiki_root).resolve()
    if not wiki_root.is_dir():
        sys.exit(f"ERROR: wiki root not found: {wiki_root}")

    pages = collect_pages(wiki_root)
    broken, orphans = check_links_and_orphans(pages)
    fm_issues = check_frontmatter(pages)
    stale = check_stale(pages, days=args.stale_days)

    all_issues = broken + orphans + fm_issues + stale
    summary = {
        "wiki_root": str(wiki_root),
        "pages_total": len(pages),
        "issues_total": len(all_issues),
        "by_severity": {
            "error": sum(1 for i in all_issues if i["severity"] == "error"),
            "warning": sum(1 for i in all_issues if i["severity"] == "warning"),
            "suggestion": sum(1 for i in all_issues if i["severity"] == "suggestion"),
        },
        "issues": all_issues,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
