#!/usr/bin/env python3
"""code_index.py — indexer per `code-index.db`.

Workflow:
  1. Scan filesystem (con exclude glob)
  2. Per file: chunk via ast (Python) o regex line-window (altri lang)
  3. Embed in batch via provider
  4. Upsert in DB

Re-index incremental: usa `last_indexed_sha` in meta + `git diff` per identificare
file modificati. Full re-index se sha non noto o `--force`.

CLI:
  python3 code_index.py --target <dir> [--force] [--limit N]
"""

import argparse
import ast
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


# ============================================================
# Filesystem scanning
# ============================================================

# Extension → lang mapping
LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "csharp",
    ".sh": "bash", ".bash": "bash",
    ".sql": "sql",
    ".md": "markdown",  # docs come "code-like" search
}

EXCLUDE_DIR_NAMES = {
    "node_modules", "vendor", "dist", "build", "out", "target", ".git",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", ".env", ".tox",
    ".next", ".nuxt", ".svelte-kit", ".cache",
    "coverage", ".coverage", "htmlcov",
    ".anjawiki",  # NON indicizzare il wiki stesso
}


def iter_source_files(root: Path) -> Iterable[Path]:
    """Yield path di file source rilevanti sotto root, escludendo dir noise."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Mutate dirnames per skippare
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES and not d.startswith(".")]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext in LANG_BY_EXT:
                yield Path(dirpath) / fname


# ============================================================
# Chunking
# ============================================================

def _chunk_python(text: str, max_lines: int = 80) -> list[dict]:
    """Chunk Python via ast: 1 chunk per top-level function/class.
    Top-level module code raccolto come "module" chunk se < max_lines."""
    chunks = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _chunk_by_lines(text, max_lines=max_lines)

    lines = text.split("\n")
    covered_lines: set = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start
            covered_lines.update(range(start, end + 1))
            content = "\n".join(lines[start - 1:end])
            chunks.append({
                "func_name": node.name,
                "line_start": start,
                "line_end": end,
                "content": content,
            })

    # Top-level non coperto (imports, costanti, etc.)
    uncovered = [(i, lines[i - 1]) for i in range(1, len(lines) + 1) if i not in covered_lines]
    if uncovered and len(uncovered) <= max_lines:
        first_line = uncovered[0][0]
        last_line = uncovered[-1][0]
        content = "\n".join(l for _, l in uncovered).strip()
        if content:
            chunks.append({
                "func_name": "<module>",
                "line_start": first_line,
                "line_end": last_line,
                "content": content,
            })

    return chunks


_FUNC_REGEX_BY_LANG = {
    "typescript": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|const|let|var)\s+(\w+)\s*(?:\(|=)", re.M),
    "javascript": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|const|let|var)\s+(\w+)\s*(?:\(|=)", re.M),
    "go": re.compile(r"^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", re.M),
    "rust": re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]", re.M),
    "java": re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?\w+\s+(\w+)\s*\(", re.M),
    "ruby": re.compile(r"^\s*def\s+(\w+)", re.M),
    "php": re.compile(r"^\s*(?:public|private|protected)?\s*function\s+(\w+)\s*\(", re.M),
    "c": re.compile(r"^\s*\w[\w\s\*]*\s+(\w+)\s*\([^)]*\)\s*\{", re.M),
    "cpp": re.compile(r"^\s*\w[\w\s\*:<>]*\s+(\w+)\s*\([^)]*\)\s*\{", re.M),
    "bash": re.compile(r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*\{", re.M),
}


def _chunk_by_func_regex(text: str, lang: str, max_lines: int = 80) -> list[dict]:
    """Chunking euristico per linguaggi via regex def-funzione.

    Approccio: trova def-lines, chunk va da una def all'inizio della successiva.
    """
    rx = _FUNC_REGEX_BY_LANG.get(lang)
    if not rx:
        return _chunk_by_lines(text, max_lines=max_lines)

    lines = text.split("\n")
    matches = []
    for m in rx.finditer(text):
        line_no = text[:m.start()].count("\n") + 1
        matches.append((line_no, m.group(1)))

    if not matches:
        return _chunk_by_lines(text, max_lines=max_lines)

    chunks = []
    for i, (start, name) in enumerate(matches):
        end = matches[i + 1][0] - 1 if i + 1 < len(matches) else len(lines)
        if end - start > max_lines * 2:
            end = start + max_lines  # cap excessive
        content = "\n".join(lines[start - 1:end])
        chunks.append({
            "func_name": name,
            "line_start": start,
            "line_end": end,
            "content": content,
        })
    return chunks


def _chunk_by_lines(text: str, max_lines: int = 80, overlap: int = 10) -> list[dict]:
    """Fallback: sliding window per file non parsabili."""
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return [{
            "func_name": None,
            "line_start": 1,
            "line_end": len(lines),
            "content": text,
        }]
    chunks = []
    step = max_lines - overlap
    for start in range(0, len(lines), step):
        end = min(start + max_lines, len(lines))
        content = "\n".join(lines[start:end])
        if content.strip():
            chunks.append({
                "func_name": None,
                "line_start": start + 1,
                "line_end": end,
                "content": content,
            })
        if end >= len(lines):
            break
    return chunks


def chunk_file(path: Path) -> list[dict]:
    """Restituisce list di chunk per un file. Skip se vuoto o troppo grande."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    if not text.strip() or len(text) > 500_000:  # skip > 500KB
        return []

    ext = path.suffix.lower()
    lang = LANG_BY_EXT.get(ext, "text")

    if lang == "python":
        return _chunk_python(text)
    if lang in _FUNC_REGEX_BY_LANG:
        return _chunk_by_func_regex(text, lang)
    return _chunk_by_lines(text)


