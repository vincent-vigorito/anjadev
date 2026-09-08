"""Gruppo `wiki` (parte 1): search, read, upsert, overview, index, backlinks, find_duplicates."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import trust
from .common import (
    _compose_frontmatter,
    _compose_sections,
    _confined_path,
    _iter_wiki_md,
    _parse_frontmatter,
    _parse_sections,
    _today_iso,
    _wiki_page,
    _wiki_root,
)
from .config import PLUGIN_ROOT, ROOT, SCRIPTS_DIR, log_exc
from .persistence import check_revision, persistence_errors, read_text, revision, write_text
from .wiki_maint import _SLUG_RE, _now_iso_utc


def tool_wiki_search(args: dict) -> dict:
    """Search nelle pagine wiki filtrando per type (entity/concept/source/analysis/all).

    Differenza vs memory.recall: filtra per cartella, ritorna metadati strutturati
    (type, updated, slug) utili per follow-up con wiki.read(slug).
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query required"}
    type_filter = (args.get("type") or "all").strip().lower()
    limit = int(args.get("limit", 10))

    valid_types = ("all", "entity", "concept", "source", "analysis", "session", "overview", "index")
    if type_filter not in valid_types:
        return {"error": f"type must be one of {valid_types}"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"results": [], "_warning": f"wiki dir not found: {wiki}"}

    keywords = [w.lower() for w in re.findall(r"\b\w{3,}\b", query)]
    if not keywords:
        return {"results": []}

    # Pluralized folder names per type
    folder_map = {
        "entity": "entities", "concept": "concepts", "source": "sources",
        "analysis": "analysis", "session": "sessions",
    }
    if type_filter == "all":
        scan_paths = [wiki]
    elif type_filter in folder_map:
        sub = wiki / folder_map[type_filter]
        scan_paths = [sub] if sub.is_dir() else []
    else:
        # overview/index — file singolo a root wiki
        single = wiki / f"{type_filter}.md"
        scan_paths = [single] if single.is_file() else []

    matches = []
    for path in scan_paths:
        files = [path] if path.is_file() else list(_iter_wiki_md(path))
        for f in files:
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                _confined_path(wiki, f)
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception as _exc:
                log_exc("wiki.tool_wiki_search", _exc)
                continue
            tl = text.lower()
            score = sum(tl.count(kw) for kw in keywords)
            if score == 0:
                continue
            # frontmatter
            title = f.stem
            ftype = ""
            updated = ""
            mt = re.search(r"^title:\s*(.+?)$", text, re.M)
            if mt:
                title = mt.group(1).strip().strip('"').strip("'")
            mty = re.search(r"^type:\s*(.+?)$", text, re.M)
            if mty:
                ftype = mty.group(1).strip().strip('"').strip("'")
            mu = re.search(r"^updated:\s*(.+?)$", text, re.M)
            if mu:
                updated = mu.group(1).strip().strip('"').strip("'")
            # preview: prima riga matchante
            preview = ""
            for line in text.split("\n"):
                if any(kw in line.lower() for kw in keywords):
                    preview = line.strip()[:160]
                    break
            try:
                rel = str(f.relative_to(ROOT))
            except ValueError:
                rel = str(f)
            matches.append({
                "slug": f.stem,
                "title": title,
                "type": ftype or (f.parent.name.rstrip("s") if f.parent.name in folder_map.values() else "page"),
                "path": rel,
                "score": score,
                "updated": updated,
                "preview": preview,
            })

    matches.sort(key=lambda x: x["score"], reverse=True)
    return {"results": matches[:limit], "total_matches": len(matches)}


def _rrf_fuse(ranked_lists, k=60, weights=None):
    """Reciprocal Rank Fusion. Ogni lista è una sequenza di chiavi ordinate per rank
    (rank 1 = migliore). Fonde per POSIZIONE, non per score assoluto (BM25 e cosine
    non sono comparabili): score(key) = Σ w / (k + rank). Ritorna {key: rrf_score}."""
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    scores = {}
    for lst, w in zip(ranked_lists, weights):
        for rank, key in enumerate(lst, start=1):
            scores[key] = scores.get(key, 0.0) + w / (k + rank)
    return scores


def _rel_to_root(p):
    try:
        return str(Path(p).resolve().relative_to(ROOT.resolve()))
    except (ValueError, OSError):
        return str(p)


