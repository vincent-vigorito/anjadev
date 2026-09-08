#!/usr/bin/env python3
"""code_index.py — indexer per `code-index.db`.

Workflow:
  1. Scan filesystem (con exclude glob)
  2. Per file: chunk via ast (Python) o regex line-window (altri lang)
  3. Embed in batch via provider
  4. Upsert in DB

Re-index incremental: confronta hash del filesystem con il manifesto pubblicato.
Staging e pubblicazione atomica condivisi con il wiki in index_pipeline.

CLI:
  python3 code_index.py --target <dir> [--force] [--limit N]
"""

import argparse
import ast
import hashlib
import re
import subprocess
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


def _scan_error(error):
    raise error


def iter_source_files(root: Path) -> Iterable[Path]:
    from index_policy import discover
    yield from discover(root, "code")[0]


# ============================================================
# Chunking
# ============================================================

def _chunk_python(text: str, max_lines: int = 80) -> list[dict]:
    """Intervalli AST e top-level completi, suddivisi in finestre con overlap."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _chunk_by_lines(text, max_lines=max_lines)
    lines = text.split("\n")
    chunks = []
    cursor = 1
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            end = node.end_lineno or start
            chunks.extend(_chunk_range(lines, cursor, start - 1, "<module>", max_lines))
            chunks.extend(_chunk_range(lines, start, end, node.name, max_lines))
            cursor = end + 1
    chunks.extend(_chunk_range(lines, cursor, len(lines), "<module>", max_lines))
    return chunks


def _chunk_range(lines, start, end, name, max_lines):
    if start > end:
        return []
    text = "\n".join(lines[start - 1:end])
    if not text.strip():
        return []
    chunks = _chunk_by_lines(text, max_lines=max_lines)
    for chunk in chunks:
        chunk["line_start"] += start - 1
        chunk["line_end"] += start - 1
        chunk["func_name"] = name
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

    chunks = _chunk_range(lines, 1, matches[0][0] - 1, "<module>", max_lines)
    for i, (start, name) in enumerate(matches):
        end = matches[i + 1][0] - 1 if i + 1 < len(matches) else len(lines)
        chunks.extend(_chunk_range(lines, start, end, name, max_lines))
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

    return chunk_text(text, path.suffix)


def chunk_text(text: str, suffix: str) -> list[dict]:
    if not text.strip() or len(text) > 500_000:
        return []
    # A terminating newline closes the last physical line; it does not add a line.
    text = text.removesuffix("\n")
    ext = suffix.lower()
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
    out = subprocess.check_output(
        ["git", "-C", str(root), "diff", "--name-status", "-z", since_sha],
        stderr=subprocess.PIPE, timeout=10,
    ).decode().split("\0")
    modified, deleted = [], []
    cursor = 0
    while cursor < len(out) and out[cursor]:
        status, path = out[cursor:cursor + 2]
        cursor += 2
        if status.startswith(("R", "C")):
            if status.startswith("R"):
                deleted.append(Path(path))
            modified.append(Path(out[cursor]))
            cursor += 1
        elif status == "D":
            deleted.append(Path(path))
        else:
            modified.append(Path(path))
    untracked = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"], timeout=10,
    ).decode().split("\0")
    modified.extend(Path(p) for p in untracked if p)
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
    dry_run: bool = False,
) -> dict:
    """Build/refresh index per `target` (root del progetto).

    Args:
      target: directory radice del progetto (contiene `.anjawiki/`)
      force: True → full re-index (drop & rebuild)
      limit: max file da processare (debug)
      verbose: log su stderr
    """
    from index_pipeline import refresh
    return refresh(target, kind="code", force=force, limit=limit, verbose=verbose, dry_run=dry_run)


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=".", help="Root del progetto (deve avere .anjawiki/)")
    parser.add_argument("--force", action="store_true", help="Full re-index (drop & rebuild)")
    parser.add_argument("--limit", type=int, help="Max file da processare (debug)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    result = index(
        target=Path(args.target).resolve(),
        force=args.force,
        limit=args.limit,
        verbose=not args.quiet,
        dry_run=args.dry_run,
    )
    import json as _json
    print(_json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
