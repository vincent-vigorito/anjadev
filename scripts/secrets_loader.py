#!/usr/bin/env python3
"""secrets_loader.py — autoload `.secrets.env` dello scope nell'env del processo.

Pattern dotenv minimale (KEY=VALUE per riga, supporta quoted values + commenti #).
Priorità: env shell esistente prevale (`os.environ.setdefault`).

Riusato sia dal MCP server (boot) sia dagli script CLI standalone (wiki_embed,
graph_report, graph_html, code_index). Senza questo, gli script falliscono
con "no embed provider" anche se la chiave è in `.secrets.env`.

Locations cercate per scope:
  project: <root>/.anjawiki/.secrets.env  → fallback  <root>/.secrets.env
  hub:     <root>/.secrets.env
"""

from __future__ import annotations

import os
from pathlib import Path


def load_secrets(root: Path, scope: str = "project") -> int:
    """Carica `.secrets.env` per lo scope dato. Ritorna count variabili caricate.

    Idempotente: keys già in env vengono saltate (no override).
    """
    root = Path(root).resolve()
    candidates: list[Path] = []
    if scope == "project":
        candidates.append(root / ".anjawiki" / ".secrets.env")
        candidates.append(root / ".secrets.env")
    else:
        candidates.append(root / ".secrets.env")

    loaded = 0
    for path in candidates:
        if not path.is_file():
            continue
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                    v = v[1:-1]
                if k and k not in os.environ:
                    os.environ[k] = v
                    loaded += 1
        except Exception:
            continue
        if loaded > 0:
            break  # first non-empty file wins
    return loaded


def autoload_from_env() -> int:
    """Convenience: auto-detect scope/root via env e carica.

    Usato dagli script CLI dove SCOPE/ROOT sono in env (ereditati dal MCP server)
    OR dove `ANJA_ROOT` è settato esplicitamente sulla CLI.
    """
    scope = os.environ.get("ANJA_SCOPE", "project")
    root = Path(os.environ.get("ANJA_ROOT", os.getcwd()))
    return load_secrets(root, scope=scope)
