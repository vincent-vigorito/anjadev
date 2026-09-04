"""Gruppo `user`: profilo utente HOT/DETAIL."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import ROOT, SCOPE, log_exc


def _resolve_default_user_slug(hub: Path) -> Optional[str]:
    """Read default_user da <hub>/config.json."""
    cfg = hub / "config.json"
    if not cfg.is_file():
        return None
    try:
        return json.loads(cfg.read_text(encoding="utf-8")).get("default_user")
    except Exception as _exc:
        log_exc("user._resolve_default_user_slug", _exc)
        return None


def _resolve_user_files(slug: Optional[str], detail: bool = False) -> tuple[Optional[Path], Optional[Path]]:
    """Risolvi (hub, file_path) per user HOT (default) o DETAIL."""
    hub = _hub_root_from_scope()
    if not hub:
        return None, None
    if not slug:
        slug = _resolve_default_user_slug(hub)
        if not slug:
            return hub, None
    suffix = "-detail" if detail else ""
    return hub, hub / "users" / f"{slug}{suffix}.md"


# User dir globale (cross-progetto) del plugin standalone: ~/.anja/{user,user-detail}.md.
# `~/.anjahub` è il path pre-split: letto ancora come alias (deprecato, warning una volta).
_USER_GLOBAL_DIR = Path.home() / ".anja"


_USER_GLOBAL_DIR_LEGACY = Path.home() / ".anjahub"


_LEGACY_USER_WARNED = False


def _resolve_user_global_fallback(detail: bool) -> Optional[Path]:
    """User file globale se esiste, None altrimenti (singleton, no multi-user):
      ~/.anja/user.md          → HOT
      ~/.anja/user-detail.md   → DETAIL
    Alias legacy ~/.anjahub/… (pre-split): ancora letto, con warning su stderr."""
    global _LEGACY_USER_WARNED
    name = "user-detail.md" if detail else "user.md"
    fp = _USER_GLOBAL_DIR / name
    if fp.is_file():
        return fp
    legacy = _USER_GLOBAL_DIR_LEGACY / name
    if legacy.is_file():
        if not _LEGACY_USER_WARNED:
            print(f"[anja_memory] DEPRECATED: {legacy} → sposta il profilo in {_USER_GLOBAL_DIR}/ "
                  f"(~/.anjahub resta letto come alias per un ciclo)", file=sys.stderr, flush=True)
            _LEGACY_USER_WARNED = True
        return legacy
    return None


def tool_user_read(args: dict) -> dict:
    """Read user profile HOT (default) o DETAIL.

    args:
      slug:   str = default_user dal hub config.json
      detail: bool = False  → True per leggere USER-detail.md

    Fallback in scope=project senza slug: legge ~/.anja/user.md se presente
    (profilo globale del plugin standalone; ~/.anjahub legacy come alias).
    """
    slug = args.get("slug")
    detail = bool(args.get("detail", False))

    if SCOPE == "project" and not slug:
        gfp = _resolve_user_global_fallback(detail)
        if gfp:
            return {
                "path": str(gfp),
                "slug": "global",
                "kind": "detail" if detail else "hot",
                "content": gfp.read_text(encoding="utf-8"),
                "source": "hub-global",
            }

    hub, fp = _resolve_user_files(slug, detail=detail)
    if not hub:
        return {"error": "hub root not determinable", "hint": "create ~/.anja/user.md (global profile) or set ANJA_HUB env"}
    if not fp or not fp.is_file():
        return {"error": f"user profile not found: {fp}", "hint": "create with users_init.py or ~/.anja/user.md for global"}
    return {
        "path": str(fp.relative_to(hub)),
        "slug": slug or _resolve_default_user_slug(hub),
        "kind": "detail" if detail else "hot",
        "content": fp.read_text(encoding="utf-8"),
    }


def tool_user_update(args: dict) -> dict:
    """Append/replace a section nel user profile (HOT default, DETAIL se detail=true).

    args:
      section: str  — heading-level-2 (es. "Gusti e preferenze"), creato se mancante
      content: str  — markdown da inserire
      mode:    'append' (default) | 'replace'
      detail:  bool  — True per scrivere su USER-detail.md (consigliato per dettagli)
      slug:    str   — override default_user
    """
    section = (args.get("section") or "").strip()
    content = (args.get("content") or "").strip()
    mode = (args.get("mode") or "append").lower()
    detail = bool(args.get("detail", False))
    slug = args.get("slug")

    if not section:
        return {"error": "section required"}
    if not content:
        return {"error": "content required"}
    if mode not in ("append", "replace"):
        return {"error": f"invalid mode '{mode}'. Use 'append' or 'replace'."}

    # Fallback hub-global in scope=project senza slug esplicito
    hub = None
    fp = None
    if SCOPE == "project" and not slug:
        gfp = _resolve_user_global_fallback(detail)
        if gfp:
            hub, fp = gfp.parent, gfp

    if fp is None:
        hub, fp = _resolve_user_files(slug, detail=detail)
        if not hub:
            return {"error": "hub root not determinable", "hint": "create ~/.anja/user.md (global profile) or set ANJA_HUB env"}
        if not fp:
            return {"error": "no default_user set in hub config.json. Run users_init.py first."}
        if not fp.is_file():
            return {"error": f"user file not found: {fp}", "hint": "create with users_init.py or ~/.anja/user.md for global"}

    today = datetime.now().strftime("%Y-%m-%d")
    text = fp.read_text(encoding="utf-8")
    lines = text.split("\n")
    section_header = f"## {section}"

    # Trova sezione (case-insensitive su nome dopo ##)
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip().lower() == section_header.lower():
            idx = i
            break

    if idx is None:
        # Append nuova sezione a fine file
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(section_header)
        lines.append("")
        lines.append(content)
        lines.append("")
    else:
        # Trova fine sezione (prossimo "## " heading o EOF)
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        if mode == "replace":
            # Rimuovi tutto tra header e end (escluso header)
            lines = lines[:idx + 1] + ["", content, ""] + lines[end:]
        else:
            # Append: trova ultima riga non vuota prima di end e inserisci dopo
            last_content = idx + 1
            for j in range(idx + 1, end):
                if lines[j].strip():
                    last_content = j + 1
            insertion = [f"- [{today}] {content}"] if "\n" not in content else [content]
            lines = lines[:last_content] + insertion + lines[last_content:]

    fp.write_text("\n".join(lines), encoding="utf-8")
    # Update frontmatter `updated:`
    text2 = fp.read_text(encoding="utf-8")
    text2 = re.sub(r"^updated:\s*.*$", f"updated: {today}", text2, count=1, flags=re.M)
    fp.write_text(text2, encoding="utf-8")

    return {
        "path": str(fp.relative_to(hub)),
        "section": section,
        "mode": mode,
        "kind": "detail" if detail else "hot",
        "status": "updated",
    }


def _hub_root_from_scope() -> Optional[Path]:
    """Risale alla hub root partendo da ANJA_ROOT.

    - SCOPE=hub:    ROOT è già il hub
    - SCOPE=agent:  ROOT è <hub>/agents/<name>/, quindi parent.parent = hub
    - SCOPE=project: ROOT è la project root, hub non determinabile direttamente
                     → prova ANJA_HUB env, altrimenti None
    """
    if SCOPE == "hub":
        return ROOT
    if SCOPE == "agent":
        # Resolve agent dir → parent (agents/) → parent (hub)
        if ROOT.parent.name == "agents":
            return ROOT.parent.parent
    # project: try env or fallback
    env_hub = os.environ.get("ANJA_HUB")
    if env_hub:
        return Path(env_hub).expanduser().resolve()
    return None


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "user.read",
        "group": "user",
        "description": "Read profilo utente — HOT (default, ~500 token, sempre sapevi questo già) o DETAIL on-demand. Usa DETAIL quando l'utente menziona qualcosa di personale che potrebbe essere registrato (gusti, hobby, persone, episodi). Se torni vuoto: profilo non esiste ancora.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Override del default_user (es. 'vincent'). Se omesso usa hub config.json default_user."},
                "detail": {"type": "boolean", "default": False, "description": "True per leggere USER-detail.md (gusti, hobby, persone, episodi); default False legge USER.md HOT."},
            },
        },
    },
    {
        "name": "user.update",
        "group": "user",
        "description": "Aggiorna profilo utente: append (default) o replace di una sezione. Per fatti permanenti core (ruolo, lingua, contesto operativo) usa detail=False. Per gusti/hobby/persone/episodi/preferenze granulari usa detail=true. Esempi: 'mi piace il jazz' → section='Gusti e preferenze', detail=true, mode='append'. 'cambio lingua a inglese' → section='Preferenze di comunicazione', detail=false, mode='replace'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Heading-level-2 (es. 'Gusti e preferenze', 'Persone importanti'). Creato se mancante."},
                "content": {"type": "string", "description": "Contenuto markdown da inserire."},
                "mode": {"type": "string", "enum": ["append", "replace"], "default": "append"},
                "detail": {"type": "boolean", "default": False, "description": "True per scrivere su USER-detail.md (gusti/hobby/persone); False per HOT (profilo core, raro)."},
                "slug": {"type": "string", "description": "Override default_user."},
            },
            "required": ["section", "content"],
        },
    },
]

