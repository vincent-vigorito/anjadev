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
    limit = args.get("limit", 10)
    lang = args.get("lang")
    return cs.code_search(query=query, smart_level=smart_level, limit=limit, lang=lang, root=ROOT,
                          max_preview_chars=args.get("max_preview_chars", 12000))




def tool_code_compare_decision(args: dict) -> dict:
    from decision_compare import compare_decision
    return compare_decision(ROOT, args.get('decision_path'), args.get('base_commit'), args.get('paths'),
                            expected_decision_revision=args.get('expected_decision_revision'),
                            max_chars=args.get('max_chars', 12000))


def tool_code_inspect(args: dict) -> dict:
    from code_inspect import inspect_source
    return inspect_source(ROOT, args.get('path'), symbol=args.get('symbol'),
                          expected_revision=args.get('expected_revision'),
                          max_chars=args.get('max_chars', 6000), max_items=args.get('max_items', 100))


def tool_code_reindex(args: dict) -> dict:
    """Wrapper MCP per code_index.index(). Build/refresh vector index."""
    ci = _code_index_module()
    if ci is None:
        return {"error": "code_index module not available"}
    force = bool(args.get("force", False))
    limit = args.get("limit")
    if limit is not None:
        limit = int(limit)
    return ci.index(target=ROOT, force=force, limit=limit, verbose=False, dry_run=bool(args.get("dry_run", False)))


def tool_code_status(args: dict) -> dict:
    """Stato dell'index: chunks totali, by-lang, provider, last_indexed_sha, size."""
    try:
        import sys as _sys
        if str(SCRIPTS_DIR) not in _sys.path:
            _sys.path.insert(0, str(SCRIPTS_DIR))
        from project_diagnostics import diagnose
        return diagnose(ROOT)
    except Exception as exc:
        return {"error": str(exc), "status": "failed", "code": "diagnostics_failed"}


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "code.compare_decision", "group": "code",
        "description": (
            "Compare a Markdown decision and selected current source files against an explicit full Git commit ID. "
            "Returns bounded textual diffs, source SHA256 and baseline blob IDs. Missing history is explicit; "
            "does not infer decision fulfillment, deletion or rename. Read-only, no embedding or source execution."
        ),
        "inputSchema": {"type": "object", "properties": {
            "decision_path": {"type": "string", "description": "Project-relative Markdown decision, including admitted wiki pages"},
            "base_commit": {"type": "string", "description": "Full 40/64-character Git commit ID selected as baseline"},
            "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
            "expected_decision_revision": {"type": "string", "description": "Optional SHA256 of current decision bytes"},
            "max_chars": {"type": "integer", "default": 12000, "minimum": 0, "maximum": 100000}
        }, "required": ["decision_path", "base_commit", "paths"]}
    },
    {
        "name": "code.inspect", "group": "code",
        "description": (
            "Inspect current Python source or explicit Markdown implementation declarations after code.search. "
            "Returns source, SHA256 revision, symbol/operation spans and typed import/declaration relations. "
            "Use expected_revision from search to reject changed sources. Assertions are syntax, not executed tests; "
            "calls/imports are not a resolved call graph. Behavioral claims remain not_assessed. No embedding or execution."
        ),
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Project-relative source path admitted by code policy"},
            "symbol": {"type": "string", "description": "Optional exact Python qualified name, e.g. Client.send"},
            "expected_revision": {"type": "string", "description": "Expected source SHA256 from code.search"},
            "max_chars": {"type": "integer", "default": 6000, "minimum": 0, "maximum": 100000},
            "max_items": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500}
        }, "required": ["path"]}
    },
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
            "Graceful fallback se livello superiore non disponibile. Riferimenti verificati con SHA256 e righe; "
            "evidence.abstain segnala assenza di riscontro letterale, non una soglia semantica calibrata. "
            "I candidati restano consultabili: una corrispondenza letterale non prova comportamento o copertura test. "
            "Per esaminare il comportamento usa code.inspect con path e expected_revision del risultato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query keyword o semantica"},
                "smart_level": {"type": "integer", "enum": [0, 1, 2], "description": "Override default auto-detect"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                "max_preview_chars": {"type": "integer", "default": 12000, "minimum": 0, "maximum": 100000,
                                      "description": "Budget totale caratteri delle anteprime; metadati esclusi"},
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
            "`.anjawiki/code-index.db`. Incremental di default (hash del working tree), "
            "force=true per rebuild transazionale. Usa il provider configurato "
            "via ANJA_EMBED_PROVIDER (default openrouter)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "default": False, "description": "Anteprima locale senza inviare contenuti o modificare l’indice"},
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