# ============================================================
# Git integration
# ============================================================

def get_current_git_sha(root: Path) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        )
        return out.decode().strip()
    except Exception:
        return None


def get_changed_files_since(root: Path, since_sha: str) -> tuple[list[Path], list[Path]]:
    """Restituisce (modified_or_added, deleted) relative a root."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "diff", "--name-status", since_sha, "HEAD"],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode()
    except Exception:
        return [], []
    modified = []
    deleted = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[1]
        if status == "D":
            deleted.append(Path(path))
        else:
            modified.append(Path(path))
    return modified, deleted


# ============================================================
# Main indexer
# ============================================================

def _sha_short(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def index(
    target: Path,
    force: bool = False,
    limit: Optional[int] = None,
    verbose: bool = False,
) -> dict:
    """Build/refresh index per `target` (root del progetto).

    Args:
      target: directory radice del progetto (contiene `.anjawiki/`)
      force: True → full re-index (drop & rebuild)
      limit: max file da processare (debug)
      verbose: log su stderr
    """
    target = target.resolve()
    anjawiki = target / ".anjawiki"
    if not anjawiki.is_dir():
        return {"error": f"`.anjawiki/` not found under {target}"}

    # Lazy imports
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import embed_providers
        import code_db
    except ImportError as e:
        return {"error": f"missing module: {e}"}

    provider = embed_providers.get_provider()
    if provider is None:
        return {
            "error": "no embed provider available. Set ANJA_EMBED_PROVIDER + API key. "
                     "See `scripts/embed_providers.py`."
        }

    if verbose:
        print(f"[index] provider={provider.name} model={provider.model} dim={provider.dim}", file=sys.stderr)

    try:
        db = code_db.open_db(anjawiki, dim=provider.dim)
    except RuntimeError as e:
        return {"error": str(e)}

    current_sha = get_current_git_sha(target)
    last_sha = code_db.get_meta(db, "last_indexed_sha")

    # Determine incremental vs full
    incremental = (not force) and (last_sha is not None) and (current_sha is not None)
    if incremental:
        modified, deleted = get_changed_files_since(target, last_sha)
        # Filter to source files only
        modified_files = [target / p for p in modified if p.suffix.lower() in LANG_BY_EXT and (target / p).is_file()]
        deleted_files = [target / p for p in deleted if p.suffix.lower() in LANG_BY_EXT]
        files_to_index = modified_files
        if verbose:
            print(f"[index] incremental from {last_sha[:8]} → {current_sha[:8]}: "
                  f"{len(modified_files)} modified, {len(deleted_files)} deleted", file=sys.stderr)
        # Remove deleted files
        for f in deleted_files:
            rel = str(f.relative_to(target))
            code_db.delete_chunks_for_file(db, rel)
    else:
        if verbose:
            mode = "force full re-index" if force else "first index (no last_sha)"
            print(f"[index] {mode}", file=sys.stderr)
        # Drop chunk tables
        if force:
            db.execute("DELETE FROM chunk_vec")
            db.execute("DELETE FROM chunks")
            db.commit()
        files_to_index = list(iter_source_files(target))

    if limit:
        files_to_index = files_to_index[:limit]

    # Collect chunks
    all_chunks: list[tuple[Path, dict]] = []
    for f in files_to_index:
        for chunk in chunk_file(f):
            all_chunks.append((f, chunk))

    if not all_chunks:
        # Aggiorna comunque meta + commit
        if current_sha:
            code_db.set_meta(db, "last_indexed_sha", current_sha)
        code_db.set_meta(db, "embed_provider", provider.name)
        code_db.set_meta(db, "embed_model", provider.model)
        return {
            "indexed_files": 0,
            "indexed_chunks": 0,
            "provider": provider.name,
            "model": provider.model,
            "git_sha": current_sha,
            "note": "nothing to index" if incremental else "no source files found",
        }

    if verbose:
        print(f"[index] embedding {len(all_chunks)} chunks via {provider.name}...", file=sys.stderr)

    # Batch embed (default 64 per call)
    BATCH = 64
    indexed_count = 0
    indexed_files: set = set()
    for i in range(0, len(all_chunks), BATCH):
        batch = all_chunks[i:i + BATCH]
        texts = [c["content"] for _, c in batch]
        try:
            vecs = provider.embed(texts)
        except Exception as e:
            if verbose:
                print(f"[index] embed batch failed: {e}", file=sys.stderr)
            continue
        # First-batch: re-check dim and re-init if needed (idempotente)
        if i == 0 and len(vecs) > 0 and len(vecs[0]) != provider.dim:
            return {"error": f"provider returned dim={len(vecs[0])} but expected {provider.dim}"}

        # Per ogni file in batch: clear vecchi chunks (per quel file) la prima volta che lo incontri
        files_seen_this_batch: set = set()
        for (fpath, chunk), vec in zip(batch, vecs):
            rel = str(fpath.relative_to(target))
            if rel not in indexed_files and rel not in files_seen_this_batch:
                # Solo se NON incrementale (perché in full mode già abbiamo droppato tutto)
                # In incremental mode, devo cancellare i vecchi chunks per file modified
                if incremental:
                    code_db.delete_chunks_for_file(db, rel)
                files_seen_this_batch.add(rel)

            content_sha = _sha_short(chunk["content"])
            last_modified = datetime.fromtimestamp(fpath.stat().st_mtime).isoformat(timespec="seconds")
            lang = LANG_BY_EXT.get(fpath.suffix.lower(), "text")
            code_db.upsert_chunk(
                db,
                file_path=rel,
                func_name=chunk.get("func_name"),
                line_start=chunk["line_start"],
                line_end=chunk["line_end"],
                content=chunk["content"],
                lang=lang,
                last_modified=last_modified,
                content_sha=content_sha,
                embedding=vec,
            )
            indexed_count += 1
            indexed_files.add(rel)
        db.commit()
        if verbose:
            print(f"[index]  ... {i + len(batch)}/{len(all_chunks)} chunks", file=sys.stderr)

    # Update meta
    code_db.set_meta(db, "embed_provider", provider.name)
    code_db.set_meta(db, "embed_model", provider.model)
    if current_sha:
        code_db.set_meta(db, "last_indexed_sha", current_sha)
    code_db.set_meta(db, "last_indexed_at", datetime.now().astimezone().isoformat(timespec="seconds"))

    return {
        "indexed_files": len(indexed_files),
        "indexed_chunks": indexed_count,
        "provider": provider.name,
        "model": provider.model,
        "git_sha": current_sha,
        "incremental": incremental,
    }


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=".", help="Root del progetto (deve avere .anjawiki/)")
    parser.add_argument("--force", action="store_true", help="Full re-index (drop & rebuild)")
    parser.add_argument("--limit", type=int, help="Max file da processare (debug)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    result = index(
        target=Path(args.target).resolve(),
        force=args.force,
        limit=args.limit,
        verbose=not args.quiet,
    )
    import json as _json
    print(_json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
