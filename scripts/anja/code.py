"""Gruppo `code`: ricerca nel codebase (3 livelli) + index."""
from __future__ import annotations

from .config import ROOT, SCRIPTS_DIR, log_exc


def _code_search_module():
    try:
        import sys as _sys
        here = SCRIPTS_DIR
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_search  # noqa
        return code_search
    except Exception as _exc:
        log_exc("code._code_search_module", _exc)
        return None


def _code_index_module():
    try:
        import sys as _sys
        here = SCRIPTS_DIR
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import code_index  # noqa
        return code_index
    except Exception as _exc:
        log_exc("code._code_index_module", _exc)
        return None


def tool_code_search(args: dict) -> dict:
    """Wrapper MCP per code_search.code_search(). Vedi code_search.py per logica 3 livelli."""
    cs = _code_search_module()
    if cs is None:
        return {"error": "code_search module not available"}
    query = (args.get("query") or "").strip()
    smart_level = args.get("smart_level")
    limit = int(args.get("limit", 10))
    lang = args.get("lang")
    return cs.code_search(query=query, smart_level=smart_level, limit=limit, lang=lang)


def tool_code_reindex(args: dict) -> dict:
    """Wrapper MCP per code_index.index(). Build/refresh vector index."""
    ci = _code_index_module()
    if ci is None:
        return {"error": "code_index module not available"}
    force = bool(args.get("force", False))
    limit = args.get("limit")
    if limit is not None:
        limit = int(limit)
    return ci.index(target=ROOT, force=force, limit=limit, verbose=False)


def tool_code_status(args: dict) -> dict:
    """Stato dell'index: chunks totali, by-lang, provider, last_indexed_sha, size."""
    anjawiki = ROOT / ".anjawiki"
    db_path = anjawiki / "code-index.db"
    if not db_path.exists():
        return {
            "indexed": False,
            "hint": "Run code.reindex or /anja-index-code to build the vector index.",
        }
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

    try:
        db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)
    except Exception as e:
        return {"error": f"db open failed: {e}"}

    s = code_db.stats(db)
    s["indexed"] = True
    s["db_path"] = str(db_path.relative_to(ROOT))
    s["db_size_mb"] = round(db_path.stat().st_size / (1024 * 1024), 2)
    return s


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "code.search",
        "group": "code",
        "description": (
            "🔎 CODE.SEARCH: ricerca nel codebase del progetto ospitante. "
            "USE PRIMA di Grep/Glob quando la query è SEMANTICA/CONCETTUALE: "
            "'dove gestiamo l'autenticazione', 'logica di retry', 'qualcosa "
            "che fa X', 'il code che parla con il DB', 'trova pattern simili'. "
            "USE quando l'utente cerca 'il codice che fa X' senza conoscere "
            "nomi esatti, o per codebase >5k LOC dove Grep porterebbe troppi hit. "
            "SKIP (usa Grep) quando la query è un NOME ESATTO di funzione/"
            "variabile/classe (es. 'trova authenticate()', 'usi di FOO_CONST'). "
            "3 livelli: 0=ripgrep+smart ranking (filename/func boost + git "
            "recency), 1=ripgrep top-50 + LLM haiku rerank semantico, 2=vector "
            "via sqlite-vec + embed provider (richiede `code.reindex`). "
            "Auto-detect: index disponibile→2, <5k LOC→0, altrimenti→1. "
            "Graceful fallback se livello superiore non disponibile."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query keyword o semantica"},
                "smart_level": {"type": "integer", "enum": [0, 1, 2], "description": "Override default auto-detect"},
                "limit": {"type": "integer", "default": 10},
                "lang": {"type": "string", "description": "Filtra per linguaggio (es. 'python', 'typescript')"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "code.reindex",
        "group": "code",
        "description": (
            "🔎 CODE: build/refresh vector index per il codebase del progetto in "
            "`.anjawiki/code-index.db`. Incremental di default (git diff vs last_indexed_sha), "
            "force=true per full re-index (drop & rebuild). Usa il provider configurato "
            "via ANJA_EMBED_PROVIDER (default openrouter)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False, "description": "true=full rebuild, false=incremental"},
                "limit": {"type": "integer", "description": "Max file da processare (debug)"},
            },
        },
    },
    {
        "name": "code.status",
        "group": "code",
        "description": (
            "🔎 CODE: stato del vector index del codebase. Restituisce: chunks totali, "
            "by-lang, provider/model usato, last_indexed_sha, size DB su disco. "
            "Restituisce indexed=false con hint se l'index non esiste ancora."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

