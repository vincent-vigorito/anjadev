"""Gruppo `wiki` (parte 3, I/O): attach_image, export, log_append."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .common import (
    _compose_frontmatter,
    _compose_sections,
    _iter_wiki_md,
    _parse_frontmatter,
    _parse_sections,
    _raw_root,
    _slug_of,
    _today_iso,
    _wiki_root,
)
from .config import ROOT, SCOPE, log_exc
from .wiki_maint import _WIKILINK_RE

_MAX_IMAGE_BYTES = 25 * 1024 * 1024  # cap download wiki.attach_image (anti file enorme da URL ostile)


_LOG_TYPE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def tool_wiki_log_append(args: dict) -> dict:
    """Append entry strict-format `## [YYYY-MM-DD] type | description` a wiki/log.md."""
    log_type = (args.get("type") or "").strip()
    if not log_type:
        return {"error": "type required"}
    if not _LOG_TYPE_RE.match(log_type):
        return {"error": f"type must match [a-z][a-z0-9-]*: '{log_type}'"}

    description = (args.get("description") or "").strip()
    if not description:
        return {"error": "description required"}
    description = description.replace("\n", " ").replace("\r", " ")[:200]

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    log_file = wiki / "log.md"
    today = _today_iso()
    entry = f"## [{today}] {log_type} | {description}"

    if log_file.is_file():
        existing = log_file.read_text(encoding="utf-8").rstrip() + "\n"
        new_content = existing + "\n" + entry + "\n"
    else:
        new_content = f"# Log\n\n{entry}\n"

    log_file.write_text(new_content, encoding="utf-8")
    return {
        "entry": entry,
        "path": str(log_file.relative_to(ROOT)),
        "type": log_type,
    }


def tool_wiki_attach_image(args: dict) -> dict:
    """Allega un'immagine a una pagina wiki (entity/concept/source/analysis).

    Workflow: copia l'immagine in raw/<topic>/ + append markdown link nella pagina
    (sezione 'Diagrammi' o 'Screenshots'). Update frontmatter.updated.

    args:
      slug:       str — slug pagina target (deve esistere)
      image_path: str — path locale immagine (o url http/https)
      topic:      opt str — sotto-cartella raw/ (default: slug stesso)
      alt_text:   opt str — alt text del markdown link (default: filename)
      section:    opt str — section dove appendere ('Diagrammi' default, 'Screenshots' alt)
    """
    import shutil as _shutil
    from urllib.request import urlopen

    slug = (args.get("slug") or "").strip()
    image_path_arg = (args.get("image_path") or "").strip()
    if not slug or not image_path_arg:
        return {"error": "slug and image_path required"}

    wiki = _wiki_root()
    raw = _raw_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    # Find target page in entities/concepts/sources/analysis
    target_file = None
    for folder in ("entities", "concepts", "sources", "analysis"):
        candidate = wiki / folder / f"{slug}.md"
        if candidate.is_file():
            target_file = candidate
            break
    if target_file is None:
        return {"error": f"page not found: {slug} (cerco in entities/concepts/sources/analysis)"}

    # Download or copy image
    # topic finisce in raw/<topic>: sanitizza per impedire '../' traversal, poi confina
    # (F-Sec-Anjadev-CategoryTopicTraversal).
    topic = re.sub(r"[^a-zA-Z0-9_-]", "", (args.get("topic") or slug).strip().strip("/")) or slug
    raw_topic_dir = raw / topic
    try:
        raw_topic_dir.resolve().relative_to(raw.resolve())
    except ValueError:
        return {"error": "invalid topic"}
    raw_topic_dir.mkdir(parents=True, exist_ok=True)

    if image_path_arg.startswith(("http://", "https://")):
        # Download — basename + cap size (no filename traversal, no file enorme da URL ostile)
        filename = os.path.basename(image_path_arg.rsplit("/", 1)[-1].split("?")[0].strip())
        if filename in ("", ".", ".."):
            filename = "image.png"
        dest = raw_topic_dir / filename
        try:
            with urlopen(image_path_arg, timeout=15) as resp:
                data = resp.read(_MAX_IMAGE_BYTES + 1)
            if len(data) > _MAX_IMAGE_BYTES:
                return {"error": f"image too large (> {_MAX_IMAGE_BYTES // (1024 * 1024)}MB)"}
            dest.write_bytes(data)
        except Exception as e:
            return {"error": f"download failed: {type(e).__name__}: {e}"}
    else:
        src = Path(image_path_arg).expanduser().resolve()
        if not src.is_file():
            return {"error": f"image file not found: {src}"}
        filename = src.name
        dest = raw_topic_dir / filename
        try:
            _shutil.copy2(src, dest)
        except Exception as e:
            return {"error": f"copy failed: {type(e).__name__}: {e}"}

    # Compute relative path da target_file a dest
    # target_file = wiki/entities/<slug>.md → ../../raw/<topic>/<filename>
    rel_path = f"../../raw/{topic}/{filename}"
    alt_text = (args.get("alt_text") or filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")).strip()
    section_name = (args.get("section") or "Diagrammi").strip()

    # Read page, parse sections, append immagine in section target
    text = target_file.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    sections = _parse_sections(body)

    image_md = f"![{alt_text}]({rel_path})"
    if section_name in sections:
        existing = sections[section_name].rstrip()
        sections[section_name] = (existing + "\n\n" + image_md) if existing else image_md
    else:
        sections[section_name] = image_md

    fm["updated"] = _today_iso()
    new_text = _compose_frontmatter(fm) + "\n" + _compose_sections(sections)
    target_file.write_text(new_text, encoding="utf-8")

    return {
        "slug": slug,
        "page_path": str(target_file.relative_to(ROOT)),
        "image_path": str(dest.relative_to(ROOT)),
        "section": section_name,
        "alt_text": alt_text,
        "markdown_inserted": image_md,
    }


def tool_wiki_export(args: dict) -> dict:
    """Esporta il wiki in formato md (zip), json (dump strutturato) o html (static render).

    args:
      format: 'md' | 'json' | 'html'
      output_path: opt, default = .anjawiki/exports/wiki-export-<date>.<ext>
      include_sessions: opt bool (default False) — include session files (volume alto)
    """
    import zipfile

    fmt = (args.get("format") or "json").lower()
    if fmt not in ("md", "json", "html"):
        return {"error": f"format must be one of md|json|html, got '{fmt}'"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    include_sessions = bool(args.get("include_sessions", False))
    today = _today_iso()
    default_dir = ROOT / ".anjawiki" / "exports" if SCOPE == "project" else ROOT / "exports"
    default_dir.mkdir(parents=True, exist_ok=True)
    out_path_arg = args.get("output_path")
    if out_path_arg:
        # Confina a ROOT: senza il check si esporta tutto il wiki (prompt utente, SOUL,
        # frontmatter) verso un path arbitrario (F-Sec-Anjadev-ExportTraversal).
        out_path = Path(out_path_arg).expanduser().resolve()
        try:
            out_path.relative_to(ROOT.resolve())
        except ValueError:
            return {"error": "output_path must be inside the project root"}
    else:
        ext = {"md": "zip", "json": "json", "html": "zip"}[fmt]
        out_path = default_dir / f"wiki-export-{today}.{ext}"

    pages = []
    for f in _iter_wiki_md(wiki):
        rel = f.relative_to(wiki)
        if not include_sessions and "sessions" in rel.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as _exc:
            log_exc("wiki_io.tool_wiki_export", _exc)
            continue
        fm, body = _parse_frontmatter(text)
        pages.append({
            "slug": _slug_of(f),
            "path": str(rel),
            "frontmatter": fm,
            "body": body,
            "raw": text,
        })

    if fmt == "json":
        payload = {
            "wiki_root": str(wiki),
            "exported_at": today,
            "schema_version": _read_schema_version(),
            "page_count": len(pages),
            "pages": [{k: v for k, v in p.items() if k != "raw"} for p in pages],
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return {"format": "json", "output_path": str(out_path), "page_count": len(pages), "size_bytes": out_path.stat().st_size}

    if fmt == "md":
        # Zip dei .md preservando struttura
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in pages:
                zf.writestr(p["path"], p["raw"])
        return {"format": "md", "output_path": str(out_path), "page_count": len(pages), "size_bytes": out_path.stat().st_size}

    # html: static render con wikilinks risolti
    slug_to_path = {p["slug"]: p["path"] for p in pages}
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in pages:
            html_body = _render_html_body(p["body"], slug_to_path)
            html = _render_html_page(p["frontmatter"].get("title", p["slug"]), html_body, p["frontmatter"])
            html_path = p["path"].replace(".md", ".html")
            zf.writestr(html_path, html)
        # Index page top-level (nome differenziato per evitare collisione con wiki/index.md)
        index_html = _render_html_index(pages)
        zf.writestr("_export_index.html", index_html)
    return {"format": "html", "output_path": str(out_path), "page_count": len(pages), "size_bytes": out_path.stat().st_size}


def _read_schema_version() -> str:
    """Legge .anjawiki/.schema-version se presente."""
    if SCOPE == "project":
        sv = ROOT / ".anjawiki" / ".schema-version"
        if sv.is_file():
            return sv.read_text(encoding="utf-8").strip()
    return "unknown"


def _render_html_body(body: str, slug_to_path: dict) -> str:
    """Render markdown→html basico: paragrafi + heading + wikilinks risolti.
    Niente markdown lib esterna, render minimale (no tables/code-block parser ricco)."""
    import html as _html
    out_lines = []
    in_pre = False
    for line in body.split("\n"):
        if line.startswith("```"):
            if in_pre:
                out_lines.append("</pre>")
                in_pre = False
            else:
                out_lines.append("<pre><code>")
                in_pre = True
            continue
        if in_pre:
            out_lines.append(_html.escape(line))
            continue
        # Heading
        m_h = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m_h:
            lvl = len(m_h.group(1))
            txt = _render_inline(m_h.group(2), slug_to_path)
            out_lines.append(f"<h{lvl}>{txt}</h{lvl}>")
            continue
        if not line.strip():
            out_lines.append("")
            continue
        out_lines.append(f"<p>{_render_inline(line, slug_to_path)}</p>")
    return "\n".join(out_lines)


_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _render_inline(text: str, slug_to_path: dict) -> str:
    """Resolve [[slug]], [[slug|label]], [![alt](url)], [text](url) inline."""
    import html as _html
    text_safe = _html.escape(text, quote=False)
    # Wikilinks: [[slug]] o [[slug|label]] o [[slug#anchor]]
    def _wl(m):
        target = m.group(1)
        anchor = m.group(2) or ""
        label = (m.group(3) or "").lstrip("|") or target
        if target in slug_to_path:
            href = slug_to_path[target].replace(".md", ".html")
            return f'<a href="/{href}{anchor}">{label}</a>'
        return f'<span class="broken-link" title="missing: {target}">{label}</span>'
    text_safe = _WIKILINK_RE.sub(_wl, text_safe)
    # Markdown inline links: [text](url)
    text_safe = _INLINE_LINK_RE.sub(r'<a href="\2">\1</a>', text_safe)
    # Inline code: `code`
    text_safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", text_safe)
    # Bold + italic markdown semplice
    text_safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text_safe)
    text_safe = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text_safe)
    return text_safe


def _render_html_page(title: str, body_html: str, fm: dict) -> str:
    """Wrapper HTML minimale, stile inline."""
    import html as _html
    css = """
    body { font-family: -apple-system, system-ui, sans-serif; max-width: 760px; margin: 2em auto; padding: 0 1em; line-height: 1.6; color: #222; }
    h1, h2, h3 { line-height: 1.25; }
    h1 { border-bottom: 2px solid #444; padding-bottom: 0.3em; }
    a { color: #0366d6; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .broken-link { color: #c00; text-decoration: line-through; }
    pre { background: #f6f8fa; padding: 1em; overflow-x: auto; border-radius: 4px; }
    code { background: #f6f8fa; padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.9em; }
    .frontmatter { background: #f6f8fa; border-left: 3px solid #0366d6; padding: 0.5em 1em; margin: 1em 0; font-size: 0.9em; color: #555; }
    """
    fm_block = ""
    if fm:
        items = "<br>".join(f"<strong>{_html.escape(str(k))}:</strong> {_html.escape(str(v))}" for k, v in fm.items() if k not in ("title",))
        fm_block = f'<div class="frontmatter">{items}</div>'
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_html.escape(title)}</title><style>{css}</style></head>
<body>{fm_block}{body_html}</body></html>"""


def _render_html_index(pages: list) -> str:
    """Index page con lista pages raggruppate per type."""
    from collections import defaultdict
    by_type = defaultdict(list)
    for p in pages:
        ptype = p["frontmatter"].get("type") or "page"
        by_type[ptype].append(p)
    css = """
    body { font-family: -apple-system, system-ui, sans-serif; max-width: 900px; margin: 2em auto; padding: 0 1em; line-height: 1.6; }
    h2 { border-bottom: 1px solid #ddd; padding-bottom: 0.3em; margin-top: 2em; text-transform: capitalize; }
    a { color: #0366d6; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .meta { color: #888; font-size: 0.9em; }
    """
    sections = []
    for ptype in sorted(by_type.keys()):
        items = by_type[ptype]
        items.sort(key=lambda p: p["slug"])
        rows = "\n".join(
            f'<li><a href="/{p["path"].replace(".md", ".html")}">{p["frontmatter"].get("title", p["slug"])}</a> <span class="meta">— {p["slug"]}</span></li>'
            for p in items
        )
        sections.append(f'<h2>{ptype} ({len(items)})</h2><ul>{rows}</ul>')
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Wiki index</title><style>{css}</style></head>
<body><h1>Wiki index</h1>{''.join(sections)}</body></html>"""


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "wiki.attach_image",
        "group": "wiki",
        "description": (
            "🖼️ WIKI write: allega immagine a pagina entity/concept/source/analysis. "
            "Copia/scarica l'immagine in raw/<topic>/ + append `![alt](rel-path)` "
            "nella sezione target (default 'Diagrammi'). Supporta path locale o URL "
            "http/https. Update frontmatter.updated. Pattern QoL per ingest visuali: "
            "diagrammi architetturali, screenshots UI, foto whiteboarding, ecc."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug pagina target (deve esistere)"},
                "image_path": {"type": "string", "description": "Path locale o URL http/https"},
                "topic": {"type": "string", "description": "Sotto-cartella raw/ (default: slug stesso)"},
                "alt_text": {"type": "string", "description": "Alt text markdown (default: filename)"},
                "section": {"type": "string", "default": "Diagrammi", "description": "Sezione dove appendere (es. 'Diagrammi', 'Screenshots')"},
            },
            "required": ["slug", "image_path"],
        },
    },
    {
        "name": "wiki.export",
        "group": "wiki",
        "description": (
            "📦 WIKI export: dump dell'intero wiki in formato md (zip), json "
            "(dump strutturato per import/training/tool esterni), o html "
            "(static site con wikilinks risolti, browsable offline). Output "
            "default in .anjawiki/exports/wiki-export-<date>.<ext>. Sessions "
            "escluse di default (alto volume), abilita con include_sessions=true. "
            "Usa per backup atomico, sharing wiki snapshot, generazione static site."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["md", "json", "html"], "default": "json"},
                "output_path": {"type": "string", "description": "Path file output (opt). Default in .anjawiki/exports/"},
                "include_sessions": {"type": "boolean", "default": False, "description": "Include session files (alto volume)"},
            },
        },
    },
    {
        "name": "wiki.log_append",
        "group": "wiki",
        "description": (
            "📝 WIKI write: append entry strict-format a wiki/log.md (memoria episodica). "
            "Format auto: `## [YYYY-MM-DD] type | description`. Tipi convenzionali: "
            "init, init-analyze, ingest, query, refresh, lint, session, decision, "
            "milestone, note (free-form ma kebab-case enforced). Usa quando un evento "
            "merita tracciamento permanente nella storia del progetto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Tipo evento, kebab-case (es. 'decision', 'milestone')"},
                "description": {"type": "string", "description": "Descrizione 1-riga, max 200 char"},
            },
            "required": ["type", "description"],
        },
    },
]

