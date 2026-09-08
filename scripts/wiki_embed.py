#!/usr/bin/env python3
"""wiki_embed.py — pipeline embedding incrementale per pagine wiki.

Scansiona `<root>/.anjawiki/wiki/**.md`, parse frontmatter, embedda body
(con campi salient del frontmatter prepended), upsert in code-index DB con
kind='wiki'.

Dirty detection via content_sha: rieseguibile, no work se nulla cambia.
Cleanup automatico delle pagine cancellate dal filesystem (orphan deletion).

Usa stesso `embed_providers` + `code_db` del code-index → spazio embedding
condiviso → k-NN cross-kind (wiki ↔ code) gratis.

Standalone CLI:
    python3 wiki_embed.py <project-root> [--force] [--no-sessions]

Modulo (per import):
    from wiki_embed import embed_wiki
    result = embed_wiki(Path("/path/to/project"), force=False)
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Lazy: imports may fail if deps missing
def _get_modules():
    code_db = _load("code_db")
    skill_parser = _load("skill_parser")
    embed_providers = _load("embed_providers")
    return code_db, skill_parser, embed_providers


# ============================================================
# Discovery + parsing
# ============================================================

# Skip directories (sessions opt-in, log/index always skipped)
_DEFAULT_SKIP_DIRS = {"raw"}  # raw/ è already-excluded a livello di tree
_ALWAYS_SKIP_FILES = {"log.md", "index.md", "roadmap.md"}


def _iter_wiki_files(wiki_root: Path, include_sessions: bool) -> list[Path]:
    """Trova tutti i `.md` da embeddare. Esclude log/index/roadmap (auto-aggiornati)
    e opzionalmente sessions/."""
    if not wiki_root.is_dir():
        return []
    out = []
    if not wiki_root.resolve().is_relative_to(wiki_root.parent.parent.resolve()):
        raise ValueError("wiki root escapes the project")
    for md in sorted(wiki_root.rglob("*.md")):
        relative = md.relative_to(wiki_root)
        if (not md.is_file() or md.is_symlink() or not md.resolve().is_relative_to(wiki_root.resolve())
                or any(p.startswith(".") for p in relative.parts)):
            continue
        if md.name in _ALWAYS_SKIP_FILES:
            continue
        if any(part in _DEFAULT_SKIP_DIRS for part in relative.parts):
            continue
        if not include_sessions and "sessions" in relative.parts:
            continue
        out.append(md)
    return out


def _compute_input_text(meta: dict, body: str, max_chars: int = 8000) -> str:
    """Combina frontmatter salient + body per embedding.

    Frontmatter campi rilevanti vengono concatenati come prefisso ad alto segnale.
    Body troncato a `max_chars` per evitare di esplodere il token budget del provider.
    """
    salient = []
    title = (meta.get("title") or "").strip()
    if title:
        salient.append(f"# {title}")
    page_type = (meta.get("type") or "").strip()
    if page_type:
        salient.append(f"Type: {page_type}")
    tags = meta.get("tags") or []
    if isinstance(tags, list) and tags:
        salient.append(f"Tags: {', '.join(str(t) for t in tags)}")
    cat = (meta.get("category") or "").strip()
    if cat:
        salient.append(f"Category: {cat}")
    prefix = "\n".join(salient)
    if prefix:
        prefix += "\n\n"

    full = prefix + body
    if len(full) > max_chars:
        full = full[:max_chars]
    return full


def _compute_hash(input_text: str) -> str:
    return hashlib.sha1(input_text.encode("utf-8")).hexdigest()


def _slug_from_path(md_path: Path, wiki_root: Path) -> str:
    """Slug = path relativo rispetto a wiki_root, senza estensione, con `/` → `:`.

    Esempi:
      wiki/entities/auth-service.md → entities:auth-service
      wiki/sessions/2026-05-19.md   → sessions:2026-05-19
      wiki/overview.md              → overview
    """
    rel = md_path.relative_to(wiki_root).with_suffix("")
    return str(rel).replace("/", ":")


# ============================================================
# Main pipeline
# ============================================================

def embed_wiki(
    root: Path,
    force: bool = False,
    include_sessions: bool = False,
    batch_size: int = 16,
    verbose: bool = False,
) -> dict:
    """Incremental embed di tutte le pagine wiki sotto `<root>/.anjawiki/wiki/`.

    Args:
      root: project root (parent di `.anjawiki/`)
      force: True → re-embed tutto (anche se hash uguale)
      include_sessions: include `wiki/sessions/*.md`
      batch_size: pagine per batch al provider

    Returns: {
      scanned: N,
      embedded: N,           # pagine effettivamente embedded
      skipped_unchanged: N,  # dirty check ha matchato
      deleted_orphans: N,    # pagine in DB ma non in FS
      errors: [...],
      provider: "openrouter" | ...,
      ms: tempo totale
    }
    """
    from index_pipeline import refresh
    return refresh(root, kind="wiki", force=force, include_sessions=include_sessions,
                   batch_size=batch_size, verbose=verbose)


def embed_single_page(root: Path, md_path: Path) -> dict:
    """Embed di una singola pagina (per trigger inline / hook PostToolUse).

    Più rapido di scan completo: 1 file → 1 embedding call → 1 upsert.
    Restituisce risultato compatto.
    """
    from index_pipeline import refresh
    root = root.resolve()
    md_path = md_path.absolute()
    result = refresh(root, kind="wiki", single=md_path)
    if "error" not in result:
        result["path"] = str(md_path)
        result["action"] = "deleted" if not md_path.is_file() else ("embedded" if result["embedded"] else "skipped_unchanged")
    return result


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Embed wiki pages into code-index DB")
    ap.add_argument("root", help="project root (parent of .anjawiki/)")
    ap.add_argument("--force", action="store_true", help="re-embed all pages, ignore dirty check")
    ap.add_argument("--no-sessions", action="store_true", help="(default dal v0.22) skip wiki/sessions/")
    ap.add_argument("--with-sessions", action="store_true", help="includi wiki/sessions/ nell'indice (opt-in)")
    ap.add_argument("--single", help="embed only this single file (absolute path)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()

    # Auto-load .secrets.env BEFORE embed_providers viene importato
    try:
        import secrets_loader
        secrets_loader.load_secrets(root)
    except ImportError:
        pass

    if args.single:
        md_path = Path(args.single).expanduser().absolute()
        result = embed_single_page(root, md_path)
    else:
        result = embed_wiki(
            root,
            force=args.force,
            include_sessions=bool(args.with_sessions) and not args.no_sessions,
            verbose=args.verbose,
        )

    import json
    print(json.dumps(result, indent=2, default=str))
