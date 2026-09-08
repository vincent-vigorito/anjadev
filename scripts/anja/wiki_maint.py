"""Gruppo `wiki` (parte 2, manutenzione): backlinks, lint, verify, rename, replace_links, delete, tree, stats."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import trust
from .common import (
    _confined_path,
    _iter_wiki_md,
    _parse_frontmatter,
    _slug_of,
    _wiki_page,
    _wiki_root,
)
from .config import ROOT, log_exc
from .persistence import (
    check_revision,
    delete_text,
    persistence_errors,
    read_text,
    revision,
    write_many,
    write_text,
)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


_WIKILINK_RE = re.compile(r"\[\[([^\]|#\s]+)(#[^\]|]+)?(\|[^\]]+)?\]\]")


def tool_wiki_backlinks(args: dict) -> dict:
    """Trova tutte le pagine che linkano allo slug specificato via [[link]].

    Riconosce: [[slug]], [[slug|label]], [[slug#section]], [[slug#section|label]].
    Restituisce: {target_slug, backlinks: [{from_slug, from_path, from_type, occurrences, contexts: [str]}]}
    """
    slug = (args.get("slug") or "").strip()
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return {"error": "slug required"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    backlinks = []
    for f in _iter_wiki_md(wiki):
        if _slug_of(f) == slug:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as _exc:
            log_exc("wiki_maint.tool_wiki_backlinks", _exc)
            continue
        contexts = []
        occ = 0
        for line_no, line in enumerate(text.split("\n"), start=1):
            for m in _WIKILINK_RE.finditer(line):
                if m.group(1) == slug:
                    occ += 1
                    if len(contexts) < 3:
                        snippet = line.strip()[:160]
                        contexts.append(f"L{line_no}: {snippet}")
        if occ > 0:
            fm, _ = _parse_frontmatter(text)
            try:
                rel = str(f.relative_to(ROOT))
            except ValueError:
                rel = str(f)
            backlinks.append({
                "from_slug": _slug_of(f),
                "from_path": rel,
                "from_type": fm.get("type", f.parent.name.rstrip("s")),
                "occurrences": occ,
                "contexts": contexts,
            })

    backlinks.sort(key=lambda x: -x["occurrences"])
    return {"target_slug": slug, "count": len(backlinks), "backlinks": backlinks}


@persistence_errors
def tool_wiki_verify(args: dict) -> dict:
    """Registra una verifica automatica legata alla revisione corrente."""
    slug = (args.get("slug") or "").strip()
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return {"error": "slug required"}

    by = (args.get("by") or "").strip()
    if not by:
        by = "process:anja"
    if by.startswith("human:"):
        return {"error": "human verification requires the local operator CLI (scripts/verify_page.py)",
                "code": "human_verification_requires_operator"}
    if not re.match(r"^(human:|process:)[\w.-]+$|^[\w.-]+/[\w.@-]+$", by):
        return {"error": f"by must follow the actor convention "
                         f"(human:<id> | process:<id> | <producer>/<version>): '{by}'"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}
    try:
        target = _wiki_page(wiki, slug)
    except (ValueError, OSError, RuntimeError) as e:
        return {"error": str(e)}
    if not target:
        return {"error": f"page not found: {slug}"}

    text = read_text(target)
    expected = check_revision(target, text, args.get("expected_revision"))
    fm, body = _parse_frontmatter(text)
    if not fm:
        return {"error": f"page '{slug}' has no frontmatter — cannot verify"}

    new_text = trust.append(text, by)
    new_revision = write_text(target, new_text, expected)
    return {"revision": new_revision, "slug": slug, "path": str(target), "verified_by": by,
            **trust.status(new_text)}


def tool_wiki_lint(args: dict) -> dict:
    """Health check del wiki: orfani, link rotti, pagine stale, frontmatter mancante.

    args:
      categories: opt list[str] subset di ['orphans', 'broken_links', 'stale', 'frontmatter']
                  (default tutte)
      stale_days: opt int (default 90) — pagine con `updated` più vecchie sono stale SE attive
    """
    cats_in = args.get("categories")
    all_cats = ("orphans", "broken_links", "stale", "frontmatter", "trust")
    categories = tuple(cats_in) if isinstance(cats_in, list) and cats_in else all_cats
    for c in categories:
        if c not in all_cats:
            return {"error": f"unknown category '{c}'. Valid: {all_cats}"}
    stale_days = int(args.get("stale_days", 90))

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    # Pass 1: catalog all pages (escludendo i 3 file speciali da orphan/frontmatter check)
    # MA scansionando TUTTI i file (inclusi i speciali) per il linkmap — altrimenti i
    # link da index/overview/log a una page non contano come backlink (bug fix v1.4.1).
    pages = {}  # slug -> {path, type, fm, body}
    all_files = []  # tutti i file md per scan link
    for f in _iter_wiki_md(wiki):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as _exc:
            log_exc("wiki_maint.tool_wiki_lint", _exc)
            continue
        all_files.append((f, text))
        if f.parent == wiki and f.name in ("log.md", "index.md", "overview.md"):
            continue
        fm, body = _parse_frontmatter(text)
        pages[_slug_of(f)] = {"path": f, "type": fm.get("type", ""), "fm": fm, "body": body, "text": text}

    # Build linkmap: slug -> list of slug-or-file-stem che linka.
    # Scansiona ANCHE i file speciali (index/overview/log) per i loro link.
    linkmap = {s: [] for s in pages}
    broken = []  # (from_slug, target_slug, line_no)
    for f, text in all_files:
        from_slug = _slug_of(f)
        for line_no, line in enumerate(text.split("\n"), start=1):
            for m in _WIKILINK_RE.finditer(line):
                target = m.group(1)
                if target in pages:
                    if from_slug not in linkmap[target]:
                        linkmap[target].append(from_slug)
                elif from_slug in pages:
                    # Solo i broken_links DA file non-speciali sono riportati
                    # (link rotti in log.md sono storia, non vanno fixati)
                    broken.append({"from_slug": from_slug, "target": target, "line": line_no, "context": line.strip()[:160]})

    result = {"summary": {}}

    if "orphans" in categories:
        orphans = []
        for slug, p in pages.items():
            if not linkmap.get(slug):
                try:
                    rel = str(p["path"].relative_to(ROOT))
                except ValueError:
                    rel = str(p["path"])
                orphans.append({"slug": slug, "type": p["type"], "path": rel})
        orphans.sort(key=lambda x: (x["type"], x["slug"]))
        result["orphans"] = orphans
        result["summary"]["orphans"] = len(orphans)

    if "broken_links" in categories:
        result["broken_links"] = broken
        result["summary"]["broken_links"] = len(broken)

    if "stale" in categories:
        cutoff = datetime.now().astimezone().date() - timedelta(days=stale_days)
        stale = []
        for slug, p in pages.items():
            upd = p["fm"].get("updated")
            if not upd or not isinstance(upd, str):
                continue
            try:
                upd_date = datetime.fromisoformat(upd).date()
            except Exception as _exc:
                log_exc("wiki_maint.tool_wiki_lint", _exc)
                continue
            if upd_date < cutoff and linkmap.get(slug):
                age_days = (datetime.now().astimezone().date() - upd_date).days
                try:
                    rel = str(p["path"].relative_to(ROOT))
                except ValueError:
                    rel = str(p["path"])
                stale.append({"slug": slug, "type": p["type"], "path": rel, "updated": upd, "age_days": age_days, "backlinks_count": len(linkmap[slug])})
        stale.sort(key=lambda x: -x["age_days"])
        result["stale"] = stale
        result["summary"]["stale"] = len(stale)

    if "frontmatter" in categories:
        required = ("title", "type", "created", "updated")
        issues = []
        for slug, p in pages.items():
            missing = [k for k in required if k not in p["fm"]]
            if missing:
                try:
                    rel = str(p["path"].relative_to(ROOT))
                except ValueError:
                    rel = str(p["path"])
                issues.append({"slug": slug, "type": p["type"], "path": rel, "missing_fields": missing})
        issues.sort(key=lambda x: x["slug"])
        result["frontmatter_issues"] = issues
        result["summary"]["frontmatter_issues"] = len(issues)

    if "trust" in categories:
        # Schema 1.1: freshness contrattuale (stale_after) + trust tier (verified)
        today = datetime.now().astimezone().date()
        expired = []
        tiers = {"unverified": 0, "machine_confirmed": 0, "human_reviewed": 0}
        deprecated = []
        for slug, p in pages.items():
            fmp = p["fm"]
            tier = trust.status(p["path"].read_text(encoding="utf-8"))["trust_tier"].replace("-", "_")
            tiers[tier] = tiers.get(tier, 0) + 1
            if str(fmp.get("status", "")).strip() == "deprecated":
                deprecated.append(slug)
            sa = str(fmp.get("stale_after", "")).strip()
            if sa:
                try:
                    sa_date = datetime.fromisoformat(sa).date()
                except Exception as _exc:
                    log_exc("wiki_maint.tool_wiki_lint", _exc)
                    continue
                if today >= sa_date:
                    expired.append({"slug": slug, "type": p["type"], "stale_after": sa,
                                    "days_expired": (today - sa_date).days})
        expired.sort(key=lambda x: -x["days_expired"])
        result["stale_after_expired"] = expired
        result["deprecated_pages"] = sorted(deprecated)
        result["summary"]["stale_after_expired"] = len(expired)
        result["summary"]["trust_tiers"] = tiers

    # Segnale (non errore): i diari crescono più della conoscenza → serve compact/steward.
    # Misura che l'ha motivato: 493 session vs 37 pagine (AnjaHub, 2026-08-19).
    sess_dir = wiki / "sessions"
    n_sess = sum(1 for f in sess_dir.rglob("*.md")
                 if f.is_file() and "archive" not in f.relative_to(sess_dir).parts) if sess_dir.is_dir() else 0
    n_know = sum(1 for p in pages.values() if p["type"] in ("entity", "concept"))
    if n_sess > 3 * max(1, n_know):
        result["warnings"] = result.get("warnings", []) + [{
            "code": "session-volume",
            "message": f"{n_sess} session vs {n_know} pagine entity/concept (> 3×): i journal non sono "
                       f"conoscenza — lancia scripts/compact_sessions.py (o lo steward) e promuovi al wiki",
        }]
    result["summary"]["session_volume"] = {"sessions": n_sess, "knowledge_pages": n_know}

    result["summary"]["pages_scanned"] = len(pages)
    result["summary"]["stale_threshold_days"] = stale_days
    return result


_WIKI_SPECIALS = ("index.md", "log.md", "overview.md", "roadmap.md")


_WIKI_CATEGORIES = ("entities", "concepts", "sources", "analysis", "sessions")


def tool_wiki_tree(args: dict) -> dict:
    """Struttura ad albero del wiki: 4 file speciali + 5 categorie con file list.

    args:
      max_per_category: opt int (default 50) — tronca liste lunghe con count residuo
      include_files: opt bool (default True) — se False ritorna solo counts senza nomi
    """
    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    max_per_cat = int(args.get("max_per_category", 50))
    include_files = args.get("include_files", True)

    specials = []
    for name in _WIKI_SPECIALS:
        p = wiki / name
        specials.append({"name": name, "exists": p.is_file(), "size_bytes": p.stat().st_size if p.is_file() else 0})

    categories = {}
    for cat in _WIKI_CATEGORIES:
        d = wiki / cat
        if not d.is_dir():
            categories[cat] = {"count": 0, "files": []}
            continue
        # Recursive: sessions/ ha sub-cartelle date, le altre categorie no, ma rglob copre entrambi
        files = sorted([
            str(f.relative_to(d)) for f in d.rglob("*.md")
            if f.is_file() and not f.name.startswith(".")
        ])
        entry = {"count": len(files)}
        if include_files:
            if len(files) > max_per_cat:
                entry["files"] = files[:max_per_cat]
                entry["truncated"] = len(files) - max_per_cat
            else:
                entry["files"] = files
        categories[cat] = entry

    # Render markdown leggibile
    lines = [f"# Wiki tree — {wiki.relative_to(ROOT) if ROOT in wiki.parents or wiki.parent == ROOT else wiki}", ""]
    lines.append("## Special files")
    for s in specials:
        mark = "✓" if s["exists"] else "✗"
        lines.append(f"- {mark} `{s['name']}` ({s['size_bytes']} B)" if s["exists"] else f"- {mark} `{s['name']}` (missing)")
    lines.append("")
    for cat in _WIKI_CATEGORIES:
        e = categories[cat]
        lines.append(f"## {cat}/ ({e['count']})")
        if include_files and e.get("files"):
            for f in e["files"]:
                lines.append(f"- {f}")
            if e.get("truncated"):
                lines.append(f"- … +{e['truncated']} altri")
        lines.append("")

    return {
        "wiki_root": str(wiki),
        "specials": specials,
        "categories": categories,
        "rendered": "\n".join(lines).rstrip(),
    }


def tool_wiki_stats(args: dict) -> dict:
    """Statistiche del wiki: counts per type, top-linked, last-updated, size, log/session counts.

    args:
      top_n: opt int (default 10) — quanti elementi nelle top list
    """
    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    top_n = max(1, int(args.get("top_n", 10)))

    # Pass 1: catalog pagine non-speciali + linkmap + size
    pages = {}  # slug -> {path, type, updated, size}
    all_files = []
    type_counts = {}
    total_size = 0

    for f in _iter_wiki_md(wiki):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as _exc:
            log_exc("wiki_maint.tool_wiki_stats", _exc)
            continue
        size = f.stat().st_size
        total_size += size
        all_files.append((f, text))
        if f.parent == wiki and f.name in _WIKI_SPECIALS:
            continue
        fm, _ = _parse_frontmatter(text)
        ptype = fm.get("type") or f.parent.name.rstrip("s")
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
        pages[_slug_of(f)] = {
            "path": f,
            "type": ptype,
            "updated": fm.get("updated") if isinstance(fm.get("updated"), str) else None,
            "size": size,
        }

    # Linkmap: target_slug -> count of incoming links
    incoming = {s: 0 for s in pages}
    for f, text in all_files:
        from_slug = _slug_of(f)
        if from_slug in pages:
            seen_targets = set()
            for m in _WIKILINK_RE.finditer(text):
                target = m.group(1)
                if target in pages and target != from_slug:
                    seen_targets.add(target)
            for t in seen_targets:
                incoming[t] += 1

    top_linked = sorted(
        ({"slug": s, "type": pages[s]["type"], "backlinks": c} for s, c in incoming.items() if c > 0),
        key=lambda x: -x["backlinks"],
    )[:top_n]

    updated_pages = [
        {"slug": s, "type": p["type"], "updated": p["updated"]}
        for s, p in pages.items() if p["updated"]
    ]
    updated_pages.sort(key=lambda x: x["updated"], reverse=True)
    last_updated = updated_pages[:top_n]

    # Log entry count (parse "## [YYYY-MM-DD] ...")
    log_path = _confined_path(wiki, wiki / "log.md")
    log_entries = 0
    if log_path.is_file():
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            log_entries = sum(1 for line in log_text.split("\n") if re.match(r"^## \[\d{4}-\d{2}-\d{2}\]", line))
        except Exception as _exc:
            log_exc("wiki_maint.tool_wiki_stats", _exc)
            pass

    # Session count = file .md ricorsivi in sessions/ (struttura sub-cartelle date)
    sessions_dir = wiki / "sessions"
    session_count = 0
    archived_count = 0
    if sessions_dir.is_dir():
        for f in sessions_dir.rglob("*.md"):
            if not f.is_file() or f.name.startswith("."):
                continue
            if "archive" in f.relative_to(sessions_dir).parts:
                archived_count += 1
            else:
                session_count += 1

    return {
        "wiki_root": str(wiki),
        "total_pages": len(pages),
        "total_size_bytes": total_size,
        "total_size_kb": round(total_size / 1024, 1),
        "by_type": dict(sorted(type_counts.items(), key=lambda kv: -kv[1])),
        "top_linked": top_linked,
        "last_updated": last_updated,
        "log_entries": log_entries,
        "session_count": session_count,
        "archived_session_count": archived_count,
        "orphan_count": sum(1 for c in incoming.values() if c == 0),
    }


@persistence_errors
def tool_wiki_rename(args: dict) -> dict:
    """Rinomina una pagina wiki preservando tutti i [[link]] cross-wiki.

    args:
      old_slug: slug attuale (req)
      new_slug: nuovo slug kebab-case (req)
    """
    old_slug = (args.get("old_slug") or "").strip()
    new_slug = (args.get("new_slug") or "").strip()
    if old_slug.endswith(".md"):
        old_slug = old_slug[:-3]
    if new_slug.endswith(".md"):
        new_slug = new_slug[:-3]
    if not old_slug or not new_slug:
        return {"error": "old_slug and new_slug required"}
    if old_slug == new_slug:
        return {"error": "old_slug == new_slug, no-op"}
    if not _SLUG_RE.match(new_slug):
        return {"error": f"new_slug must be kebab-case ([a-z0-9-]+): '{new_slug}'"}

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    source_file = None
    for f in _iter_wiki_md(wiki):
        if _slug_of(f) == old_slug:
            source_file = f
            break
    if not source_file:
        return {"error": f"page not found: {old_slug}"}

    expected = check_revision(source_file, read_text(source_file), args.get("expected_revision"))
    target_file = _confined_path(wiki, source_file.parent / f"{new_slug}.md")
    if target_file.exists():
        return {"error": f"target already exists: {target_file.name}"}

    # Replace links: [[old]], [[old|label]], [[old#section]], [[old#section|label]]
    changes = {}
    files_touched = []
    links_updated = 0
    link_re = re.compile(r"\[\[" + re.escape(old_slug) + r"((?:#[^\]|]+)?(?:\|[^\]]+)?)\]\]")
    for f in _iter_wiki_md(wiki):
        if f == source_file:
            continue
        try:
            text = read_text(f)
        except Exception as _exc:
            log_exc("wiki_maint.tool_wiki_rename", _exc)
            continue
        new_text, n = link_re.subn(lambda m: f"[[{new_slug}{m.group(1)}]]", text)
        if n > 0:
            changes[f] = (new_text, revision(text))
            files_touched.append(str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f))
            links_updated += n

    # Rename file (preserve content, optionally bump title if matches)
    write_many(changes, rename=(source_file, target_file, expected))
    from .wiki import _trigger_wiki_embed_bg
    for changed_path in set(changes) | {source_file, target_file}:
        _trigger_wiki_embed_bg(changed_path)

    return {
        "revision": expected,
        "renamed_from": old_slug,
        "renamed_to": new_slug,
        "new_path": str(target_file.relative_to(ROOT)) if target_file.is_relative_to(ROOT) else str(target_file),
        "links_updated": links_updated,
        "files_touched": files_touched,
    }


@persistence_errors
def tool_wiki_replace_links(args: dict) -> dict:
    """Replace `[[old]]` → `[[new]]` cross-wiki SENZA rinominare file.

    Utile per fixare convenzioni inconsistenti (es. `[[entity-X]]` → `[[X]]` in massa)
    o per sostituire link a pagine ancora da creare. Preserva label e anchor:
    `[[old|label]]` → `[[new|label]]`, `[[old#section]]` → `[[new#section]]`.

    args:
      old: slug attuale nei [[link]]
      new: slug nuovo da scrivere nei [[link]]
      dry_run: bool (default false) — true per preview senza scrivere
    """
    old = (args.get("old") or "").strip()
    new = (args.get("new") or "").strip()
    if not old or not new:
        return {"error": "old and new required"}
    if old == new:
        return {"error": "old == new, no-op"}
    dry_run = bool(args.get("dry_run", False))

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    link_re = re.compile(r"\[\[" + re.escape(old) + r"((?:#[^\]|]+)?(?:\|[^\]]+)?)\]\]")
    changes = {}
    files_touched = []
    links_replaced = 0

    for f in _iter_wiki_md(wiki):
        try:
            text = read_text(f)
        except Exception as _exc:
            log_exc("wiki_maint.tool_wiki_replace_links", _exc)
            continue
        new_text, n = link_re.subn(lambda m: f"[[{new}{m.group(1)}]]", text)
        if n > 0:
            changes[f] = (new_text, revision(text))
            try:
                rel = str(f.relative_to(ROOT))
            except ValueError:
                rel = str(f)
            files_touched.append({"path": rel, "occurrences": n, "revision": revision(text)})
            links_replaced += n

    if not dry_run:
        supplied = args.get("expected_revisions")
        if supplied is not None:
            for f in changes:
                rel = str(f.relative_to(ROOT))
                if rel not in supplied:
                    from .persistence import PersistenceError
                    raise PersistenceError("missing_revision", "expected_revisions missing a changed page", path=rel)
                text, _ = changes[f]
                changes[f] = (text, supplied[rel])
        write_many(changes)
        from .wiki import _trigger_wiki_embed_bg
        for path in changes:
            _trigger_wiki_embed_bg(path)
    return {
        "old": old,
        "new": new,
        "dry_run": dry_run,
        "links_replaced": links_replaced,
        "files_touched": files_touched,
    }


@persistence_errors
def tool_wiki_delete(args: dict) -> dict:
    """Cancella una pagina wiki. Safety: confirm=false ritorna preview con backlinks.

    args:
      slug: slug della pagina (req)
      confirm: bool (default false) — true per eseguire
    """
    slug = (args.get("slug") or "").strip()
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return {"error": "slug required"}
    confirm = bool(args.get("confirm", False))

    wiki = _wiki_root()
    if not wiki.is_dir():
        return {"error": f"wiki dir not found: {wiki}"}

    target_file = None
    for f in _iter_wiki_md(wiki):
        if _slug_of(f) == slug:
            target_file = f
            break
    if not target_file:
        return {"error": f"page not found: {slug}"}

    expected = check_revision(target_file, read_text(target_file), args.get("expected_revision"))
    # Compute backlinks (would become broken)
    backlinks_result = tool_wiki_backlinks({"slug": slug})
    backlinks = backlinks_result.get("backlinks", [])
    rel = str(target_file.relative_to(ROOT)) if target_file.is_relative_to(ROOT) else str(target_file)

    if not confirm:
        return {
            "slug": slug,
            "path": rel,
            "action": "preview",
            "revision": expected,
            "would_break_links": len(backlinks),
            "backlinks_preview": backlinks[:5],
            "hint": "Pass confirm=true to actually delete. Consider wiki.rename instead if you want to preserve links.",
        }

    delete_text(target_file, expected)
    from .wiki import _trigger_wiki_embed_bg
    _trigger_wiki_embed_bg(target_file)
    return {
        "slug": slug,
        "path": rel,
        "action": "deleted",
        "broken_links_now": len(backlinks),
        "backlinks_affected": [b["from_slug"] for b in backlinks],
    }


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "wiki.backlinks",
        "group": "wiki",
        "description": (
            "🔍 WIKI nav: trova tutte le pagine che linkano allo slug via [[link]]. "
            "Riconosce [[slug]], [[slug|label]], [[slug#section]], [[slug#section|label]]. "
            "Usa per: capire connessioni, decidere se cancellare una pagina (vedere chi "
            "diventerebbe broken), navigare la rete del wiki."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug della pagina target"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "wiki.lint",
        "group": "wiki",
        "description": (
            "🔍 WIKI health check: orfani (pagine non linkate da nessuno), broken_links "
            "([[X]] dove X non esiste), stale (updated > N giorni ma ancora attive), "
            "frontmatter_issues (campi obbligatori mancanti: title/type/created/updated), "
            "trust (schema 1.1: pagine oltre stale_after + conteggio trust tier da verified). "
            "Usa periodicamente per non lasciar degradare il wiki."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["orphans", "broken_links", "stale", "frontmatter", "trust"]},
                    "description": "Subset di check (default: tutti)",
                },
                "stale_days": {"type": "integer", "default": 90, "description": "Soglia stale (default 90 giorni)"},
            },
        },
    },
    {
        "name": "wiki.verify",
        "group": "wiki",
        "description": "Registra una verifica automatica sulla revisione corrente; conserva lo storico. Le conferme human richiedono la CLI locale dell'operatore.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug della pagina da verificare"},
                "by": {"type": "string", "description": "Attore automatico (default process:anja): process:<id> | <producer>/<version>"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "wiki.rename",
        "group": "wiki",
        "description": (
            "✏️ WIKI maintenance: rinomina una pagina preservando TUTTI i [[link]] "
            "cross-wiki (replace `[[old]]`, `[[old|label]]`, `[[old#section]]` "
            "→ `[[new...]]`). Validato kebab-case sul new_slug. Errore se target esiste."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "old_slug": {"type": "string"},
                "new_slug": {"type": "string"},
            },
            "required": ["old_slug", "new_slug"],
        },
    },
    {
        "name": "wiki.replace_links",
        "group": "wiki",
        "description": (
            "✏️ WIKI maintenance: replace `[[old]]` → `[[new]]` cross-wiki SENZA "
            "rinominare file. Utile per fixare convenzioni inconsistenti in massa "
            "(es. `[[entity-X]]` → `[[X]]`). Preserva label/anchor. dry_run=true "
            "per preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "old": {"type": "string"},
                "new": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["old", "new"],
        },
    },
    {
        "name": "wiki.delete",
        "group": "wiki",
        "description": (
            "🗑️ WIKI maintenance: cancella una pagina. SAFETY: confirm=false (default) "
            "ritorna preview con backlinks che diventerebbero rotti. confirm=true esegue. "
            "Suggerimento: se ha backlinks, considera wiki.rename per preservarli."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "confirm": {"type": "boolean", "default": False, "description": "Default false = preview. true = delete reale."},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "wiki.tree",
        "group": "wiki",
        "description": (
            "🌳 WIKI explore: struttura ad albero del wiki. Mostra 4 file speciali "
            "(index/log/overview/roadmap) + 5 categorie (entities/concepts/sources/analysis/sessions) "
            "con count e file list. Usa per orientarti veloce in un wiki sconosciuto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_per_category": {"type": "integer", "default": 50, "description": "Tronca liste lunghe"},
                "include_files": {"type": "boolean", "default": True, "description": "False = solo counts"},
            },
        },
    },
    {
        "name": "wiki.stats",
        "group": "wiki",
        "description": (
            "📊 WIKI explore: statistiche di salute del wiki. Counts per type, top-N "
            "pagine più linkate, top-N più recenti aggiornate, size totale, count entry "
            "log + count session. Usa per dashboard veloce dello stato del wiki."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "default": 10, "description": "Quanti item nelle top list"},
            },
        },
    },
]


for _spec in TOOLS:
    if _spec["name"] in {"wiki.verify", "wiki.rename", "wiki.delete"}:
        _spec["inputSchema"]["properties"]["expected_revision"] = {
            "type": "string", "description": "Revisione da wiki.read; stale restituisce revision_conflict."
        }

for _spec in TOOLS:
    if _spec["name"] == "wiki.replace_links":
        _spec["inputSchema"]["properties"]["expected_revisions"] = {
            "type": "object", "additionalProperties": {"type": "string"},
            "description": "Mappa path/revisione dalla preview dry_run: richiesta per ogni pagina modificata."
        }
