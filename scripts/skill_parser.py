#!/usr/bin/env python3
"""skill_parser.py — Hermes-aligned SKILL.md frontmatter parser (stdlib only).

Parse minimo per il plugin anjadev (no PyYAML dep). Supporta:
  - Scalar: `key: value` / `key: "quoted"` / `key: 'quoted'`
  - Inline list: `key: [a, b, c]` (JSON-style, parsato via json.loads)
  - Inline list di dict: `key: [{"k": "v"}, ...]` (JSON-style)
  - Block list: `key:\n  - item1\n  - item2` (scalar items only)

Limitazioni:
  - Dict nested in block format NON supportato (usa JSON inline).
  - Multiline string NON supportato (usa una sola riga).

Per casi avanzati (config:, required_env: con sub-key) usa formato JSON inline:
    config: [{"key": "foo.bar", "default": "baz", "prompt": "Enter foo"}]
"""

from __future__ import annotations

import json
import re
from pathlib import Path


_SCALAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_LIST_ITEM_RE = re.compile(r"^\s+-\s+(.*)$")


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _parse_inline_list(s: str):
    """YAML-style inline list: `[a, b, c]` → ['a', 'b', 'c'].
    Ritorna None se non è una list (e quindi non è il branch giusto)."""
    s = s.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return None
    inner = s[1:-1].strip()
    if not inner:
        return []
    items = [x.strip() for x in inner.split(",")]
    return [_coerce_scalar(x) for x in items if x]


def _coerce_scalar(s: str):
    """Convert string to int/float/bool/None or strip quotes."""
    s_stripped = s.strip()
    if not s_stripped:
        return ""
    low = s_stripped.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    try:
        if "." in s_stripped and not s_stripped.startswith("."):
            return float(s_stripped)
        return int(s_stripped)
    except ValueError:
        pass
    return _strip_quotes(s_stripped)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-ish frontmatter. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")

    meta: dict = {}
    lines = raw.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = _SCALAR_RE.match(line)
        if not m:
            i += 1
            continue
        key, value = m.group(1), m.group(2).strip()

        if value == "":
            # Block-list o block-dict: look ahead per `  - ` items
            items = []
            j = i + 1
            while j < len(lines):
                nl = lines[j]
                if not nl.strip():
                    j += 1
                    continue
                im = _LIST_ITEM_RE.match(nl)
                if im:
                    items.append(_coerce_scalar(im.group(1)))
                    j += 1
                    continue
                if nl.startswith(" ") or nl.startswith("\t"):
                    # indented non-list line: salta (nested dict non supportato)
                    j += 1
                    continue
                break
            meta[key] = items
            i = j
            continue

        # Inline list/dict: prima tenta JSON, poi YAML-style senza quote
        if value.startswith("[") or value.startswith("{"):
            try:
                meta[key] = json.loads(value)
                i += 1
                continue
            except json.JSONDecodeError:
                pass
            inline = _parse_inline_list(value)
            if inline is not None:
                meta[key] = inline
                i += 1
                continue
            meta[key] = _strip_quotes(value)
            i += 1
            continue

        meta[key] = _coerce_scalar(value)
        i += 1

    return meta, body


def parse_skill_md(skill_md_path: Path) -> dict:
    """Parse un SKILL.md → dict {name, description, version, ..., body}.

    Ritorna {} se il file non esiste o non ha frontmatter parsabile.
    """
    try:
        text = skill_md_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):
        return {}
    meta, body = parse_frontmatter(text)
    if not meta:
        return {}
    return {
        "name": str(meta.get("name") or skill_md_path.parent.name).strip(),
        "description": str(meta.get("description") or "").strip(),
        "version": str(meta.get("version") or ""),
        "category": str(meta.get("category") or ""),
        "tags": _ensure_list(meta.get("tags")),
        "platforms": _ensure_list(meta.get("platforms")),
        "requires_tools": _ensure_list(meta.get("requires_tools")),
        "fallback_for_tools": _ensure_list(meta.get("fallback_for_tools")),
        "config": _ensure_list(meta.get("config")),
        "required_env": _ensure_list(meta.get("required_env")),
        "body": body,
        "path": str(skill_md_path),
    }


def _ensure_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def dump_frontmatter(meta: dict) -> str:
    """Render frontmatter da dict. Inverso parziale di parse_frontmatter.

    Output formato Hermes-style:
        ---
        name: ...
        description: ...
        version: ...
        tags: [a, b, c]
        ---
    """
    lines = ["---"]
    # Ordine canonico
    canonical = ["name", "description", "version", "category", "tags",
                 "platforms", "requires_tools", "fallback_for_tools",
                 "config", "required_env"]
    seen = set()
    for k in canonical:
        if k in meta:
            lines.append(_render_kv(k, meta[k]))
            seen.add(k)
    for k, v in meta.items():
        if k not in seen:
            lines.append(_render_kv(k, v))
    lines.append("---")
    return "\n".join(lines)


def _render_kv(key: str, value) -> str:
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if value is None:
        return f"{key}: null"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        if all(isinstance(x, (str, int, float, bool)) and not isinstance(x, bool) for x in value):
            inner = ", ".join(json.dumps(x) if isinstance(x, str) else str(x) for x in value)
            return f"{key}: [{inner}]"
        return f"{key}: {json.dumps(value, ensure_ascii=False)}"
    if isinstance(value, dict):
        return f"{key}: {json.dumps(value, ensure_ascii=False)}"
    s = str(value)
    if any(c in s for c in (":", "#", "\"", "'", "[", "]", "{", "}")) or s != s.strip():
        return f"{key}: {json.dumps(s, ensure_ascii=False)}"
    return f"{key}: {s}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: skill_parser.py <SKILL.md path>")
        sys.exit(1)
    p = Path(sys.argv[1])
    parsed = parse_skill_md(p)
    print(json.dumps({k: v for k, v in parsed.items() if k != "body"}, indent=2, ensure_ascii=False))
    print(f"\n[body: {len(parsed.get('body', ''))} chars]")
