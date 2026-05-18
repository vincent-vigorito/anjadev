#!/usr/bin/env python3
"""
context_loader.py — costruisce tier HOT + WARM da iniettare nel prompt LLM.

HOT (always-on, ~1.5k tokens target):
    - AGENTS.md
    - SOUL.md
    - TOOLS.md
    - last N log entries (default 3)

WARM (relevance-driven, ~3k tokens target):
    - last N session summaries (default 5)
    - wiki pages by keyword match al prompt utente (max 3)

COLD: non iniettato qui — accessibile via tool memory.recall on-demand.

Usage as Python module:
    from context_loader import build_session_context
    ctx = build_session_context(scope_root, user_prompt=None)

Usage as CLI (debug):
    python3 context_loader.py --root <project-or-hub-root> [--prompt "..."] [--budget-hot 1500] [--budget-warm 3000]
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG = {
    "hot_budget_tokens": 1500,
    "warm_budget_tokens": 3000,
    "log_entries_count": 3,
    "session_summaries_count": 5,
    "wiki_match_max_pages": 3,
}


# Approssimazione: ~4 char = 1 token (English) / ~3 char = 1 token (Italian).
# Usiamo 3.5 come compromesso conservativo.
CHARS_PER_TOKEN = 3.5


def _est_tokens(s: str) -> int:
    return int(len(s) / CHARS_PER_TOKEN)


def _truncate_to_tokens(s: str, max_tokens: int) -> str:
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "\n...[truncated]"


def _read_if_exists(p: Path) -> str:
    if not p.is_file() and not p.is_symlink():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from markdown."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    return text[end + 4:].lstrip("\n")


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def load_config(root: Path) -> dict:
    """Read memory config from <root>/.anjawiki/config.json or <root>/config.json (hub).
    Fallback to DEFAULT_CONFIG.
    """
    candidates = [
        root / ".anjawiki" / "config.json",
        root / "config.json",
    ]
    cfg = dict(DEFAULT_CONFIG)
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                memcfg = data.get("memory", {})
                if isinstance(memcfg, dict):
                    cfg.update({k: memcfg[k] for k in DEFAULT_CONFIG if k in memcfg})
                break
            except Exception:
                continue
    return cfg


# =================================================================
# HOT tier
# =================================================================

def load_triade(root: Path) -> str:
    """Load AGENTS.md + SOUL.md + TOOLS.md, strip frontmatter + comments, concat."""
    parts = []
    for fname in ("AGENTS.md", "SOUL.md", "TOOLS.md"):
        text = _read_if_exists(root / fname)
        if not text:
            continue
        clean = _strip_html_comments(_strip_frontmatter(text))
        parts.append(f"### {fname}\n\n{clean}")
    return "\n\n---\n\n".join(parts)


def load_recent_log_entries(root: Path, n: int = 3) -> str:
    """Read last N entries from wiki/log.md (project) or cross/log.md (hub).

    Log entry format: `## [YYYY-MM-DD] type | description`
    """
    log_candidates = [
        root / ".anjawiki" / "wiki" / "log.md",
        root / "cross" / "log.md",
    ]
    log_path = next((p for p in log_candidates if p.is_file()), None)
    if not log_path:
        return ""

    text = log_path.read_text(encoding="utf-8", errors="replace")
    text = _strip_frontmatter(text)
    # find all "## [DATE] ..." entries
    entries = re.findall(r"^(##\s+\[\d{4}-\d{2}-\d{2}\][^\n]*(?:\n(?!##\s+\[)[^\n]*)*)", text, re.M)
    if not entries:
        return ""
    last_n = entries[-n:][::-1]  # most recent first
    return "### Recent log entries\n\n" + "\n\n".join(last_n)


# =================================================================
# WARM tier
# =================================================================

def _wiki_root(root: Path) -> Optional[Path]:
    """Resolve wiki/ dir (project: .anjawiki/wiki, hub: wiki)."""
    for cand in (root / ".anjawiki" / "wiki", root / "wiki"):
        if cand.is_dir():
            return cand
    return None


def load_recent_sessions(root: Path, n: int = 5) -> str:
    """Read last N session summaries from wiki/sessions/. Supporta entrambi i layout."""
    wiki = _wiki_root(root)
    if not wiki:
        return ""
    sessions_dir = wiki / "sessions"
    if not sessions_dir.is_dir():
        return ""

    found = []
    # File-per-session in date dirs
    for date_dir in sorted(sessions_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("*.md"), reverse=True):
            found.append(f)
            if len(found) >= n:
                break
        if len(found) >= n:
            break

    # Legacy file-per-day
    if len(found) < n:
        for f in sorted(sessions_dir.glob("*.md"), reverse=True):
            if f.name == "index.md":
                continue
            found.append(f)
            if len(found) >= n:
                break

    if not found:
        return ""

    parts = []
    for f in found[:n]:
        text = _strip_html_comments(_strip_frontmatter(f.read_text(encoding="utf-8", errors="replace")))
        # Try to extract Summary section, else use first paragraph
        m = re.search(r"^## Summary\s*\n(.+?)(?=\n## |\Z)", text, re.M | re.DOTALL)
        body = m.group(1).strip() if m else text.strip()[:400]
        parts.append(f"#### {f.stem}\n{body}")
    return "### Recent sessions\n\n" + "\n\n".join(parts)


def load_wiki_pages_by_keyword(root: Path, prompt: str, max_pages: int = 3) -> str:
    """Find wiki pages matching keywords in prompt (grep+rank).

    Riusa logica del MCP server tool memory.recall ma in-process.
    """
    if not prompt:
        return ""
    wiki = _wiki_root(root)
    if not wiki:
        return ""

    keywords = [w.lower() for w in re.findall(r"\b\w{3,}\b", prompt)][:10]
    if not keywords:
        return ""

    matches = []
    for f in wiki.rglob("*.md"):
        if "sessions/" in str(f.relative_to(wiki)) or f.name in ("index.md", "log.md"):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        text_lower = text.lower()
        score = sum(text_lower.count(kw) for kw in keywords)
        if score == 0:
            continue
        matches.append((score, f, text))

    if not matches:
        return ""

    matches.sort(key=lambda t: t[0], reverse=True)
    parts = []
    for score, f, text in matches[:max_pages]:
        clean = _strip_html_comments(_strip_frontmatter(text))[:1200]
        parts.append(f"#### [{f.stem}] (score={score})\n{clean}")
    return "### Relevant wiki pages\n\n" + "\n\n".join(parts)


# =================================================================
# Main entry
# =================================================================

def build_session_context(
    root: Path,
    user_prompt: Optional[str] = None,
    config_override: Optional[dict] = None,
) -> dict:
    """Costruisce HOT + WARM context per una sessione (chat o routine).

    Ritorna dict con:
      hot:  str             — sempre presente (triade + log)
      warm: str             — opzionale (sessions + wiki match)
      tokens_estimated: int
      tokens_budget: int
      truncated: bool
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_config(root))
    if config_override:
        cfg.update(config_override)

    # HOT
    hot_parts = []
    triade = load_triade(root)
    if triade:
        hot_parts.append(triade)
    logs = load_recent_log_entries(root, n=cfg["log_entries_count"])
    if logs:
        hot_parts.append(logs)
    hot = "\n\n---\n\n".join(hot_parts)
    hot_truncated = False
    if _est_tokens(hot) > cfg["hot_budget_tokens"]:
        # Cascade per design doc §5.G: HOT non si taglia, errore esplicito
        # Per ora soft warning, e tronchiamo log (NON triade)
        hot = "\n\n---\n\n".join(hot_parts[:1])  # solo triade
        hot = _truncate_to_tokens(hot, cfg["hot_budget_tokens"])
        hot_truncated = True

    # WARM
    warm_parts = []
    sess = load_recent_sessions(root, n=cfg["session_summaries_count"])
    if sess:
        warm_parts.append(sess)
    if user_prompt:
        wiki_match = load_wiki_pages_by_keyword(root, user_prompt, max_pages=cfg["wiki_match_max_pages"])
        if wiki_match:
            warm_parts.append(wiki_match)
    warm = "\n\n---\n\n".join(warm_parts)
    warm_truncated = False
    if _est_tokens(warm) > cfg["warm_budget_tokens"]:
        warm = _truncate_to_tokens(warm, cfg["warm_budget_tokens"])
        warm_truncated = True

    return {
        "hot": hot,
        "warm": warm,
        "tokens_estimated": _est_tokens(hot) + _est_tokens(warm),
        "tokens_budget": cfg["hot_budget_tokens"] + cfg["warm_budget_tokens"],
        "hot_truncated": hot_truncated,
        "warm_truncated": warm_truncated,
        "config": cfg,
    }


