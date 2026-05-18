#!/usr/bin/env python3
"""code_search.py — ricerca nel codebase ospitante via 3 livelli.

Level 0: ripgrep + smart ranking (count + filename boost + recency)
Level 1: ripgrep top-50 + LLM haiku rerank semantico
Level 2: vector search via sqlite-vec + embed provider

Auto-detect default level basato su:
  - has_vector_index → level 2
  - codebase < 5k LOC → level 0
  - altrimenti → level 1

Override esplicito via smart_level=N.

Stdlib + subprocess `rg` + lazy import code_db/embed_providers per level 2.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# ============================================================
# Filesystem detection
# ============================================================

def _project_root(start: Path = None) -> Optional[Path]:
    """Risali dalla cwd cercando `.anjawiki/`.

    Fallback su env ANJA_ROOT (settato dal server MCP) se cwd non valida.
    """
    candidates = []
    if start:
        candidates.append(start.resolve())
    candidates.append(Path.cwd().resolve())
    env_root = os.environ.get("ANJA_ROOT")
    if env_root:
        candidates.append(Path(env_root).resolve())

    for cur in candidates:
        for parent in [cur] + list(cur.parents):
            if (parent / ".anjawiki" / "meta.yaml").is_file():
                return parent
    return None


def _has_vector_index(project_root: Path) -> bool:
    return (project_root / ".anjawiki" / "code-index.db").exists()


def _quick_loc_count(project_root: Path) -> int:
    """Conta LOC totali per linguaggi rilevanti. Cap timeout 5s."""
    extensions = ["py", "ts", "tsx", "js", "jsx", "go", "rs", "java", "kt", "rb", "php", "c", "cpp"]
    patterns = " -o ".join(f"-name '*.{e}'" for e in extensions)
    cmd = (
        f"find {project_root} -type f \\( {patterns} \\) "
        "-not -path '*/node_modules/*' -not -path '*/.git/*' "
        "-not -path '*/__pycache__/*' -not -path '*/dist/*' "
        "| xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}'"
    )
    try:
        r = subprocess.run(["sh", "-c", cmd], capture_output=True, timeout=5, text=True)
        return int((r.stdout.strip() or "0").split()[0])
    except Exception:
        return 0


def infer_default_level(project_root: Path) -> int:
    """Auto-detect smart_level basato su contesto."""
    if _has_vector_index(project_root):
        return 2
    loc = _quick_loc_count(project_root)
    if loc < 5000:
        return 0
    return 1


# ============================================================
# Level 0: ripgrep + smart ranking
# ============================================================

EXCLUDE_GLOBS = [
    "node_modules/**", "vendor/**", "dist/**", "build/**", "out/**",
    ".git/**", "__pycache__/**", "*.min.js", "*.min.css",
    ".anjawiki/code-index.db",
]


def _ripgrep_search(query: str, root: Path, limit: int = 50, lang: Optional[str] = None) -> list[dict]:
    """ripgrep --json output → list di match strutturati."""
    cmd = ["rg", "--json", "--smart-case", "--max-count=20", "-C", "1", query, str(root)]
    for glob in EXCLUDE_GLOBS:
        cmd.extend(["--glob", f"!{glob}"])
    if lang:
        cmd.extend(["--type", lang])

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15, text=True)
    except FileNotFoundError:
        return []
    except subprocess.TimeoutExpired:
        return []

    matches = []
    for line in result.stdout.splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") != "match":
            continue
        data = ev["data"]
        path = data["path"]["text"]
        line_no = data["line_number"]
        text = data["lines"]["text"].rstrip("\n")
        matches.append({"path": path, "line": line_no, "text": text})
        if len(matches) >= limit:
            break
    return matches


def _git_recency(path: str, root: Path) -> float:
    """Boost basato su recency del file via git log. Restituisce [0, 1]."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "log", "-1", "--format=%ct", "--", path],
            stderr=subprocess.DEVNULL, timeout=3,
        ).decode().strip()
        if not out:
            return 0.0
        commit_ts = int(out)
        days_ago = (datetime.now().timestamp() - commit_ts) / 86400
        # Decay esponenziale: 1.0 oggi, 0.5 a 30 giorni, 0.1 a 100 giorni
        return max(0.0, 1.0 / (1.0 + days_ago / 30))
    except Exception:
        return 0.0


