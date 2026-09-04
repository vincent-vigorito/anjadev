"""Gruppo `graph`: embedding wiki, k-NN cross-kind, report, visualizer HTML."""
from __future__ import annotations

from pathlib import Path

from .config import ROOT, SCRIPTS_DIR


def _wiki_embed_module():
    """Lazy import wiki_embed.py locale al plugin."""
    import importlib.util
    sp = SCRIPTS_DIR / "wiki_embed.py"
    spec = importlib.util.spec_from_file_location("wiki_embed", sp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tool_wiki_embed(args: dict) -> dict:
    """Embed incrementale delle pagine wiki del progetto corrente.

    args:
      force: bool = False        — re-embed tutto, ignora dirty check
      include_sessions: bool = False   (v0.22: i diari non sono conoscenza; prima True)
      single_page: str = ""      — path assoluto a una singola .md (più rapido)

    Ritorna stats: scanned, embedded, skipped_unchanged, deleted_orphans, errors, ms.
    """
    try:
        we = _wiki_embed_module()
    except Exception as e:
        return {"error": f"wiki_embed module unavailable: {e}"}

    single = (args.get("single_page") or "").strip()
    if single:
        return we.embed_single_page(ROOT, Path(single))

    return we.embed_wiki(
        ROOT,
        force=bool(args.get("force", False)),
        include_sessions=bool(args.get("include_sessions", False)),
    )


def tool_graph_report(args: dict) -> dict:
    """Compute knowledge graph report e scrivi GRAPH_REPORT.md nel wiki.

    args:
      top_god: int = 8                   — top-N god nodes
      surprise_threshold: float = 0.72   — similarity sopra → candidato surprise edge
      anchor_threshold: float = 0.6      — similarity wiki→code per "anchor"
      k_per_node: int = 5                — neighbors per pagina
      include_sessions: bool = False     — include wiki/sessions/ nel grafo
      write: bool = True                 — scrive GRAPH_REPORT.md (False=ritorna solo dict)
    """
    import importlib.util
    sp = SCRIPTS_DIR / "graph_report.py"
    spec = importlib.util.spec_from_file_location("graph_report", sp)
    gr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gr)

    report = gr.build_report(
        ROOT,
        top_n_god=int(args.get("top_god", 8)),
        surprise_threshold=float(args.get("surprise_threshold", 0.72)),
        anchor_threshold=float(args.get("anchor_threshold", 0.6)),
        k_per_node=int(args.get("k_per_node", 5)),
        include_sessions=bool(args.get("include_sessions", False)),
    )
    if "error" in report:
        return report

    if args.get("write", True):
        target = gr.write_report(ROOT, report)
        report["report_path"] = str(target.relative_to(ROOT))

    # Compact response: skip i field grossi se non richiesti
    if not args.get("verbose", False):
        report.pop("semantic_neighbors", None)
        report.pop("explicit_edges", None)
    return report