def _wiki_vector_search(query, type_filter, limit, include_sessions):
    """Canale vector del wiki: embedda la query e fa k-NN su kind='wiki'.
    Ritorna (hits, note). note != None se il canale non è disponibile (no provider/index)."""
    try:
        import sys as _sys
        here = SCRIPTS_DIR
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db
        import embed_providers
    except ImportError as e:
        return [], f"module missing: {e}"

    try:
        provider = embed_providers.get_project_provider(ROOT)
    except ValueError as exc:
        return [], str(exc)
    if provider is None:
        return [], "no embed provider (set ANJA_EMBED_PROVIDER + API key)"
    anjawiki = ROOT / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return [], "vector index not built (run wiki.embed / code.reindex)"
    db = None
    try:
        qv = provider.embed([query])
        code_db.validate_vectors(qv, 1, provider.dim)
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False, provider=provider)
        hits = code_db.vector_search(db, qv[0], limit=limit, kind_filter="wiki")
    except Exception as e:
        return [], f"vector search failed: {e}"
    finally:
        if db is not None:
            db.close()

    out = []
    for h in hits:
        page_type = h.get("lang") or "page"
        if not include_sessions and page_type == "session":
            continue
        if type_filter != "all" and page_type != type_filter:
            continue
        slug_full = h.get("func_name") or ""
        out.append({
            "key": str(Path(h["file_path"]).resolve()),
            "slug": slug_full.split(":")[-1],
            "title": slug_full.split(":")[-1],
            "type": page_type,
            "path": _rel_to_root(h["file_path"]),
            "preview": (h.get("content") or "")[:160].replace("\n", " ").strip(),
        })
    return out, None