def _smart_rank(matches: list[dict], query: str, root: Path) -> list[dict]:
    """Ranking: count_in_file * 1.0 + filename_match * 2.0 + recency * 1.5."""
    q_lower = query.lower()
    by_file: dict[str, dict] = {}

    for m in matches:
        path = m["path"]
        if path not in by_file:
            by_file[path] = {"path": path, "matches": [], "score": 0.0}
        by_file[path]["matches"].append(m)

    for path, entry in by_file.items():
        n_matches = len(entry["matches"])
        score = float(n_matches)

        filename = Path(path).name.lower()
        if q_lower in filename:
            score += 2.0

        # Boost se query match in def/class lines
        for m in entry["matches"]:
            text = m["text"].lower()
            if any(kw in text for kw in (f"def {q_lower}", f"class {q_lower}", f"function {q_lower}", f"func {q_lower}")):
                score += 3.0
                break

        # Recency
        score += _git_recency(path, root) * 1.5

        entry["score"] = score

    return sorted(by_file.values(), key=lambda e: -e["score"])


def search_level_0(query: str, root: Path, limit: int = 20, lang: Optional[str] = None) -> dict:
    """ripgrep + smart ranking. Restituisce top file con match preview."""
    raw_matches = _ripgrep_search(query, root, limit=200, lang=lang)
    ranked = _smart_rank(raw_matches, query, root)

    results = []
    for entry in ranked[:limit]:
        rel_path = str(Path(entry["path"]).relative_to(root)) if Path(entry["path"]).is_absolute() else entry["path"]
        results.append({
            "path": rel_path,
            "score": round(entry["score"], 2),
            "match_count": len(entry["matches"]),
            "preview": entry["matches"][:3],  # top 3 match per file
        })
    return {"level": 0, "method": "ripgrep_smart_rank", "results": results, "count": len(results)}


# ============================================================
# Level 1: ripgrep + LLM haiku rerank
# ============================================================

def search_level_1(query: str, root: Path, limit: int = 10, lang: Optional[str] = None) -> dict:
    """Level 0 top 50 → spawn claude haiku per rerank semantico."""
    level0 = search_level_0(query, root, limit=50, lang=lang)
    if not level0["results"]:
        return {"level": 1, "method": "ripgrep_llm_rerank", "results": [], "count": 0, "note": "no ripgrep matches"}

    # Build prompt
    candidates_text = []
    for i, r in enumerate(level0["results"]):
        previews = "\n".join(f"  L{m['line']}: {m['text'][:120]}" for m in r["preview"])
        candidates_text.append(f"[{i}] {r['path']}\n{previews}")

    prompt = (
        f"Query: {query}\n\n"
        f"Sono stati trovati {len(level0['results'])} file candidati via ricerca keyword. "
        f"Per ogni file vedi: path + 3 righe più rilevanti (con numero L). "
        f"Devi RANKARE i top {limit} per relevance semantica alla query (non solo keyword).\n\n"
        f"OUTPUT FORMAT: solo lista di indici [0..N-1] ordinati per relevance descending, "
        f"max {limit} indici, separati da virgola. NIENTE preambolo, NIENTE spiegazioni.\n\n"
        f"Esempio output: 3,7,1,12,0,5,9,2,8,15\n\n"
        f"--- CANDIDATI ---\n" + "\n\n".join(candidates_text)
    )

    claude_bin = os.environ.get("ANJA_CLAUDE_BIN", "claude")
    model = os.environ.get("ANJA_SEARCH_RERANK_MODEL", "haiku")
    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--model", model],
            capture_output=True, timeout=90, text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        # Fallback level 0 se claude non disponibile
        return {
            "level": 0,
            "method": "ripgrep_smart_rank",
            "results": level0["results"][:limit],
            "count": min(len(level0["results"]), limit),
            "_fallback_reason": f"LLM rerank failed: {type(e).__name__}",
        }

    if result.returncode != 0:
        return {
            "level": 0,
            "method": "ripgrep_smart_rank",
            "results": level0["results"][:limit],
            "count": min(len(level0["results"]), limit),
            "_fallback_reason": f"claude rc={result.returncode}",
        }

    # Parse indices
    out = result.stdout.strip()
    indices = []
    for tok in re.split(r"[,\s]+", out):
        try:
            idx = int(tok)
            if 0 <= idx < len(level0["results"]):
                indices.append(idx)
        except ValueError:
            continue

    if not indices:
        return {
            "level": 0,
            "method": "ripgrep_smart_rank",
            "results": level0["results"][:limit],
            "count": min(len(level0["results"]), limit),
            "_fallback_reason": "LLM returned no parsable indices",
        }

    reranked = [level0["results"][i] for i in indices[:limit]]
    return {
        "level": 1,
        "method": "ripgrep_llm_rerank",
        "model": model,
        "results": reranked,
        "count": len(reranked),
    }