def tool_graph_search_text(args: dict) -> dict:
    """Search semantico cross-kind via query libera.

    Embedda la query con il provider corrente e fa k-NN nello spazio condiviso
    wiki + code. Filtri opzionali: kind (wiki|code|all), page_type per sotto-filtrare
    pagine wiki (entity|concept|source|analysis|session).

    args:
      query: str (required)
      filter: 'wiki' | 'code' | 'sessions' | 'all' = 'all'
      include_sessions: bool = False — con all/wiki includi i journal (v0.22: esclusi di default)
      k: int = 10
      min_score: float = 0.5
      page_type: str — filtro extra per pagine wiki (es. 'entity', 'session')
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query required"}

    filter_kind = (args.get("filter") or "all").strip()
    # v0.22: con filter 'all'/'wiki' i journal (type=session) restano FUORI salvo
    # include_sessions=true o filter='sessions' — i diari non sono conoscenza.
    include_sessions = bool(args.get("include_sessions", False))
    page_type = (args.get("page_type") or "").strip() or None
    k = int(args.get("k", 10))
    min_score = float(args.get("min_score", 0.5))

    try:
        import sys as _sys
        here = SCRIPTS_DIR
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db
        import embed_providers
    except ImportError as e:
        return {"error": f"module missing: {e}"}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embed provider configured (set ANJA_EMBED_PROVIDER + API key)"}

    anjawiki = ROOT / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return {"error": "index not built — run code.reindex and/or wiki.embed first"}

    # Embedda la query (1 call al provider)
    try:
        vecs = provider.embed([query])
    except Exception as e:
        return {"error": f"embedding failed: {e}"}
    if not vecs:
        return {"error": "empty embedding response"}
    query_vec = vecs[0]

    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        return {"error": f"db open failed: {e}"}

    try:
        # Filter → kind_filter + page_type post-filter
        if filter_kind == "sessions":
            kind_filter = "wiki"
            page_type_required = "session"
        elif filter_kind == "all":
            kind_filter = None
            page_type_required = page_type
        elif filter_kind in ("wiki", "code"):
            kind_filter = filter_kind
            page_type_required = page_type if filter_kind == "wiki" else None
        else:
            return {"error": f"invalid filter: {filter_kind}"}

        # Over-fetch per consentire post-filter su page_type
        raw = code_db.vector_search(
            db, query_vec=query_vec, limit=k * 3, kind_filter=kind_filter,
        )

        results = []
        for r in raw:
            if page_type_required and r["lang"] != page_type_required:
                continue
            if (filter_kind in ("all", "wiki") and not include_sessions
                    and r["kind"] == "wiki" and r["lang"] == "session"):
                continue
            score = 1.0 - float(r["distance"])
            if score < min_score:
                continue
            item = {
                "kind": r["kind"],
                "score": round(score, 4),
                "preview": (r["content"] or "")[:200].replace("\n", " ").strip(),
            }
            if r["kind"] == "wiki":
                item["slug"] = r["func_name"]
                item["page_type"] = r["lang"]
                item["file_path"] = r["file_path"]
            else:
                item["file_path"] = r["file_path"]
                item["func_name"] = r["func_name"]
                item["line_range"] = [r["line_start"], r["line_end"]]
                item["lang"] = r["lang"]
            results.append(item)
            if len(results) >= k:
                break

        return {
            "query": query,
            "filter": filter_kind,
            "page_type": page_type,
            "results": results,
            "count": len(results),
        }
    finally:
        db.close()


def tool_wiki_search_semantic(args: dict) -> dict:
    """Sugar wrapper: semantic search ristretto al wiki (escluse sessions di default).

    args identici a graph.search_text ma filter è forzato a 'wiki'.
    """
    forced = dict(args)
    forced["filter"] = "wiki"
    # Esclude sessions di default a meno che non si chieda esplicitamente
    if not forced.get("page_type") and not forced.get("include_sessions"):
        # Strategia: prima passata sopra-filter, poi escludi session in Python
        # Lo facciamo via _SOURCE_FILTER post-process per semplicità
        forced["_exclude_session_default"] = True
    result = tool_graph_search_text(forced)
    if "_exclude_session_default" in forced and "results" in result:
        result["results"] = [r for r in result["results"] if r.get("page_type") != "session"]
        result["count"] = len(result["results"])
    return result


def tool_sessions_search_semantic(args: dict) -> dict:
    """Sugar wrapper: semantic search ristretto a session journals.

    USE FOR: 'ricorda di cosa abbiamo parlato', 'session passata su X', 'quando
    ho discusso Y'. Richiede embed di sessions abilitato (default in wiki.embed).
    """
    forced = dict(args)
    forced["filter"] = "sessions"
    return tool_graph_search_text(forced)


def tool_graph_html(args: dict) -> dict:
    """Genera `<wiki>/graph.html` standalone Cytoscape visualizer.

    Single-file output (Cytoscape da CDN + dati embedded). Apri nel browser.
    Sidebar sx con search + filtri kind/type/edge, pannello dx dettagli su click.
    """
    import importlib.util
    sp = SCRIPTS_DIR / "graph_html.py"
    spec = importlib.util.spec_from_file_location("graph_html", sp)
    gh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gh)

    target = args.get("target")
    target_path = Path(target).expanduser() if target else None
    return gh.build_and_write_html(ROOT, target=target_path)


def tool_graph_semantic_neighbors(args: dict) -> dict:
    """k-NN cross-kind nello spazio embedding condiviso (wiki + code).

    args:
      source: str (required)     — slug pagina wiki (es. 'auth-service' o 'entities:auth-service')
                                   oppure file path codice (es. 'src/auth.py')
      kind: 'auto'|'wiki'|'code' = 'auto' — kind del source per lookup
      filter: 'all'|'wiki'|'code' = 'all' — quali neighbors ritornare
      k: int = 10
      min_score: float = 0.55    — cosine similarity threshold (0.0 = niente filter)

    Ritorna: {query: {...}, neighbors: [...], stats: {...}}
    Score = 1 - cosine_distance (1.0 = identico, 0.0 = ortogonale).
    """
    source = (args.get("source") or "").strip()
    if not source:
        return {"error": "source required"}
    kind = (args.get("kind") or "auto").strip()
    filter_kind = (args.get("filter") or "all").strip()
    k = int(args.get("k", 10))
    min_score = float(args.get("min_score", 0.55))

    try:
        import sys as _sys
        here = SCRIPTS_DIR
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_db
        import embed_providers
    except ImportError as e:
        return {"error": f"module missing: {e}"}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embed provider available (set ANJA_EMBED_PROVIDER + API key)"}

    anjawiki = ROOT / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return {"error": "index not built yet — run code.reindex and/or wiki.embed first"}

    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        return {"error": f"db open failed: {e}"}

    try:
        # 1. Lookup source — può essere slug (wiki, esatto o con prefisso) o file_path (code)
        candidates = []
        if kind in ("auto", "wiki"):
            # Wiki slug: try exact match first, then suffix match (entities:foo vs foo)
            row = db.execute(
                "SELECT id, file_path, func_name, kind FROM chunks "
                "WHERE kind = 'wiki' AND (func_name = ? OR func_name LIKE ?) LIMIT 1",
                (source, f"%:{source}"),
            ).fetchone()
            if row:
                candidates.append(dict(row))
        if kind in ("auto", "code") and not candidates:
            row = db.execute(
                "SELECT id, file_path, func_name, kind FROM chunks "
                "WHERE kind = 'code' AND file_path = ? LIMIT 1",
                (source,),
            ).fetchone()
            if row:
                candidates.append(dict(row))

        if not candidates:
            return {"error": f"source '{source}' not found in index (kind={kind})"}

        self_row = candidates[0]
        self_vec = code_db.get_embedding_vector(db, self_row["id"])
        if self_vec is None:
            return {"error": "embedding vector missing for source"}

        # 2. k-NN cross-kind
        kind_filter = None if filter_kind == "all" else filter_kind
        results = code_db.vector_search(
            db,
            query_vec=self_vec,
            limit=k + 1,  # +1 perché probabilmente self è in top
            kind_filter=kind_filter,
            exclude_id=self_row["id"],
        )

        # 3. Score = 1 - distance + filter min_score + preview
        neighbors = []
        for r in results:
            score = 1.0 - float(r["distance"])
            if score < min_score:
                continue
            edge_type = "semantic_strong" if score >= 0.8 else (
                "semantic_medium" if score >= 0.65 else "semantic_weak"
            )
            preview = (r["content"] or "")[:200].replace("\n", " ").strip()
            item = {
                "kind": r["kind"],
                "score": round(score, 4),
                "edge_type": edge_type,
                "preview": preview,
            }
            if r["kind"] == "wiki":
                item["slug"] = r["func_name"]
                item["page_type"] = r["lang"]
                item["file_path"] = r["file_path"]
            else:
                item["file_path"] = r["file_path"]
                item["func_name"] = r["func_name"]
                item["line_range"] = [r["line_start"], r["line_end"]]
                item["lang"] = r["lang"]
            neighbors.append(item)
            if len(neighbors) >= k:
                break

        return {
            "query": {
                "source": source,
                "kind": self_row["kind"],
                "resolved_path": self_row["file_path"],
                "self_id": self_row["id"],
            },
            "neighbors": neighbors,
            "stats": {
                "candidates_scanned": len(results),
                "above_threshold": len(neighbors),
                "min_score": min_score,
                "filter": filter_kind,
            },
        }
    finally:
        db.close()


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "wiki.embed",
        "group": "graph",
        "description": (
            "🔗 GRAPH: embed incrementale delle pagine wiki nello stesso spazio vettoriale "
            "del code-index → abilita k-NN cross-kind (wiki ↔ code) via "
            "graph.semantic_neighbors. Dirty detection via content hash: re-run è no-op "
            "se nulla cambia. Per re-embed singolo file usa `single_page`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False, "description": "Re-embed all, ignore dirty check"},
                "include_sessions": {"type": "boolean", "default": False, "description": "Include wiki/sessions/ (default false dal v0.22: i journal non sono conoscenza)"},
                "single_page": {"type": "string", "description": "Path assoluto a una singola .md (più rapido per refresh post-modifica)"},
            },
        },
    },
    {
        "name": "graph.report",
        "group": "graph",
        "description": (
            "🔗 GRAPH: compute knowledge graph report (god nodes + clusters + surprise edges + "
            "wiki↔code anchors + orphans). Scrive `wiki/GRAPH_REPORT.md` agent-readable. "
            "USE FOR: 'panoramica del wiki', 'cosa è centrale qui?', 'cosa va consolidato?', "
            "'mappa code→entity automatica'. **Read this report instead of scanning the whole wiki** "
            "quando ti serve orientamento su un progetto sconosciuto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_god": {"type": "integer", "default": 8, "description": "Top-N god nodes per degree centrality"},
                "surprise_threshold": {"type": "number", "default": 0.72},
                "anchor_threshold": {"type": "number", "default": 0.6},
                "k_per_node": {"type": "integer", "default": 5},
                "include_sessions": {"type": "boolean", "default": False},
                "write": {"type": "boolean", "default": True, "description": "Scrive GRAPH_REPORT.md"},
                "verbose": {"type": "boolean", "default": False, "description": "Include semantic_neighbors + explicit_edges nel response"},
            },
        },
    },
    {
        "name": "graph.search_text",
        "group": "graph",
        "description": (
            "🔗 GRAPH: semantic search cross-kind via query libera. Embedda la query "
            "nello stesso spazio del wiki+code → k-NN. USE FOR: 'trova pagine/file su X', "
            "domande senza nomi esatti, ricerca per intenzione. Filter: 'wiki' | 'code' | "
            "'sessions' | 'all'. Score = 1 - cosine_distance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query libera (lingua naturale)"},
                "filter": {"type": "string", "enum": ["wiki", "code", "sessions", "all"], "default": "all"},
                "include_sessions": {"type": "boolean", "default": False, "description": "Con filter all/wiki includi anche le pagine session (default false dal v0.22)"},
                "k": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.5},
                "page_type": {"type": "string", "description": "Filtro extra wiki: 'entity' | 'concept' | 'source' | 'analysis' | 'session'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wiki.search_semantic",
        "group": "graph",
        "description": (
            "📚 WIKI: semantic search del wiki (sessions escluse di default). "
            "Sugar di graph.search_text con filter='wiki'. USE FOR: 'pagine sul concetto X', "
            "'dove abbiamo discusso Y nel wiki'. Complementare a wiki.search (FTS testuale): "
            "wiki.search trova match esatti di parole, wiki.search_semantic trova affinità "
            "concettuale anche con vocabolario diverso."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.5},
                "page_type": {"type": "string", "description": "'entity' | 'concept' | 'source' | 'analysis'"},
                "include_sessions": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "sessions.search_semantic",
        "group": "graph",
        "description": (
            "🧠 SESSIONS: semantic search nelle session journal. USE FOR: 'ricorda di cosa "
            "abbiamo parlato', 'session passata su X', 'quando ho discusso Y'. Richiede che "
            "wiki.embed sia stato fatto con include_sessions=True (default)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "graph.html",
        "group": "graph",
        "description": (
            "🔗 GRAPH: genera `<wiki>/graph.html` standalone visualizer (Cytoscape). "
            "Single-file con dati embedded, sidebar search/filtri, click su nodo per dettagli. "
            "Apri nel browser, no server. Suggerisci all'utente di aprire il file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Output path override (default: <wiki>/graph.html)"},
            },
        },
    },
    {
        "name": "graph.semantic_neighbors",
        "group": "graph",
        "description": (
            "🔗 GRAPH: k-NN nello spazio embedding unificato wiki+code. "
            "Trova pagine wiki e/o file di codice semanticamente simili a una source data. "
            "USE FOR: 'pagine simili a [[X]]', 'quale codice descrive questa entity?', "
            "'questa entity ha file di codice mappabili?', 'duplicati semantici nel wiki', "
            "'surprise edges (no [[wikilink]] esplicito ma alta similarity)'. "
            "Score = 1 - cosine_distance (1.0 identico, 0.0 ortogonale)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "slug pagina wiki ('auth-service' o 'entities:auth-service') OR file path codice ('src/auth.py')"},
                "kind": {"type": "string", "enum": ["auto", "wiki", "code"], "default": "auto"},
                "filter": {"type": "string", "enum": ["all", "wiki", "code"], "default": "all"},
                "k": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.55, "description": "Cosine similarity threshold"},
            },
            "required": ["source"],
        },
    },
]

