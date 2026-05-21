#!/usr/bin/env python3
"""code_db.py — wrapper sqlite-vec per `.anjawiki/code-index.db`.

Schema:
  chunks       — metadata + content dei chunk (id, file_path, func_name, line_start, line_end, content, lang, last_modified, sha)
  chunk_vec    — virtual table vec0 con embedding per chunk (rowid = chunks.id)
  meta         — last_indexed_sha, provider_name, dim, model

Lazy import sqlite-vec (deps esterna ~5MB). Errore graceful se manca.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional


CODE_DB_FILENAME = "code-index.db"


def _ensure_sqlite_vec(db: sqlite3.Connection) -> None:
    """Carica sqlite-vec extension. Lazy import + load."""
    try:
        import sqlite_vec  # noqa
    except ImportError:
        raise RuntimeError(
            "sqlite-vec required for code search. Install: pip install sqlite-vec"
        )
    db.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(db)
    db.enable_load_extension(False)


def open_db(anjawiki_root: Path, dim: int = 1536, create_if_missing: bool = True) -> sqlite3.Connection:
    """Apre/crea la code-index.db sotto `.anjawiki/`.

    Args:
      anjawiki_root: path a `.anjawiki/` (NON `.anjawiki/wiki/`)
      dim: dimensione embedding (varia per provider/model)
      create_if_missing: True → crea schema se db non esiste
    """
    db_path = anjawiki_root / CODE_DB_FILENAME
    if not create_if_missing and not db_path.exists():
        raise FileNotFoundError(f"code-index.db not found at {db_path}")

    anjawiki_root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    _ensure_sqlite_vec(db)
    _init_schema(db, dim=dim)
    return db


def _init_schema(db: sqlite3.Connection, dim: int) -> None:
    """Crea tabelle se mancano. Idempotente."""
    db.executescript(f"""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            func_name TEXT,
            line_start INTEGER,
            line_end INTEGER,
            content TEXT NOT NULL,
            lang TEXT,
            last_modified TEXT,
            content_sha TEXT,
            UNIQUE(file_path, line_start, line_end)
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(file_path);
        CREATE INDEX IF NOT EXISTS idx_chunks_lang ON chunks(lang);

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # vec virtual table (dim fissa una volta creata; se cambi provider serve drop+recreate)
    existing_dim = get_meta(db, "embed_dim")
    if existing_dim and int(existing_dim) != dim:
        raise RuntimeError(
            f"DB dim mismatch: existing={existing_dim} requested={dim}. "
            f"Run reindex --force (drops + rebuilds) per cambiare provider/model."
        )
    db.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(
            embedding float[{dim}]
        );
    """)
    set_meta(db, "embed_dim", str(dim))
    _migrate_kind_column(db)
    db.commit()


def _migrate_kind_column(db: sqlite3.Connection) -> None:
    """Migration idempotente: aggiunge `kind` discriminator a `chunks`.

    'code' (default, backwards-compat) | 'wiki' (entity/concept/source/analysis/session).
    Code-index esistenti vengono marchiati 'code' automaticamente via DEFAULT.
    """
    cols = {row["name"] for row in db.execute("PRAGMA table_info(chunks)").fetchall()}
    if "kind" in cols:
        return
    db.executescript("""
        ALTER TABLE chunks ADD COLUMN kind TEXT NOT NULL DEFAULT 'code';
        CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind);
        CREATE INDEX IF NOT EXISTS idx_chunks_kind_path ON chunks(kind, file_path);
    """)


def get_meta(db: sqlite3.Connection, key: str) -> Optional[str]:
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


def _serialize_vec(vec: list[float]) -> bytes:
    """sqlite-vec espone serialize_float32 helper."""
    import sqlite_vec
    return sqlite_vec.serialize_float32(vec)


def upsert_chunk(
    db: sqlite3.Connection,
    file_path: str,
    func_name: Optional[str],
    line_start: int,
    line_end: int,
    content: str,
    lang: str,
    last_modified: str,
    content_sha: str,
    embedding: list[float],
    kind: str = "code",
) -> int:
    """Insert o update chunk + vec. Restituisce chunk_id.

    `kind`: 'code' (default, backwards-compat) | 'wiki'. Vedi anche `upsert_wiki_page`.
    """
    # Cerca esistente
    row = db.execute(
        "SELECT id, content_sha FROM chunks WHERE file_path = ? AND line_start = ? AND line_end = ?",
        (file_path, line_start, line_end),
    ).fetchone()

    if row is not None:
        chunk_id, existing_sha = row[0], row[1]
        if existing_sha == content_sha:
            return chunk_id  # No change
        # Update content + re-embed
        db.execute(
            "UPDATE chunks SET func_name=?, content=?, lang=?, last_modified=?, content_sha=?, kind=? WHERE id=?",
            (func_name, content, lang, last_modified, content_sha, kind, chunk_id),
        )
        db.execute("DELETE FROM chunk_vec WHERE rowid = ?", (chunk_id,))
    else:
        cur = db.execute(
            "INSERT INTO chunks (file_path, func_name, line_start, line_end, content, lang, last_modified, content_sha, kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_path, func_name, line_start, line_end, content, lang, last_modified, content_sha, kind),
        )
        chunk_id = cur.lastrowid

    db.execute(
        "INSERT INTO chunk_vec (rowid, embedding) VALUES (?, ?)",
        (chunk_id, _serialize_vec(embedding)),
    )
    return chunk_id


def upsert_wiki_page(
    db: sqlite3.Connection,
    slug: str,
    file_path: str,
    content: str,
    content_sha: str,
    last_modified: str,
    embedding: list[float],
    page_type: Optional[str] = None,
) -> int:
    """Wrapper di upsert_chunk per pagine wiki (one-shot, no line range).

    `func_name` archivia lo slug, `lang` archivia il page_type (entity/concept/...).
    `line_start=line_end=0` (necessario per UNIQUE constraint).
    """
    return upsert_chunk(
        db=db,
        file_path=file_path,
        func_name=slug,
        line_start=0,
        line_end=0,
        content=content,
        lang=page_type or "markdown",
        last_modified=last_modified,
        content_sha=content_sha,
        embedding=embedding,
        kind="wiki",
    )


def delete_chunks_for_file(
    db: sqlite3.Connection,
    file_path: str,
    kind: Optional[str] = None,
) -> int:
    """Rimuovi tutti i chunks per un file. Se `kind` passato, filtra.
    Restituisce conta righe rimosse."""
    if kind:
        rows = db.execute(
            "SELECT id FROM chunks WHERE file_path = ? AND kind = ?",
            (file_path, kind),
        ).fetchall()
    else:
        rows = db.execute("SELECT id FROM chunks WHERE file_path = ?", (file_path,)).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        return 0
    db.execute(f"DELETE FROM chunk_vec WHERE rowid IN ({','.join('?' * len(ids))})", ids)
    db.execute(f"DELETE FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids)
    return len(ids)


def list_wiki_pages(db: sqlite3.Connection) -> list[dict]:
    """Elenca tutte le pagine wiki indexate (slug + path + hash) per consistency check."""
    rows = db.execute(
        "SELECT id, func_name AS slug, file_path, content_sha, lang AS page_type, last_modified "
        "FROM chunks WHERE kind = 'wiki' ORDER BY func_name"
    ).fetchall()
    return [dict(r) for r in rows]


def vector_search(
    db: sqlite3.Connection,
    query_vec: list[float],
    limit: int = 10,
    lang_filter: Optional[str] = None,
    kind_filter: Optional[str] = None,
    exclude_id: Optional[int] = None,
) -> list[dict]:
    """Top-k vector search per cosine distance. Joina con chunks per metadata.

    sqlite-vec knn richiede `k = ?` predicate (più efficiente di LIMIT plain).
    Filtri lang/kind/exclude_id applicati in Python su over-fetch (3×).
    """
    needs_post_filter = bool(lang_filter or kind_filter or exclude_id is not None)
    k = limit * 3 if needs_post_filter else limit
    sql = """
        SELECT c.id, c.file_path, c.func_name, c.line_start, c.line_end,
               c.content, c.lang, c.kind, c.last_modified, v.distance
        FROM chunk_vec v
        JOIN chunks c ON c.id = v.rowid
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
    """
    params: list = [_serialize_vec(query_vec), k]
    rows = db.execute(sql, params).fetchall()
    if needs_post_filter:
        out = []
        for r in rows:
            if lang_filter and r["lang"] != lang_filter:
                continue
            if kind_filter and r["kind"] != kind_filter:
                continue
            if exclude_id is not None and r["id"] == exclude_id:
                continue
            out.append(r)
            if len(out) >= limit:
                break
        rows = out
    else:
        rows = rows[:limit]
    return [dict(row) for row in rows]


def get_embedding_by_source(
    db: sqlite3.Connection,
    source: str,
    kind: Optional[str] = None,
) -> Optional[dict]:
    """Trova il primo chunk per source (file_path o slug-as-file_path) + kind.

    Ritorna dict con id + metadata (no embedding raw, serve solo l'id per query knn).
    """
    if kind:
        row = db.execute(
            "SELECT id, file_path, func_name, line_start, line_end, lang, kind FROM chunks "
            "WHERE (file_path = ? OR func_name = ?) AND kind = ? LIMIT 1",
            (source, source, kind),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT id, file_path, func_name, line_start, line_end, lang, kind FROM chunks "
            "WHERE file_path = ? OR func_name = ? LIMIT 1",
            (source, source),
        ).fetchone()
    return dict(row) if row else None


def get_embedding_vector(db: sqlite3.Connection, chunk_id: int) -> Optional[list[float]]:
    """Ritorna l'embedding raw per un chunk_id (per usarlo come query knn altrove)."""
    row = db.execute(
        "SELECT embedding FROM chunk_vec WHERE rowid = ?", (chunk_id,)
    ).fetchone()
    if not row:
        return None
    import struct
    blob = row[0]
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def stats(db: sqlite3.Connection) -> dict:
    """Statistiche dell'index."""
    total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    by_lang = {
        row[0]: row[1]
        for row in db.execute("SELECT lang, COUNT(*) FROM chunks GROUP BY lang ORDER BY 2 DESC").fetchall()
    }
    by_kind = {
        row[0]: row[1]
        for row in db.execute("SELECT kind, COUNT(*) FROM chunks GROUP BY kind ORDER BY 2 DESC").fetchall()
    }
    last_modified = db.execute("SELECT MAX(last_modified) FROM chunks").fetchone()[0]
    return {
        "total_chunks": total,
        "by_lang": by_lang,
        "by_kind": by_kind,
        "last_modified": last_modified,
        "embed_dim": get_meta(db, "embed_dim"),
        "embed_provider": get_meta(db, "embed_provider"),
        "embed_model": get_meta(db, "embed_model"),
        "last_indexed_sha": get_meta(db, "last_indexed_sha"),
    }