def format_for_prompt(ctx: dict) -> str:
    """Formatta il context come blocco markdown per system prompt."""
    parts = []
    if ctx.get("hot"):
        parts.append("# Context (always-on)\n\n" + ctx["hot"])
    if ctx.get("warm"):
        parts.append("# Recent context (relevance)\n\n" + ctx["warm"])
    if not parts:
        return ""
    return "\n\n===\n\n".join(parts)


# =================================================================
# CLI debug
# =================================================================

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, help="project or hub root")
    p.add_argument("--prompt", default=None, help="user prompt (per WARM keyword match)")
    p.add_argument("--budget-hot", type=int, default=None)
    p.add_argument("--budget-warm", type=int, default=None)
    p.add_argument("--format", choices=["json", "prompt"], default="prompt")
    args = p.parse_args()

    root = Path(args.root).expanduser().resolve()
    overrides = {}
    if args.budget_hot is not None:
        overrides["hot_budget_tokens"] = args.budget_hot
    if args.budget_warm is not None:
        overrides["warm_budget_tokens"] = args.budget_warm

    ctx = build_session_context(root, user_prompt=args.prompt, config_override=overrides)
    if args.format == "json":
        print(json.dumps({k: v for k, v in ctx.items() if k not in ("hot", "warm")}, indent=2))
        print("\n--- HOT ---\n")
        print(ctx["hot"])
        print("\n--- WARM ---\n")
        print(ctx["warm"])
    else:
        print(format_for_prompt(ctx))
        print(f"\n[stats] tokens~{ctx['tokens_estimated']}/budget={ctx['tokens_budget']} "
              f"hot_trunc={ctx['hot_truncated']} warm_trunc={ctx['warm_truncated']}", file=sys.stderr)


if __name__ == "__main__":
    main()
