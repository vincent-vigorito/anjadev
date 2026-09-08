"""Snapshot filesystem → staging limitato su disco → pubblicazione SQLite atomica."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import index_policy

import code_db
import code_index
import embed_providers
import skill_parser

MAX_FILE_BYTES = 500_000


def now():
    return datetime.now(timezone.utc).isoformat()


def snapshot_file(path):
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        data = stream.read(MAX_FILE_BYTES + 1)
    reason = "too_large" if len(data) > MAX_FILE_BYTES else ("empty" if not data.strip() else None)
    digest = (f"oversize:{stat.st_size}:{stat.st_mtime_ns}" if reason == "too_large"
              else hashlib.sha256(data).hexdigest())
    return {"hash": digest, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "excluded": reason}, (
        "" if reason else data.decode("utf-8", errors="replace"))


def discover(root, kind, include_sessions=False, single=None):
    return index_policy.discover(root, kind, include_sessions, single)[0]


def file_key(root, kind, path):
    return str(path.relative_to(root)) if kind == "code" else str(path)


def snapshot(root, kind, include_sessions=False, single=None):
    return {file_key(root, kind, path): snapshot_file(path)[0]
            for path in discover(root, kind, include_sessions, single)}


def content_equal(first, second):
    return first is not None and first["hash"] == second["hash"] and first["excluded"] == second["excluded"]


def chunks(root, kind, path, text):
    if kind == "code":
        return [{**chunk, "lang": code_index.LANG_BY_EXT[path.suffix.lower()]}
                for chunk in code_index.chunk_text(text, path.suffix)]
    import wiki_embed
    meta, body = skill_parser.parse_frontmatter(text)
    meta = meta if isinstance(meta, dict) else {}
    content = wiki_embed._compute_input_text(meta, body)
    return [{"content": content, "func_name": wiki_embed._slug_from_path(path, root / ".anjawiki" / "wiki"),
             "line_start": 0, "line_end": 0, "lang": str(meta.get("type") or "page")}]


def _manifest(db):
    return {(row["kind"], row["file_path"]): json.loads(row["snapshot"])
            for row in db.execute("SELECT * FROM indexed_files")}


def refresh(root, kind, **kwargs):
    if kwargs.pop("dry_run", False):
        try:
            return index_policy.preview(root, kind, kwargs.get("include_sessions", False), kwargs.get("single"))
        except Exception as exc:
            return {"error": str(exc), "code": "index_policy_error", "dry_run": True}
    started = time.monotonic()
    for attempt in range(3):
        result = _refresh(root, kind, **kwargs)
        result["attempts"] = attempt + 1
        if result.get("error") != "another index was published during embedding; retry":
            break
    result["ms"] = round((time.monotonic() - started) * 1000)
    return result


def _refresh(root, kind, force=False, limit=None, include_sessions=False, single=None, batch_size=64, verbose=False, expected_snapshot=None, expected_fingerprint=None):
    root = Path(root).resolve()
    directory = root / ".anjawiki"
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        return {"error": "project state must remain inside root", "status": "failed", "code": "unsafe_project_state"}
    if not directory.is_dir():
        return {"error": f"`.anjawiki/` not found under {root}", "status": "failed", "code": "project_missing"}
    if limit is not None and (not isinstance(limit, int) or limit <= 0):
        return {"error": "limit must be a positive integer", "status": "failed", "code": "invalid_index_options"}
    if not isinstance(batch_size, int) or batch_size <= 0:
        return {"error": "batch_size must be positive", "status": "failed", "code": "invalid_index_options"}
    db = None
    run_id = uuid.uuid4().hex
    state = "failed"
    details = {}
    try:
        index_policy.authorize(root)
        provider = embed_providers.get_provider()
        if provider is None:
            return {"error": "no embed provider available (set ANJA_EMBED_PROVIDER + API key)", "status": "failed", "code": "provider_unavailable"}
        index_policy.authorize(root, provider)
        if not isinstance(provider.dim, int) or provider.dim <= 0:
            raise ValueError("invalid provider dimension")
        db = code_db.open_db(directory, dim=provider.dim, allow_dimension_mismatch=True)
        generation = code_db.get_meta(db, "index_generation") or "0"
        old_fp = code_db.get_meta(db, "index_fingerprint")
        new_fp = code_db.fingerprint(provider)
        if expected_fingerprint is not None and expected_fingerprint != new_fp:
            return {"status": "superseded", "error": "job fingerprint changed"}
        if expected_snapshot is not None:
            discover(root, "wiki", single=single)
            actual = snapshot_file(Path(single))[0]["hash"] if Path(single).is_file() else "missing"
            if actual != expected_snapshot:
                return {"status": "superseded", "error": "job content changed"}

        manifest = _manifest(db)
        existing_rows = db.execute("SELECT DISTINCT kind, file_path FROM chunks").fetchall()
        migration = (old_fp != new_fp and bool(old_fp or existing_rows or manifest)) or int(code_db.get_meta(db, "embed_dim")) != provider.dim
        db.execute("INSERT INTO index_runs(id,kind,status,started,pid,details) VALUES (?,?,?,?,?,?)",
                   (run_id, kind, "building", now(), os.getpid(), "{}"))
        db.commit()
        if migration and limit is not None:
            state = "partial"
            raise ValueError("model/pipeline migration requires a complete rebuild without limit")
        if migration:
            from project_recovery import snapshot_project
            backup = snapshot_project(root, reason="embedding_migration")
            details["backup"] = backup
        scopes = ("code", "wiki") if migration else (kind,)
        old_sessions = any(row["kind"] == "wiki" and Path(row["file_path"]).is_relative_to(directory / "wiki" / "sessions")
                           for row in existing_rows)
        sessions = include_sessions or (migration and old_sessions)
        # Un job single non può pubblicare un modello nuovo per una sola pagina.
        selected_single = None if migration else single
        current = {}
        selected = set()
        deleted = set()
        for scope in scopes:
            scan = snapshot(root, scope, sessions, selected_single if scope == "wiki" else None)
            current.update({(scope, path): info for path, info in scan.items()})
            changed = [(scope, path) for path, info in scan.items()
                       if force or migration or not content_equal(manifest.get((scope, path)), info)]
            selected.update(changed[:limit] if limit is not None else changed)
            old_paths = {path for k, path in manifest if k == scope}
            old_paths.update(row["file_path"] for row in existing_rows if row["kind"] == scope)
            if scope == "wiki":
                if selected_single is not None:
                    old_paths &= {str(selected_single)}
                elif not sessions:
                    old_paths = {p for p in old_paths if not Path(p).is_relative_to(directory / "wiki" / "sessions")}
            deleted.update((scope, p) for p in old_paths - scan.keys())
        deferred = [path for (scope, path), info in current.items()
                    if (scope, path) not in selected and not content_equal(manifest.get((scope, path)), info)]
        details = {**details, "scopes": list(scopes), "migration": migration, "deferred_files": deferred,
                   "excluded_files": [{"path": p, "reason": info["excluded"]}
                                      for (_k, p), info in current.items() if info["excluded"]],
                   "selected_files": len(selected), "deleted_files": len(deleted)}
        db.execute("UPDATE index_runs SET details=? WHERE id=?", (json.dumps(details), run_id))
        db.commit()
        sha = code_index.get_current_git_sha(root)
        indexed_chunks = 0
        # Sempre staging su disco: memoria limitata a un file e un batch di vettori.
        with tempfile.TemporaryDirectory(prefix=".index-stage-", dir=directory) as tmp:
            stage = sqlite3.connect(str(Path(tmp) / "stage.db"))
            try:
                stage.execute("CREATE TABLE staged (kind TEXT, path TEXT, chunk TEXT, vector TEXT)")
                for scope, path in sorted(selected):
                    source = root / path if scope == "code" else Path(path)
                    info, text = snapshot_file(source)
                    if info != current[(scope, path)]:
                        state = "stale"
                        raise RuntimeError("file changed while collecting chunks; retry")
                    if info["excluded"]:
                        continue
                    for chunk in chunks(root, scope, source, text):
                        stage.execute("INSERT INTO staged VALUES (?,?,?,NULL)", (scope, path, json.dumps(chunk)))
                stage.commit()
                cursor = stage.execute("SELECT rowid, chunk FROM staged ORDER BY rowid")
                while batch := cursor.fetchmany(batch_size):
                    for scope in scopes:
                        admitted = {file_key(root, scope, path) for path in discover(root, scope, sessions, selected_single if scope == "wiki" else None)}
                        if admitted != {path for k, path in current if k == scope}:
                            state = "stale"
                            raise RuntimeError("discovery policy changed before embedding batch")
                    index_policy.authorize(root, provider)
                    vectors = provider.embed([json.loads(row[1])["content"] for row in batch])
                    code_db.validate_vectors(vectors, len(batch), provider.dim)
                    stage.executemany("UPDATE staged SET vector=? WHERE rowid=?",
                                      [(json.dumps(vec), row[0]) for row, vec in zip(batch, vectors)])
                    indexed_chunks += len(batch)
                stage.commit()
                # Nessuna chiamata remota dopo BEGIN: vecchi dati e checkpoint restano leggibili fino al commit.
                db.execute("BEGIN IMMEDIATE")
                current_generation = code_db.get_meta(db, "index_generation") or "0"
                concurrent = current_generation != generation
                if concurrent and (migration or code_db.get_meta(db, "index_fingerprint") != new_fp):
                    state = "stale"
                    raise RuntimeError("another index was published during embedding; retry")
                for scope in scopes:
                    latest = snapshot(root, scope, sessions, selected_single if scope == "wiki" else None)
                    if latest != {p: info for (k, p), info in current.items() if k == scope}:
                        state = "stale"
                        raise RuntimeError("another index was published during embedding; retry" if concurrent
                                           else "filesystem changed during embedding; retry")
                if migration:
                    code_db.reset_vectors(db, provider.dim)
                for scope, path in selected | deleted:
                    code_db.delete_chunks_for_file(db, path, kind=scope)
                    db.execute("DELETE FROM indexed_files WHERE kind=? AND file_path=?", (scope, path))
                for scope, path, raw_chunk, raw_vec in stage.execute("SELECT kind,path,chunk,vector FROM staged ORDER BY rowid"):
                    chunk = json.loads(raw_chunk)
                    info = current[(scope, path)]
                    code_db.upsert_chunk(db, file_path=path, func_name=chunk["func_name"],
                                         line_start=chunk["line_start"], line_end=chunk["line_end"],
                                         content=chunk["content"], lang=chunk["lang"],
                                         last_modified=datetime.fromtimestamp(info["mtime_ns"] / 1e9, timezone.utc).isoformat(),
                                         content_sha=hashlib.sha256(chunk["content"].encode()).hexdigest(),
                                         embedding=json.loads(raw_vec), kind=scope)
                for key, info in current.items():
                    if key in selected or content_equal(manifest.get(key), info):
                        db.execute("INSERT OR REPLACE INTO indexed_files VALUES (?,?,?)", (*key, json.dumps(info)))
                state = "partial" if deferred or selected_single is not None else "ready"
                finished = now()
                metadata = {"embed_provider": provider.name, "embed_model": provider.model,
                            "index_fingerprint": new_fp, "index_generation": str(int(current_generation) + 1)}
                for scope in scopes:
                    metadata[scope + "_index_state"] = state
                    if state == "ready":
                        metadata[scope + "_last_success"] = finished
                if "code" in scopes and state == "ready":
                    metadata["last_indexed_sha"] = sha or ""
                    metadata["last_indexed_at"] = finished
                for key, value in metadata.items():
                    code_db.set_meta(db, key, value, commit=False)
                db.execute("UPDATE index_runs SET status=?,finished=?,details=? WHERE id=?",
                           (state, finished, json.dumps(details), run_id))
                db.commit()
            finally:
                stage.close()
        return {"status": state, "indexed_files": len(selected), "indexed_chunks": indexed_chunks,
                "scanned": len(current), "embedded": indexed_chunks, "deleted_orphans": len(deleted),
                "skipped_unchanged": len(current) - len(selected) - len(deferred),
                "provider": provider.name, "model": provider.model, "dim": provider.dim, "git_sha": sha,
                "incremental": not (force or migration), "errors": [], **details}
    except Exception as exc:
        if state not in ("stale", "partial"):
            state = "failed"
        if db is not None:
            db.rollback()
            db.execute("UPDATE index_runs SET status=?,finished=?,error=?,details=? WHERE id=?",
                       (state, now(), str(exc), json.dumps(details), run_id))
            db.commit()
        return {"error": str(exc), "errors": [str(exc)], "status": state, "code": getattr(exc, "code", "index_build_failed"), "indexed_files": 0, "indexed_chunks": 0, **details}
    finally:
        if db is not None:
            db.close()


def index_status(root, provider=None):
    root = Path(root).resolve()
    path = root / ".anjawiki" / code_db.CODE_DB_FILENAME
    if not path.is_file():
        return {"indexed": False, "status": "missing", "hint": "Run code.reindex"}
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        meta = dict(db.execute("SELECT key,value FROM meta"))
        manifest = _manifest(db) if "indexed_files" in tables else {}
        compatible = provider is not None and meta.get("index_fingerprint") == code_db.fingerprint(provider)
        scopes = {}
        for kind in ("code", "wiki"):
            run = db.execute("SELECT * FROM index_runs WHERE kind=? ORDER BY started DESC LIMIT 1", (kind,)).fetchone() if "index_runs" in tables else None
            attempt = dict(run) if run else None
            state = meta.get(kind + "_index_state", "missing")
            changed = []
            if attempt:
                attempt["details"] = json.loads(attempt["details"] or "{}")
                if attempt["started"] > meta.get(kind + "_last_success", ""):
                    state = attempt["status"]
                if state == "building":
                    try:
                        os.kill(attempt["pid"], 0)
                    except ProcessLookupError:
                        state = "failed"
                        attempt["error"] = "index process interrupted before completion"
                    except PermissionError:
                        pass
            if not compatible:
                state = "incompatible" if provider is not None else "provider_unavailable"
            elif state not in ("building", "failed"):
                previous = {p: info for (k, p), info in manifest.items() if k == kind}
                sessions = any(Path(p).is_relative_to(root / ".anjawiki/wiki/sessions") for p in previous) if kind == "wiki" else False
                try:
                    current = snapshot(root, kind, sessions)
                    changed = sorted(p for p in current if not content_equal(previous.get(p), current[p]))
                    changed += sorted(previous.keys() - current.keys())
                    if changed and state != "partial":
                        state = "stale"
                except OSError as exc:
                    state = "stale"
                    changed = [str(exc)]
            scopes[kind] = {"status": state, "last_success": meta.get(kind + "_last_success"),
                            "changed_files": changed, "last_attempt": attempt}
        result = code_db.stats(db)
        result.update({"indexed": bool(meta.get("index_fingerprint")), "status": scopes["code"]["status"],
                       "scopes": scopes, "fingerprint": meta.get("index_fingerprint"),
                       "db_path": ".anjawiki/code-index.db", "db_size_mb": round(path.stat().st_size / 1024**2, 2)})
        return result
    finally:
        db.close()