def tool_wiki_search_hybrid(args: dict) -> dict:
    """🔀 Ricerca wiki IBRIDA (keyword + vector) fusa via Reciprocal Rank Fusion.

    Usa SEMPRE entrambi i canali quando disponibili e li fonde per rango: robusta sia
    su query esatte (nomi/comandi → keyword) sia concettuali (riformulazioni → vector).
    Degrada a solo keyword se manca embed provider/index.

    args: query (req), type ('all'|entity|concept|source|analysis|session), limit (10),
    mode ('hybrid'|'keyword'|'vector', default 'hybrid'), k (60, costante RRF),
    include_sessions (false), weight_keyword/weight_vector (1.0).
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query required"}
    type_filter = (args.get("type") or "all").strip().lower()
    limit = int(args.get("limit", 10))
    mode = (args.get("mode") or "hybrid").strip().lower()
    k = int(args.get("k", 60))
    include_sessions = bool(args.get("include_sessions", False))
    wk = float(args.get("weight_keyword", 1.0))
    wv = float(args.get("weight_vector", 1.0))

    pool = max(limit * 3, 20)
    meta = {}
    kw_rank = []
    vec_rank = []
    note = None

    if mode in ("hybrid", "keyword"):
        kw = tool_wiki_search({"query": query, "type": type_filter, "limit": pool})
        for r in kw.get("results", []):
            if not include_sessions and r.get("type") == "session":
                continue
            key = str((ROOT / r["path"]).resolve())
            m = meta.setdefault(key, {"slug": r["slug"], "title": r["title"],
                                      "type": r["type"], "path": r["path"],
                                      "preview": r.get("preview", ""), "channels": []})
            m["channels"].append("keyword")
            kw_rank.append(key)

    if mode in ("hybrid", "vector"):
        hits, vnote = _wiki_vector_search(query, type_filter, pool, include_sessions)
        if vnote:
            note = vnote
        for h in hits:
            key = h["key"]
            m = meta.setdefault(key, {"slug": h["slug"], "title": h["title"],
                                      "type": h["type"], "path": h["path"],
                                      "preview": h["preview"], "channels": []})
            m["channels"].append("vector")
            vec_rank.append(key)

    if not meta:
        out = {"results": [], "count": 0, "method": f"rrf_{mode}"}
        if note:
            out["_note"] = note
        return out

    fused = _rrf_fuse([kw_rank, vec_rank], k=k, weights=[wk, wv])
    ranked = sorted(fused.keys(), key=lambda key: -fused[key])[:limit]
    results = []
    for key in ranked:
        m = meta[key]
        results.append({
            "slug": m["slug"], "title": m["title"], "type": m["type"],
            "path": m["path"], "rrf_score": round(fused[key], 5),
            "channels": sorted(set(m["channels"])), "preview": m["preview"],
        })
    channels_used = []
    if kw_rank:
        channels_used.append("keyword")
    if vec_rank:
        channels_used.append("vector")
    out = {"results": results, "count": len(results),
           "method": f"rrf_{mode}", "k": k, "channels_used": channels_used}
    if note:
        out["_note"] = note
    return out


def tool_wiki_find_duplicates(args: dict) -> dict:
    """🔎 Trova coppie di pagine wiki semanticamente troppo simili (candidati duplicati
    / da fondere, o potenzialmente contraddittorie) via gli embeddings condivisi
    (code-index.db). Vede ciò che il match esatto NON vede (es. 'auth-service' vs
    'authentication'). Complementa il lint.

    args: threshold (default 0.85 similarity coseno), types (lista page_type, default
    ['entity','concept']), limit (default 20 coppie).
    """
    threshold = float(args.get("threshold", 0.85))
    types = args.get("types") or ["entity", "concept"]
    if isinstance(types, str):
        types = [types]
    limit = int(args.get("limit", 20))

    try:
        import sys as _sys
        here = SCRIPTS_DIR
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db
        import embed_providers
    except ImportError as e:
        return {"error": f"module missing: {e}"}

    try:
        provider = embed_providers.get_project_provider(ROOT)
    except ValueError as exc:
        return {"error": str(exc), "code": "index_policy_error"}
    if provider is None:
        return {"error": "no embed provider available (set ANJA_EMBED_PROVIDER + API key)"}
    anjawiki = ROOT / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return {"error": "vector index not built — run wiki.embed / code.reindex first"}
    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False, provider=provider)
    except Exception as e:
        return {"error": f"db open failed: {e}"}

    try:
        pages = [p for p in code_db.list_wiki_pages(db)
                 if (p.get("page_type") or "") in types]
        seen_pairs = {}
        for p in pages:
            vec = code_db.get_embedding_vector(db, p["id"])
            if vec is None:
                continue
            neighbors = code_db.vector_search(db, vec, limit=4, kind_filter="wiki", exclude_id=p["id"])
            for n in neighbors:
                if (n.get("lang") or "") not in types:
                    continue
                score = 1.0 - float(n["distance"])
                if score < threshold:
                    continue
                a = (p.get("slug") or "").split(":")[-1]
                b = (n.get("func_name") or "").split(":")[-1]
                if not a or not b or a == b:
                    continue
                key = tuple(sorted([a, b]))
                if key not in seen_pairs or score > seen_pairs[key]["score"]:
                    seen_pairs[key] = {"pages": list(key), "score": round(score, 4),
                                       "type": p.get("page_type")}
    finally:
        try:
            db.close()
        except Exception as _exc:
            log_exc("wiki.tool_wiki_find_duplicates", _exc)
            pass

    pairs = sorted(seen_pairs.values(), key=lambda x: -x["score"])[:limit]
    return {
        "duplicates": pairs,
        "count": len(pairs),
        "threshold": threshold,
        "types": types,
        "_hint": "Coppie sopra soglia = candidati merge/conflitto. Verifica con wiki.read prima di unire.",
    }


def tool_wiki_read(args: dict) -> dict:
    """Legge una pagina wiki per slug. Ricerca breadth-first in wiki/ + sottocartelle."""
    slug = (args.get("slug") or "").strip()
    if not slug:
        return {"error": "slug required"}
    # Strip .md if user passes filename
    if slug.endswith(".md"):
        slug = slug[:-3]

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    try:
        target = _wiki_page(wiki, slug)
    except (ValueError, OSError, RuntimeError) as e:
        return {"error": str(e)}
    if not target:
        return {"error": f"page not found: {slug}"}

    try:
        text = read_text(target)
        if text is None:
            return {"error": "page disappeared; retry the read"}
    except Exception as e:
        return {"error": f"read error: {e}"}

    content_revision = revision(text)
    verification = trust.status(text)
    # Cap a 10k chars (~2500 token) per evitare context blow-up
    max_chars = int(args.get("max_chars", 10000))
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... [troncato a {max_chars} chars, totale {len(text)}]"

    try:
        rel = str(target.relative_to(ROOT))
    except ValueError:
        rel = str(target)
    return {"slug": slug, "path": rel, "content": text, "size": len(text), "revision": content_revision, **verification}


def _plugin_version() -> str:
    """Versione del plugin dal manifest (per l'actor `anjadev/<version>`)."""
    global _PLUGIN_VERSION_CACHE
    if _PLUGIN_VERSION_CACHE is None:
        try:
            pj = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
            _PLUGIN_VERSION_CACHE = json.loads(pj.read_text(encoding="utf-8")).get("version", "unknown")
        except Exception as _exc:
            log_exc("wiki._plugin_version", _exc)
            _PLUGIN_VERSION_CACHE = "unknown"
    return _PLUGIN_VERSION_CACHE


_PLUGIN_VERSION_CACHE = None


def _actor() -> str:
    """Convenzione attori OKF §7: <producer>/<version> | human:<id> | process:<id>.
    Override via ANJA_ACTOR (es. l'hub può stampare process:routine-x)."""
    return os.environ.get("ANJA_ACTOR") or f"anjadev/{_plugin_version()}"


_EXTRA_FM_FIELDS = ("source_path", "subtype", "git_sha", "analyzed_at", "question", "transient",
                    "status", "stale_after")


# Schema 1.1 (semantica OKF v0.2 §5.4/§5.5)
_VALID_STATUS = ("draft", "stable", "deprecated")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_CANONICAL_SECTIONS = {
    "entity": ["Sintesi", "Dettagli", "Apparizioni", "Connessioni"],
    "concept": ["Definizione", "Perché conta in questo progetto", "Esempi nel progetto", "Riferimenti"],
    "source": ["Punti chiave", "Pagine wiki coinvolte"],
    "analysis": ["Domanda", "Risposta", "Pagine usate"],
}


def _compute_canonical_warnings(sections_keys: list[str], page_type: str) -> list[str]:
    """Verifica sezioni canoniche del template anjadev. Lista warning strings,
    vuota se tutte presenti. Case-insensitive, strip whitespace."""
    canonical = _CANONICAL_SECTIONS.get(page_type, [])
    if not canonical:
        return []
    existing_norm = {s.strip().lower() for s in sections_keys if s and s.strip()}
    missing = [s for s in canonical if s.lower() not in existing_norm]
    if not missing:
        return []
    return [f"missing canonical section '{s}' (recommended for type={page_type})" for s in missing]


@persistence_errors
def _wiki_upsert_page(args: dict, page_type: str, folder: str) -> dict:
    """Upsert generico entity/concept/source/analysis. Merge sezioni replace-by-name.
    Frontmatter extra opt-in: source_path, subtype, git_sha, analyzed_at,
    question, transient (vengono scritti solo se presenti in args)."""
    from collections import OrderedDict

    slug = (args.get("slug") or "").strip()
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return {"error": "slug required"}
    if not _SLUG_RE.match(slug):
        return {"error": f"slug must be kebab-case lowercase ([a-z0-9-]+): '{slug}'"}

    sections_in = args.get("sections") or {}
    if not isinstance(sections_in, dict) or not sections_in:
        return {"error": "sections must be a non-empty dict {section_name: content}"}

    # Schema 1.1: lifecycle opzionale, validato writer-side (siamo noi a scrivere)
    if args.get("status") is not None and args["status"] not in _VALID_STATUS:
        return {"error": f"status must be one of {_VALID_STATUS}"}
    if args.get("stale_after") is not None and not _DATE_RE.match(str(args["stale_after"])):
        return {"error": "stale_after must be an absolute ISO date (YYYY-MM-DD)"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    try:
        target_dir = _confined_path(wiki, wiki / folder)
        target_file = _confined_path(wiki, target_dir / f"{slug}.md")
    except (ValueError, OSError, RuntimeError) as e:
        return {"error": str(e)}
    target_dir.mkdir(parents=True, exist_ok=True)

    today = _today_iso()
    title_in = (args.get("title") or "").strip()
    title_default = slug.replace("-", " ").strip().title()
    sources_in = args.get("sources") or []
    tags_in = args.get("tags") or []

    text = read_text(target_file)
    expected = check_revision(target_file, text, args.get("expected_revision"))
    if text is not None:
        fm, body = _parse_frontmatter(text)
        sections = _parse_sections(body)
        for sec_name, sec_content in sections_in.items():
            sections[sec_name] = (sec_content or "").strip()
        fm["updated"] = today
        fm["type"] = page_type
        if title_in:
            fm["title"] = title_in
        elif "title" not in fm:
            fm["title"] = title_default
        if sources_in:
            existing = fm.get("sources", []) if isinstance(fm.get("sources"), list) else []
            for s in sources_in:
                if s not in existing:
                    existing.append(s)
            fm["sources"] = existing
        if tags_in:
            existing = fm.get("tags", []) if isinstance(fm.get("tags"), list) else []
            for t in tags_in:
                if t not in existing:
                    existing.append(t)
            fm["tags"] = existing
        for fname in _EXTRA_FM_FIELDS:
            if fname in args and args[fname] is not None:
                fm[fname] = args[fname]
        action = "updated"
    else:
        title_final = title_in or title_default
        fm = {
            "title": title_final,
            "type": page_type,
            "created": today,
            "updated": today,
            "sources": list(sources_in),
            "tags": list(tags_in),
        }
        for fname in _EXTRA_FM_FIELDS:
            if fname in args and args[fname] is not None:
                fm[fname] = args[fname]
        sections = OrderedDict()
        sections[""] = f"# {title_final}"
        for sec_name, sec_content in sections_in.items():
            sections[sec_name] = (sec_content or "").strip()
        action = "created"

    # Schema 1.1 (OKF §5.2): chi ha prodotto l'ultima modifica di contenuto.
    # Stampato a OGNI write — generated ≠ verified (chi scrive non è chi conferma).
    fm["generated"] = f"{{ by: {_actor()}, at: {_now_iso_utc()} }}"

    new_text = _compose_frontmatter(fm) + "\n" + _compose_sections(sections)
    new_revision = write_text(target_file, new_text, expected)

    # Trigger re-embed in background (fire-and-forget) — abilita semantic graph k-NN
    _trigger_wiki_embed_bg(target_file)

    # Validation soft: warning se mancano sezioni canoniche post-write (no block)
    final_sections = [k for k in sections.keys() if k]
    warnings = _compute_canonical_warnings(final_sections, page_type)

    result = {
        "slug": slug,
        "path": str(target_file.relative_to(ROOT)),
        "action": action,
        "type": page_type,
        "sections_modified": list(sections_in.keys()),
        "revision": new_revision,
    }
    if warnings:
        result["_warnings"] = warnings
    return result


def _trigger_wiki_embed_bg(md_path: Path) -> None:
    """Accoda lo snapshot; il worker del progetto serializza i job."""
    if os.environ.get("ANJA_WIKI_EMBED", "1") == "0" or not (ROOT / ".anjawiki").is_dir():
        return
    try:
        import wiki_jobs
        wiki_jobs.enqueue(ROOT, md_path)
    except Exception as exc:
        log_exc("wiki._trigger_wiki_embed_bg", exc)


def tool_wiki_upsert_entity(args: dict) -> dict:
    """Crea o aggiorna una pagina entity nel wiki."""
    return _wiki_upsert_page(args, page_type="entity", folder="entities")


def tool_wiki_upsert_concept(args: dict) -> dict:
    """Crea o aggiorna una pagina concept nel wiki."""
    return _wiki_upsert_page(args, page_type="concept", folder="concepts")


def tool_wiki_upsert_source(args: dict) -> dict:
    """Crea o aggiorna una pagina source. Accetta source_path, subtype,
    git_sha, analyzed_at come campi frontmatter extra (per codebase-snapshot)."""
    return _wiki_upsert_page(args, page_type="source", folder="sources")


def tool_wiki_upsert_analysis(args: dict) -> dict:
    """Crea o aggiorna una pagina analysis. Accetta question, transient come
    campi frontmatter extra (transient=true per lint report cancellabili)."""
    return _wiki_upsert_page(args, page_type="analysis", folder="analysis")


@persistence_errors
def tool_wiki_update_overview(args: dict) -> dict:
    """Update `wiki/overview.md` con merge sezioni replace-by-name.

    Crea il file se mancante (frontmatter type=overview + heading `# Overview`).
    Mai aggressivo: aggiorna solo le sezioni passate, lascia intatte le altre.

    args:
      sections: dict {section_name: markdown_content} — sezioni da scrivere
      title: opt, default "Overview"
    """
    from collections import OrderedDict

    sections_in = args.get("sections") or {}
    if not isinstance(sections_in, dict) or not sections_in:
        return {"error": "sections must be a non-empty dict {section_name: content}"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    overview_file = _confined_path(wiki, wiki / "overview.md")
    today = _today_iso()
    title_in = (args.get("title") or "Overview").strip()

    text = read_text(overview_file)
    expected = check_revision(overview_file, text, args.get("expected_revision"))
    if text is not None:
        fm, body = _parse_frontmatter(text)
        sections = _parse_sections(body)
        for sec_name, sec_content in sections_in.items():
            sections[sec_name] = (sec_content or "").strip()
        fm["updated"] = today
        fm["type"] = "overview"
        if title_in and "title" not in fm:
            fm["title"] = title_in
        action = "updated"
    else:
        fm = {
            "title": title_in,
            "type": "overview",
            "created": today,
            "updated": today,
        }
        sections = OrderedDict()
        sections[""] = f"# {title_in}"
        for sec_name, sec_content in sections_in.items():
            sections[sec_name] = (sec_content or "").strip()
        action = "created"

    new_text = _compose_frontmatter(fm) + "\n" + _compose_sections(sections)
    new_revision = write_text(overview_file, new_text, expected)
    _trigger_wiki_embed_bg(overview_file)

    return {
        "path": str(overview_file.relative_to(ROOT)),
        "action": action,
        "sections_modified": list(sections_in.keys()),
        "revision": new_revision,
    }


@persistence_errors
def tool_wiki_index_update(args: dict) -> dict:
    """Update `wiki/index.md`: per una `category` (heading di livello 2) fa
    append o replace della lista entries (markdown bullets).

    Crea l'index se mancante. Crea la category se non esiste. Mode 'append'
    dedupa per riga esatta (no duplicati). Mode 'replace' sostituisce l'intera
    sezione.

    args:
      category: str — nome sezione (es. 'Sources', 'Entities', 'Concepts', 'Analysis')
      entries: list[str] — righe markdown da appendere (es. '- [[auth-service]] — servizio JWT')
      mode: 'append' (default) | 'replace'
    """
    from collections import OrderedDict

    category = (args.get("category") or "").strip()
    if not category:
        return {"error": "category required"}
    entries = args.get("entries") or []
    if not isinstance(entries, list) or not entries:
        return {"error": "entries must be a non-empty list of strings"}
    mode = (args.get("mode") or "append").strip().lower()
    if mode not in ("append", "replace"):
        return {"error": "mode must be 'append' or 'replace'"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    index_file = _confined_path(wiki, wiki / "index.md")
    today = _today_iso()

    text = read_text(index_file)
    expected = check_revision(index_file, text, args.get("expected_revision"))
    if text is not None:
        fm, body = _parse_frontmatter(text)
        sections = _parse_sections(body)
        fm["updated"] = today
        if "type" not in fm:
            fm["type"] = "index"
        if "title" not in fm:
            fm["title"] = "Index"
    else:
        fm = {"title": "Index", "type": "index", "created": today, "updated": today}
        sections = OrderedDict()
        sections[""] = "# Index"

    if mode == "replace" or category not in sections:
        sections[category] = "\n".join(entries).strip()
        added = entries
    else:
        existing = sections[category]
        existing_lines = [ln for ln in existing.split("\n") if ln.strip()]
        added = []
        for e in entries:
            if e not in existing_lines:
                existing_lines.append(e)
                added.append(e)
        sections[category] = "\n".join(existing_lines).strip()

    new_text = _compose_frontmatter(fm) + "\n" + _compose_sections(sections)
    new_revision = write_text(index_file, new_text, expected)
    _trigger_wiki_embed_bg(index_file)

    return {
        "path": str(index_file.relative_to(ROOT)),
        "revision": new_revision,
        "category": category,
        "mode": mode,
        "entries_added": added,
        "entries_total": len(sections[category].split("\n")) if sections.get(category) else 0,
    }


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "wiki.search",
        "group": "wiki",
        "description": (
            "📚 Cerca nelle pagine del wiki di questo scope (project/hub/workspace). "
            "IBRIDA di default: fonde keyword + ricerca semantica via Reciprocal Rank "
            "Fusion (trova sia match esatti di parole sia affinità concettuale). "
            "Degrada a solo keyword se manca l'embed index. Ritorna metadati strutturati "
            "(slug, type, path, rrf_score, channels, preview). "
            "USE FOR: 'cerca le entità che parlano di X', 'che concetti abbiamo su Y', 'fonti su Z'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query (parole chiave o frase concettuale)"},
                "type": {"type": "string", "enum": ["all", "entity", "concept", "source", "analysis", "session", "overview", "index"], "description": "Filtra per tipo pagina (default 'all')"},
                "limit": {"type": "integer", "description": "Max risultati (default 10)"},
                "mode": {"type": "string", "enum": ["hybrid", "keyword", "vector"], "description": "Canale di ricerca (default 'hybrid')"},
                "include_sessions": {"type": "boolean", "description": "Includi le pagine session nei risultati (default false)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wiki.find_duplicates",
        "group": "graph",
        "description": (
            "🔎 Trova coppie di pagine wiki semanticamente troppo simili (candidati duplicati / "
            "da fondere o contraddittorie) via embeddings condivisi. Vede ciò che il match esatto "
            "non vede (es. 'auth-service' vs 'authentication'). USE per cleanup wiki / pre-ingest dedup."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "description": "Similarity coseno minima (default 0.85)"},
                "types": {"type": "array", "items": {"type": "string"}, "description": "page_type da considerare (default [entity, concept])"},
                "limit": {"type": "integer", "description": "Max coppie (default 20)"},
            },
        },
    },
    {
        "name": "wiki.read",
        "group": "wiki",
        "description": (
            "📚 Legge una pagina wiki per slug. Usa DOPO wiki.search per leggere il contenuto pieno. "
            "Cap a 10k chars (~2500 token) di default; passa max_chars per override."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug della pagina (es. 'auth-service'), .md opzionale"},
                "max_chars": {"type": "integer", "description": "Default 10000"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "wiki.upsert_entity",
        "group": "wiki",
        "description": (
            "📝 WIKI write: crea o aggiorna una entity page (modulo, servizio, persona, "
            "prodotto, sistema esterno) in wiki/entities/<slug>.md. Se la pagina esiste, "
            "fa MERGE delle sezioni passate (sostituisce solo quelle fornite, lascia "
            "intatte le altre, bump `updated`). Se non esiste, la crea con frontmatter "
            "completo. Usa per persistere decisioni strutturate su entità del progetto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Kebab-case (es. 'auth-service')"},
                "title": {"type": "string", "description": "Titolo leggibile (opt, default da slug humanized)"},
                "sections": {
                    "type": "object",
                    "description": "Dict {section_name: markdown_content}. Es. {'Sintesi': '...', 'Apparizioni': '- [[source-x]]: ...'}",
                    "additionalProperties": {"type": "string"},
                },
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Source slug da aggiungere a `sources` frontmatter (dedupe)"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "enum": ["draft", "stable", "deprecated"], "description": "Lifecycle (schema 1.1). Assente = stable"},
                "stale_after": {"type": "string", "description": "Data ISO YYYY-MM-DD oltre cui il contenuto va ri-verificato (schema 1.1). Usa per pagine volatili"},
            },
            "required": ["slug", "sections"],
        },
    },
    {
        "name": "wiki.upsert_concept",
        "group": "wiki",
        "description": (
            "📝 WIKI write: crea o aggiorna una concept page (pattern, idea, architettura, "
            "convenzione) in wiki/concepts/<slug>.md. Stesso MERGE-pattern di "
            "wiki.upsert_entity. Usa per pattern architetturali, convenzioni di progetto, "
            "idee astratte ricorrenti."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "sections": {"type": "object", "additionalProperties": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "enum": ["draft", "stable", "deprecated"], "description": "Lifecycle (schema 1.1). Assente = stable"},
                "stale_after": {"type": "string", "description": "Data ISO YYYY-MM-DD oltre cui il contenuto va ri-verificato (schema 1.1). Usa per pagine volatili"},
            },
            "required": ["slug", "sections"],
        },
    },
    {
        "name": "wiki.upsert_source",
        "group": "wiki",
        "description": (
            "📝 WIKI write: crea o aggiorna una source page in wiki/sources/<slug>.md "
            "(riassunto di una fonte ingerita: articolo, paper, doc, codebase-snapshot). "
            "Stesso MERGE-pattern di upsert_entity. Campi frontmatter extra opt-in: "
            "`source_path` (path al file in raw/), `subtype` (es. 'codebase-snapshot'), "
            "`git_sha`, `analyzed_at`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Es. '2026-04-26-karpathy-llm-wiki' o 'codebase-snapshot-2026-04-26'"},
                "title": {"type": "string"},
                "sections": {"type": "object", "additionalProperties": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_path": {"type": "string", "description": "Path al file originale in raw/ (es. '../../raw/articoli/x.md')"},
                "subtype": {"type": "string", "description": "Sottotipo (es. 'codebase-snapshot')"},
                "git_sha": {"type": "string", "description": "Solo per codebase-snapshot"},
                "analyzed_at": {"type": "string", "description": "ISO timestamp analisi (solo codebase-snapshot)"},
                "status": {"type": "string", "enum": ["draft", "stable", "deprecated"], "description": "Lifecycle (schema 1.1). Assente = stable"},
                "stale_after": {"type": "string", "description": "Data ISO YYYY-MM-DD oltre cui il contenuto va ri-verificato (schema 1.1). Usa per pagine volatili"},
            },
            "required": ["slug", "sections"],
        },
    },
    {
        "name": "wiki.upsert_analysis",
        "group": "wiki",
        "description": (
            "📝 WIKI write: crea o aggiorna una analysis page in wiki/analysis/<slug>.md "
            "(query trasformata in pagina, confronti, lint report). Stesso MERGE-pattern. "
            "Campi frontmatter extra: `question` (la query originale), `transient` "
            "(true per report cancellabili tipo lint)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "sections": {"type": "object", "additionalProperties": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Pagine wiki usate per sintetizzare"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "question": {"type": "string", "description": "La domanda originale che ha generato l'analisi"},
                "transient": {"type": "boolean", "description": "True se cancellabile (es. lint report)"},
                "status": {"type": "string", "enum": ["draft", "stable", "deprecated"], "description": "Lifecycle (schema 1.1). Assente = stable"},
                "stale_after": {"type": "string", "description": "Data ISO YYYY-MM-DD oltre cui il contenuto va ri-verificato (schema 1.1). Usa per pagine volatili"},
            },
            "required": ["slug", "sections"],
        },
    },
    {
        "name": "wiki.update_overview",
        "group": "wiki",
        "description": (
            "📝 WIKI write: aggiorna `wiki/overview.md` (sintesi di alto livello — 'cosa "
            "abbiamo capito'). Stesso MERGE-pattern: replace per sezione, lascia intatte "
            "le altre. Crea il file se manca. Usa SOLO quando la tesi corrente cambia in "
            "modo significativo (anti-pattern: aggiornarlo per ogni piccola cosa)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sections": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Dict {section_name: markdown_content}"},
                "title": {"type": "string", "description": "Default 'Overview'"},
            },
            "required": ["sections"],
        },
    },
    {
        "name": "wiki.index_update",
        "group": "wiki",
        "description": (
            "📝 WIKI write: manutenzione di `wiki/index.md`. Per una `category` (heading "
            "di livello 2, es. 'Sources', 'Entities', 'Concepts', 'Analysis') fa append "
            "(default, dedupe per riga esatta) o replace della lista entries. Crea index "
            "+ category se mancanti."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Nome sezione (es. 'Sources', 'Entities')"},
                "entries": {"type": "array", "items": {"type": "string"}, "description": "Righe markdown bullets (es. '- [[auth-service]] — servizio JWT')"},
                "mode": {"type": "string", "enum": ["append", "replace"], "default": "append"},
            },
            "required": ["category", "entries"],
        },
    },
]


for _spec in TOOLS:
    if _spec["name"] in {"wiki.upsert_entity", "wiki.upsert_concept", "wiki.upsert_source", "wiki.upsert_analysis", "wiki.update_overview", "wiki.index_update"}:
        _spec["inputSchema"]["properties"]["expected_revision"] = {
            "type": "string", "description": "Revisione da wiki.read (missing per creare); stale restituisce revision_conflict."
        }
