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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


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
    for md in sorted(wiki_root.rglob("*.md")):
        if md.name in _ALWAYS_SKIP_FILES:
            continue
        if any(part in _DEFAULT_SKIP_DIRS for part in md.parts):
            continue
        if not include_sessions and "sessions" in md.parts:
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
    include_sessions: bool = True,
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
    code_db, skill_parser, embed_providers = _get_modules()
    anjawiki_dir = root / ".anjawiki"
    wiki_root = anjawiki_dir / "wiki"
    if not wiki_root.is_dir():
        return {"error": f"no wiki dir at {wiki_root}", "scanned": 0}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embedding provider configured (set ANJA_EMBED_PROVIDER + API key)", "scanned": 0}

    start = datetime.now(timezone.utc)
    db = code_db.open_db(anjawiki_dir, dim=provider.dim)
    code_db.set_meta(db, "embed_provider", provider.name)
    code_db.set_meta(db, "embed_model", provider.model)

    # 1. Discovery filesystem
    md_files = _iter_wiki_files(wiki_root, include_sessions=include_sessions)
    if verbose:
        print(f"[wiki_embed] scanned {len(md_files)} .md files under {wiki_root}", file=sys.stderr)

    # 2. Dirty detection
    existing_in_db = {row["file_path"]: row for row in code_db.list_wiki_pages(db)}
    fs_paths = {str(md) for md in md_files}

    to_embed: list[tuple[Path, str, dict, str, str, str]] = []  # (path, slug, meta, body_input, hash, page_type)
    skipped = 0
    errors: list[str] = []

    for md in md_files:
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errors.append(f"read {md}: {e}")
            continue
        meta, body = skill_parser.parse_frontmatter(text)
        if not isinstance(meta, dict):
            meta = {}
        input_text = _compute_input_text(meta, body)
        body_hash = _compute_hash(input_text)
        slug = _slug_from_path(md, wiki_root)
        page_type = (meta.get("type") or "").strip() or "page"

        existing = existing_in_db.get(str(md))
        if not force and existing and existing["content_sha"] == body_hash:
            skipped += 1
            continue
        to_embed.append((md, slug, meta, input_text, body_hash, page_type))

    # 3. Embed in batch
    embedded = 0
    for i in range(0, len(to_embed), batch_size):
        batch = to_embed[i:i + batch_size]
        texts = [b[3] for b in batch]
        try:
            vectors = provider.embed(texts)
        except Exception as e:
            errors.append(f"provider.embed batch {i}: {e}")
            continue
        if len(vectors) != len(batch):
            errors.append(f"batch {i}: expected {len(batch)} vectors, got {len(vectors)}")
            continue
        last_mod = datetime.now(timezone.utc).isoformat()
        for (md, slug, meta, input_text, body_hash, page_type), vec in zip(batch, vectors):
            try:
                code_db.upsert_wiki_page(
                    db=db,
                    slug=slug,
                    file_path=str(md),
                    content=input_text,
                    content_sha=body_hash,
                    last_modified=last_mod,
                    embedding=vec,
                    page_type=page_type,
                )
                embedded += 1
            except Exception as e:
                errors.append(f"upsert {slug}: {e}")
        db.commit()

    # 4. Orphan cleanup: pagine in DB ma non più nel filesystem
    deleted_orphans = 0
    for file_path, row in existing_in_db.items():
        if file_path not in fs_paths:
            code_db.delete_chunks_for_file(db, file_path, kind="wiki")
            deleted_orphans += 1
    if deleted_orphans:
        db.commit()

    db.close()

    elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    return {
        "scanned": len(md_files),
        "embedded": embedded,
        "skipped_unchanged": skipped,
        "deleted_orphans": deleted_orphans,
        "errors": errors,
        "provider": provider.name,
        "model": provider.model,
        "dim": provider.dim,
        "ms": elapsed_ms,
    }


def embed_single_page(root: Path, md_path: Path) -> dict:
    """Embed di una singola pagina (per trigger inline / hook PostToolUse).

    Più rapido di scan completo: 1 file → 1 embedding call → 1 upsert.
    Restituisce risultato compatto.
    """
    code_db, skill_parser, embed_providers = _get_modules()
    anjawiki_dir = root / ".anjawiki"
    wiki_root = anjawiki_dir / "wiki"
    if not md_path.is_file():
        # Se il file è sparito → orphan cleanup
        provider = embed_providers.get_provider()
        if provider is None:
            return {"error": "no provider"}
        db = code_db.open_db(anjawiki_dir, dim=provider.dim)
        removed = code_db.delete_chunks_for_file(db, str(md_path), kind="wiki")
        db.commit()
        db.close()
        return {"action": "deleted", "removed": removed, "path": str(md_path)}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embedding provider configured"}

    text = md_path.read_text(encoding="utf-8", errors="replace")
    meta, body = skill_parser.parse_frontmatter(text)
    if not isinstance(meta, dict):
        meta = {}
    input_text = _compute_input_text(meta, body)
    body_hash = _compute_hash(input_text)

    db = code_db.open_db(anjawiki_dir, dim=provider.dim)
    code_db.set_meta(db, "embed_provider", provider.name)
    code_db.set_meta(db, "embed_model", provider.model)

    # Dirty check
    existing = code_db.get_embedding_by_source(db, source=str(md_path), kind="wiki")
    if existing:
        row = db.execute("SELECT content_sha FROM chunks WHERE id = ?", (existing["id"],)).fetchone()
        if row and row["content_sha"] == body_hash:
            db.close()
            return {"action": "skipped_unchanged", "path": str(md_path)}

    vectors = provider.embed([input_text])
    if not vectors:
        db.close()
        return {"error": "empty embedding response"}

    slug = _slug_from_path(md_path, wiki_root)
    page_type = (meta.get("type") or "").strip() or "page"
    last_mod = datetime.now(timezone.utc).isoformat()
    code_db.upsert_wiki_page(
        db=db,
        slug=slug,
        file_path=str(md_path),
        content=input_text,
        content_sha=body_hash,
        last_modified=last_mod,
        embedding=vectors[0],
        page_type=page_type,
    )
    db.commit()
    db.close()
    return {"action": "embedded", "slug": slug, "path": str(md_path)}


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Embed wiki pages into code-index DB")
    ap.add_argument("root", help="project root (parent of .anjawiki/)")
    ap.add_argument("--force", action="store_true", help="re-embed all pages, ignore dirty check")
    ap.add_argument("--no-sessions", action="store_true", help="skip wiki/sessions/")
    ap.add_argument("--single", help="embed only this single file (absolute path)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if args.single:
        md_path = Path(args.single).expanduser().resolve()
        result = embed_single_page(root, md_path)
    else:
        result = embed_wiki(
            root,
            force=args.force,
            include_sessions=not args.no_sessions,
            verbose=args.verbose,
        )

    import json
    print(json.dumps(result, indent=2, default=str))
