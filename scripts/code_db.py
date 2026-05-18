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
    db.commit()


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
) -> int:
    """Insert o update chunk + vec. Restituisce chunk_id."""
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
            "UPDATE chunks SET func_name=?, content=?, lang=?, last_modified=?, content_sha=? WHERE id=?",
            (func_name, content, lang, last_modified, content_sha, chunk_id),
        )
        db.execute("DELETE FROM chunk_vec WHERE rowid = ?", (chunk_id,))
    else:
        cur = db.execute(
            "INSERT INTO chunks (file_path, func_name, line_start, line_end, content, lang, last_modified, content_sha) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (file_path, func_name, line_start, line_end, content, lang, last_modified, content_sha),
        )
        chunk_id = cur.lastrowid

    db.execute(
        "INSERT INTO chunk_vec (rowid, embedding) VALUES (?, ?)",
        (chunk_id, _serialize_vec(embedding)),
    )
    return chunk_id


def delete_chunks_for_file(db: sqlite3.Connection, file_path: str) -> int:
    """Rimuovi tutti i chunks per un file (es. file cancellato o re-indexato).
    Restituisce conta righe rimosse."""
    rows = db.execute("SELECT id FROM chunks WHERE file_path = ?", (file_path,)).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        return 0
    db.execute(f"DELETE FROM chunk_vec WHERE rowid IN ({','.join('?' * len(ids))})", ids)
    db.execute(f"DELETE FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids)
    return len(ids)


def vector_search(
    db: sqlite3.Connection,
    query_vec: list[float],
    limit: int = 10,
    lang_filter: Optional[str] = None,
) -> list[dict]:
    """Top-k vector search per cosine distance. Joina con chunks per metadata.

    sqlite-vec knn richiede `k = ?` predicate (più efficiente di LIMIT plain).
    Se lang_filter è attivo, sovra-recuperiamo k*3 e filtriamo in Python.
    """
    k = limit * 3 if lang_filter else limit
    sql = """
        SELECT c.id, c.file_path, c.func_name, c.line_start, c.line_end,
               c.content, c.lang, c.last_modified, v.distance
        FROM chunk_vec v
        JOIN chunks c ON c.id = v.rowid
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
    """
    params: list = [_serialize_vec(query_vec), k]
    rows = db.execute(sql, params).fetchall()
    if lang_filter:
        rows = [r for r in rows if r["lang"] == lang_filter][:limit]
    else:
        rows = rows[:limit]
    return [dict(row) for row in rows]


def stats(db: sqlite3.Connection) -> dict:
    """Statistiche dell'index."""
    total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    by_lang = {
        row[0]: row[1]
        for row in db.execute("SELECT lang, COUNT(*) FROM chunks GROUP BY lang ORDER BY 2 DESC").fetchall()
    }
    last_modified = db.execute("SELECT MAX(last_modified) FROM chunks").fetchone()[0]
    return {
        "total_chunks": total,
        "by_lang": by_lang,
        "last_modified": last_modified,
        "embed_dim": get_meta(db, "embed_dim"),
        "embed_provider": get_meta(db, "embed_provider"),
        "embed_model": get_meta(db, "embed_model"),
        "last_indexed_sha": get_meta(db, "last_indexed_sha"),
    }
