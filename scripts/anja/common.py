"""Helper condivisi da più moduli tool (path del wiki, frontmatter, ...)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import ROOT, SCOPE


def _wiki_root() -> Path:
    """Ritorna la directory wiki in base allo scope.

    Hub/agent: gli hub post-hoist (init_hub recenti) hanno il wiki in
    `.anjawiki/wiki` come i project; i legacy in `<root>/wiki`. Probe del
    layout reale — il bug era assumere il legacy e fallire sugli hub nuovi."""
    if SCOPE == "project":
        return ROOT / ".anjawiki" / "wiki"
    hoisted = ROOT / ".anjawiki" / "wiki"
    return hoisted if hoisted.is_dir() else ROOT / "wiki"


def _raw_root() -> Path:
    if SCOPE == "project":
        return ROOT / ".anjawiki" / "raw"
    hoisted = ROOT / ".anjawiki" / "raw"
    return hoisted if hoisted.is_dir() else ROOT / "raw"


def _sessions_root() -> Path:
    """Path canonico delle session in base allo scope.

    - project: <root>/.anjawiki/wiki/sessions/
    - hub:     <root>/sessions/                   (NON sotto wiki/ — l'hub non ha wiki/)
    - agent:   <root>/sessions/                   (idem agent dir)
    """
    if SCOPE == "project":
        return ROOT / ".anjawiki" / "wiki" / "sessions"
    return ROOT / "sessions"


def _today_iso() -> str:
    return datetime.now().astimezone().date().isoformat()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter naive (no PyYAML dep). Restituisce (fm_dict, body).

    Supporta: scalar str, lista inline `[a, b, c]`. Niente nested o multi-line.
    Sufficiente per il nostro schema wiki.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm_raw = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fm: dict = {}
    for line in fm_raw.split("\n"):
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if "{" in inner:
                # flow-list di mappe (es. verified: [{ by, at }, ...]): lo split
                # sulle virgole la corromperebbe — opaca, round-trip verbatim
                fm[key] = val
            else:
                items = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
                fm[key] = items
        else:
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            fm[key] = val
    return fm, body


def _fm_kv(key: str, val) -> str:
    if isinstance(val, bool):
        return f"{key}: {'true' if val else 'false'}"
    if isinstance(val, list):
        if not val:
            return f"{key}: []"
        return f"{key}: [{', '.join(str(x) for x in val)}]"
    return f"{key}: {val}"


def _compose_frontmatter(fm: dict) -> str:
    """Serializza frontmatter dict in YAML. Preserva ordine canonico: title,
    type, subtype, created, updated, sources, tags, poi resto."""
    order = ["title", "type", "subtype", "created", "updated", "sources", "tags"]
    lines = ["---"]
    written = set()
    for key in order:
        if key in fm:
            lines.append(_fm_kv(key, fm[key]))
            written.add(key)
    for key in fm:
        if key not in written:
            lines.append(_fm_kv(key, fm[key]))
    lines.append("---")
    return "\n".join(lines) + "\n"


def _parse_sections(body: str):
    """Parse body markdown in OrderedDict {section_heading: content}.

    Sezioni = headings di livello 2 (`## Title`). Tutto prima del primo `##`
    finisce nella chiave '' (preambolo, tipicamente `# Title`).
    """
    from collections import OrderedDict
    sections: "OrderedDict[str, str]" = OrderedDict()
    current_key = ""
    current_lines: list = []
    for line in body.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            sections[current_key] = "\n".join(current_lines).strip("\n")
            current_key = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current_key] = "\n".join(current_lines).strip("\n")
    return sections


def _compose_sections(sections) -> str:
    """Inverse di _parse_sections: produce body markdown coerente."""
    parts = []
    for heading, content in sections.items():
        if heading == "":
            if content:
                parts.append(content)
        else:
            block = f"## {heading}\n\n{content}".rstrip()
            parts.append(block)
    return "\n\n".join(parts) + "\n"


def _iter_wiki_md(wiki: Path):
    """Iter su tutti i file .md sotto wiki/ (esclude . files)."""
    for f in wiki.rglob("*.md"):
        if f.is_file() and not f.name.startswith("."):
            yield f


def _slug_of(f: Path) -> str:
    return f.stem