# ============================================================
# Level 2: vector search
# ============================================================

def search_level_2(query: str, project_root: Path, limit: int = 10, lang: Optional[str] = None) -> dict:
    """Vector search via sqlite-vec. Embed query con stesso provider usato per index."""
    # Lazy imports
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import code_db
        import embed_providers
    except ImportError as e:
        return {"level": 1, "results": [], "_fallback_reason": f"missing module: {e}"}

    anjawiki = project_root / ".anjawiki"
    db_path = anjawiki / "code-index.db"
    if not db_path.exists():
        # Graceful fallback level 1
        l1 = search_level_1(query, project_root, limit=limit, lang=lang)
        l1["_fallback_reason"] = "vector index not built. Run `code.reindex` or `/anja-index-code`."
        return l1

    provider = embed_providers.get_provider()
    if provider is None:
        l1 = search_level_1(query, project_root, limit=limit, lang=lang)
        l1["_fallback_reason"] = "no embed provider available"
        return l1

    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        l1 = search_level_1(query, project_root, limit=limit, lang=lang)
        l1["_fallback_reason"] = f"db open failed: {e}"
        return l1

    try:
        query_vecs = provider.embed([query])
        if not query_vecs:
            return {"level": 2, "results": [], "_fallback_reason": "empty query embedding"}
        query_vec = query_vecs[0]
    except Exception as e:
        l1 = search_level_1(query, project_root, limit=limit, lang=lang)
        l1["_fallback_reason"] = f"query embed failed: {e}"
        return l1

    hits = code_db.vector_search(db, query_vec, limit=limit, lang_filter=lang)

    results = []
    for h in hits:
        results.append({
            "path": h["file_path"],
            "func_name": h["func_name"],
            "line_start": h["line_start"],
            "line_end": h["line_end"],
            "lang": h["lang"],
            "distance": round(h["distance"], 4),
            "preview": h["content"][:300],
        })

    return {
        "level": 2,
        "method": "vector_search",
        "provider": provider.name,
        "model": provider.model,
        "results": results,
        "count": len(results),
    }


# ============================================================
# Entry point
# ============================================================

def code_search(query: str, smart_level: Optional[int] = None, limit: int = 10, lang: Optional[str] = None) -> dict:
    """Entry point: auto-detect default level + dispatch."""
    if not query.strip():
        return {"error": "query required"}

    root = _project_root()
    if root is None:
        return {"error": "not in an anja project (no `.anjawiki/meta.yaml` found in parent dirs)"}

    if smart_level is None:
        smart_level = infer_default_level(root)
    smart_level = max(0, min(2, int(smart_level)))

    if smart_level == 0:
        return search_level_0(query, root, limit=limit, lang=lang)
    if smart_level == 1:
        return search_level_1(query, root, limit=limit, lang=lang)
    return search_level_2(query, root, limit=limit, lang=lang)
