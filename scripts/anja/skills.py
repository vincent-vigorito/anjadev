"""Gruppo `skills`: skill management 3 livelli (discovery, lazy load, write-side)."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import PLUGIN_ROOT, ROOT, SCOPE, SCRIPTS_DIR, log_exc


def _skill_parser():
    """Lazy import skill_parser locale al plugin (stdlib only)."""
    import importlib.util
    sp = SCRIPTS_DIR / "skill_parser.py"
    spec = importlib.util.spec_from_file_location("skill_parser", sp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _skill_sources() -> list[tuple[str, Path]]:
    """Lista (scope_label, path) da scansionare per SKILL.md.

    Ordine = precedenza dedup (first wins):
      1. project anja  → ${ANJA_ROOT}/.anjawiki/skills/   (se SCOPE=project)
      2. hub anja      → ${ANJA_ROOT}/skills/             (se SCOPE=hub)
      3. user-global   → ~/.anja/skills/
      4. plugin        → ${CLAUDE_PLUGIN_ROOT}/skills/    (bundled)
      5. cc:project    → ${ANJA_ROOT}/.claude/skills/     (legacy)
      6. cc:user       → ~/.claude/skills/                (legacy)
    """
    sources: list[tuple[str, Path]] = []
    if SCOPE == "project":
        sources.append(("project", ROOT / ".anjawiki" / "skills"))
        sources.append(("cc:project", ROOT / ".claude" / "skills"))
    elif SCOPE == "hub":
        sources.append(("hub", ROOT / "skills"))
    sources.append(("user-global", Path.home() / ".anja" / "skills"))
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        sources.append(("plugin", Path(plugin_root) / "skills"))
    else:
        sources.append(("plugin", PLUGIN_ROOT / "skills"))
    sources.append(("cc:user", Path.home() / ".claude" / "skills"))
    return sources


def _scan_skill_dir(scope_label: str, path: Path, sp_mod) -> list[dict]:
    if not path.is_dir():
        return []
    out = []
    for sub in sorted(path.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.is_file():
            continue
        parsed = sp_mod.parse_skill_md(skill_md)
        if not parsed.get("name"):
            continue
        out.append({
            "name": parsed["name"],
            "description": parsed["description"][:200],
            "version": parsed["version"],
            "category": parsed["category"],
            "tags": parsed["tags"],
            "platforms": parsed["platforms"],
            "scope": scope_label,
            "path": parsed["path"],
        })
    return out


def tool_skill_list(args: dict) -> dict:
    """Level 0 catalog: lista skill con name + description + scope.

    Dedup per name (first wins per ordine sources).
    """
    sp_mod = _skill_parser()
    seen: dict[str, dict] = {}
    for scope_label, path in _skill_sources():
        for s in _scan_skill_dir(scope_label, path, sp_mod):
            seen.setdefault(s["name"], s)
    skills = sorted(seen.values(), key=lambda x: x["name"])
    return {"skills": skills, "total": len(skills)}


def tool_skill_load(args: dict) -> dict:
    """Level 1: carica body completo SKILL.md per name. Cerca in tutte le source."""
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name required"}
    sp_mod = _skill_parser()
    for scope_label, path in _skill_sources():
        skill_md = path / name / "SKILL.md"
        if skill_md.is_file():
            parsed = sp_mod.parse_skill_md(skill_md)
            if parsed.get("name"):
                parsed["scope"] = scope_label
                return parsed
    return {"error": f"skill '{name}' not found"}


def tool_skill_read_file(args: dict) -> dict:
    """Level 2: leggi file in references/scripts/templates della skill.

    args:
      name: str — skill name
      path: str — relative path inside skill dir (es. 'references/foo.md')
    """
    name = (args.get("name") or "").strip()
    rel_path = (args.get("path") or "").strip()
    if not name:
        return {"error": "name required"}
    if not rel_path:
        return {"error": "path required"}
    for scope_label, src in _skill_sources():
        skill_dir = src / name
        if not (skill_dir / "SKILL.md").is_file():
            continue
        candidate = (skill_dir / rel_path).resolve()
        try:
            candidate.relative_to(skill_dir.resolve())
        except ValueError:
            return {"error": "path escapes skill directory"}
        if not candidate.is_file():
            return {"error": f"file not found: {rel_path}"}
        try:
            return {
                "name": name,
                "scope": scope_label,
                "path": str(candidate),
                "content": candidate.read_text(encoding="utf-8"),
            }
        except OSError as e:
            return {"error": f"read failed: {e}"}
    return {"error": f"skill '{name}' not found"}


_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


_WRITABLE_SCOPES = ("project", "hub", "user-global")


def _default_write_scope() -> str:
    if SCOPE == "project":
        return "project"
    if SCOPE == "hub":
        return "hub"
    return "user-global"


def _resolve_skill_write_dir(scope: str, name: str) -> Optional[Path]:
    if not _SKILL_NAME_RE.match(name):
        return None
    if scope == "project":
        return ROOT / ".anjawiki" / "skills" / name
    if scope == "hub":
        return ROOT / "skills" / name
    if scope == "user-global":
        return Path.home() / ".anja" / "skills" / name
    return None


def _find_skill_dir_writable(name: str) -> Optional[tuple[str, Path]]:
    """Trova la skill esistente in uno scope writable. Ritorna (scope, dir) o None."""
    for scope_label, src in _skill_sources():
        if scope_label not in _WRITABLE_SCOPES:
            continue
        skill_dir = src / name
        if (skill_dir / "SKILL.md").is_file():
            return (scope_label, skill_dir)
    return None


def tool_skill_save(args: dict) -> dict:
    """Crea una nuova skill da zero. Errore se esiste già (usa skill.edit per riscrivere).

    args:
      name: str (kebab-case)
      content: str — intero SKILL.md (frontmatter + body)
      scope: 'project' | 'hub' | 'user-global' — default da SCOPE env
    """
    name = (args.get("name") or "").strip()
    content = args.get("content") or ""
    scope = (args.get("scope") or _default_write_scope()).strip()

    if not _SKILL_NAME_RE.match(name):
        return {"error": "name must be kebab-case (lowercase, digits, dash, underscore)"}
    if not content.strip():
        return {"error": "content required"}
    if scope not in _WRITABLE_SCOPES:
        return {"error": f"scope must be one of {_WRITABLE_SCOPES}"}

    target = _resolve_skill_write_dir(scope, name)
    if not target:
        return {"error": "cannot resolve target directory"}
    if target.exists():
        return {"error": f"skill already exists: {scope}/{name} (use skill.edit or skill.patch)"}

    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(content, encoding="utf-8")
    return {
        "status": "created",
        "scope": scope,
        "name": name,
        "path": str(target / "SKILL.md"),
    }


def _backup_skill_md(skill_dir: Path) -> Optional[Path]:
    """Salva copia di SKILL.md in <skill>/.history/<ts>.SKILL.md prima di una modifica.
    Ritorna path del backup o None se SKILL.md non esiste."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    history_dir = skill_dir / ".history"
    history_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    bak = history_dir / f"{ts}.SKILL.md"
    bak.write_text(skill_md.read_text(encoding="utf-8"), encoding="utf-8")
    # Cap history: keep last 20
    backups = sorted(history_dir.glob("*.SKILL.md"))
    for old in backups[:-20]:
        try:
            old.unlink()
        except Exception as _exc:
            log_exc("skills._backup_skill_md", _exc)
            pass
    return bak


def tool_skill_patch(args: dict) -> dict:
    """Patch mirato del SKILL.md via find/replace (Hermes-style, preferito a edit).
    Backup automatico in <skill>/.history/<ts>.SKILL.md prima della modifica.

    args:
      name: str
      old_string: str — testo esatto da sostituire (deve essere unique)
      new_string: str — nuovo testo
    """
    name = (args.get("name") or "").strip()
    old = args.get("old_string") or ""
    new = args.get("new_string") or ""

    if not name:
        return {"error": "name required"}
    if not old:
        return {"error": "old_string required"}

    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found

    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    if old not in text:
        return {"error": "old_string not found in SKILL.md"}
    if text.count(old) > 1:
        return {"error": "old_string is not unique — include more surrounding context"}
    bak = _backup_skill_md(skill_dir)
    skill_md.write_text(text.replace(old, new, 1), encoding="utf-8")
    return {"status": "patched", "scope": scope_label, "name": name,
            "backup": str(bak) if bak else None}


def tool_skill_history(args: dict) -> dict:
    """Lista i backup disponibili di una skill (in .history/).

    args:
      name: str — skill name
    """
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name required"}
    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    _, skill_dir = found
    history_dir = skill_dir / ".history"
    if not history_dir.is_dir():
        return {"name": name, "backups": []}
    backups = []
    for f in sorted(history_dir.glob("*.SKILL.md")):
        try:
            backups.append({
                "timestamp": f.stem.replace(".SKILL", ""),
                "path": str(f),
                "size_bytes": f.stat().st_size,
            })
        except Exception as _exc:
            log_exc("skills.tool_skill_history", _exc)
            continue
    return {"name": name, "backups": backups, "total": len(backups)}


def tool_skill_rollback(args: dict) -> dict:
    """Ripristina SKILL.md da un backup in .history/.

    args:
      name: str — skill name
      timestamp: str — opzionale, formato YYYYMMDD-HHMMSS. Se omesso usa l'ultimo.
    """
    name = (args.get("name") or "").strip()
    ts_filter = (args.get("timestamp") or "").strip() or None
    if not name:
        return {"error": "name required"}
    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    _, skill_dir = found
    history_dir = skill_dir / ".history"
    if not history_dir.is_dir():
        return {"error": "no .history/ directory — nothing to rollback"}
    backups = sorted(history_dir.glob("*.SKILL.md"))
    if not backups:
        return {"error": "no backups available"}
    target = None
    if ts_filter:
        for b in backups:
            if b.stem.startswith(ts_filter):
                target = b
                break
        if target is None:
            return {"error": f"backup with timestamp '{ts_filter}' not found"}
    else:
        target = backups[-1]
    # Backup state corrente PRIMA di rollback (così è anche reversibile)
    _backup_skill_md(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    return {"status": "rolled_back", "name": name,
            "restored_from": str(target),
            "current_snapshot_saved": True}


def tool_skill_edit(args: dict) -> dict:
    """Riscrive l'intero SKILL.md. Usa skill.patch quando possibile (più sicuro).

    args:
      name: str
      content: str — nuovo SKILL.md completo
    """
    name = (args.get("name") or "").strip()
    content = args.get("content") or ""
    if not name:
        return {"error": "name required"}
    if not content.strip():
        return {"error": "content required"}

    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return {"status": "edited", "scope": scope_label, "name": name}


def tool_skill_delete(args: dict) -> dict:
    """Cancella una skill (rimuove la directory). Solo in scope writable."""
    import shutil
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name required"}
    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found
    shutil.rmtree(skill_dir)
    return {"status": "deleted", "scope": scope_label, "name": name}


def tool_skill_write_file(args: dict) -> dict:
    """Scrive un file di reference dentro la skill (references/, scripts/, templates/).

    args:
      name: str — skill name
      path: str — relative path inside skill dir (es. 'references/api.md')
      content: str
    """
    name = (args.get("name") or "").strip()
    rel = (args.get("path") or "").strip()
    content = args.get("content") or ""
    if not name or not rel:
        return {"error": "name and path required"}
    if rel == "SKILL.md":
        return {"error": "use skill.edit or skill.patch to modify SKILL.md"}

    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found
    target = (skill_dir / rel).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError:
        return {"error": "path escapes skill directory"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"status": "written", "scope": scope_label, "name": name, "path": str(target)}


def tool_skill_remove_file(args: dict) -> dict:
    """Rimuove un file di reference. Usa skill.delete per cancellare l'intera skill."""
    name = (args.get("name") or "").strip()
    rel = (args.get("path") or "").strip()
    if not name or not rel:
        return {"error": "name and path required"}
    if rel == "SKILL.md":
        return {"error": "use skill.delete to remove the entire skill"}

    found = _find_skill_dir_writable(name)
    if not found:
        return {"error": f"skill '{name}' not found in a writable scope"}
    scope_label, skill_dir = found
    target = (skill_dir / rel).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError:
        return {"error": "path escapes skill directory"}
    if not target.is_file():
        return {"error": f"file not found: {rel}"}
    target.unlink()
    return {"status": "removed", "scope": scope_label, "name": name, "path": str(target)}


# Schemi MCP dei tool di questo modulo (registry: anja.server aggrega in MODULE_ORDER).
TOOLS = [
    {
        "name": "skill.list",
        "group": "skills",
        "description": "Catalog skills disponibili (workflow plugin/hub/workspace). Ritorna nomi + 1-line desc. Auto-iniettato nel system prompt; usa skill.load per body completo on-demand.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "skill.load",
        "group": "skills",
        "description": "Carica body SKILL.md completo per uno skill specifico. Usa DOPO aver visto il catalog quando ti serve eseguire un workflow specifico (es. ingest, query, lint).",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name (es. 'ingest', 'query', 'lint')"}},
            "required": ["name"],
        },
    },
    {
        "name": "skill.read_file",
        "group": "skills",
        "description": "Level 2: leggi un file di reference dentro la skill (references/, scripts/, templates/). Usa quando la SKILL.md menziona un file specifico (es. 'vedere references/api.md').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name"},
                "path": {"type": "string", "description": "Relative path inside skill dir (es. 'references/api.md')"},
            },
            "required": ["name", "path"],
        },
    },
    {
        "name": "skill.save",
        "group": "skills",
        "description": "Crea una nuova skill (Hermes skill_manage analog). Usa quando un workflow non-triviale merita di essere salvato come memoria procedurale (5+ tool call, scoperta di pattern, correzione utente). Scope default da SCOPE env.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case slug (es. 'deploy-staging')"},
                "content": {"type": "string", "description": "intero SKILL.md (frontmatter YAML + body markdown)"},
                "scope": {"type": "string", "enum": ["project", "hub", "user-global"], "description": "default da ANJA_SCOPE"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "skill.patch",
        "group": "skills",
        "description": "Patch mirato del SKILL.md via find/replace (preferito a edit, più sicuro). old_string deve essere unico nel file. Backup automatico in <skill>/.history/<ts>.SKILL.md prima della modifica (recoverable via skill.rollback).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["name", "old_string", "new_string"],
        },
    },
    {
        "name": "skill.history",
        "group": "skills",
        "description": "Lista backup disponibili di una skill (file in <skill>/.history/). Backup creati automaticamente prima di ogni skill.patch/skill.edit/skill.save. Max 20 backup tenuti per skill (LRU).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "skill.rollback",
        "group": "skills",
        "description": "Ripristina SKILL.md da un backup in .history/. Default: ultimo backup. Opzionale: timestamp specifico (formato YYYYMMDD-HHMMSS). Lo stato corrente viene a sua volta backuppato prima del rollback (reversibile).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "timestamp": {"type": "string", "description": "Opzionale, formato YYYYMMDD-HHMMSS. Vedi output skill.history per timestamps disponibili."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "skill.edit",
        "group": "skills",
        "description": "Riscrive l'intero SKILL.md. Usa skill.patch quando possibile (più sicuro per modifiche piccole).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "skill.delete",
        "group": "skills",
        "description": "Cancella una skill (rimuove la directory intera). Solo scope writable (project/hub/user-global).",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "skill.write_file",
        "group": "skills",
        "description": "Scrive un file di reference dentro la skill (references/, scripts/, templates/). Usa per aggiungere doc esempi, template, script helper.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string", "description": "relative path inside skill dir"},
                "content": {"type": "string"},
            },
            "required": ["name", "path", "content"],
        },
    },
    {
        "name": "skill.remove_file",
        "group": "skills",
        "description": "Rimuove un file di reference dalla skill. NON cancella la skill (per quello usa skill.delete).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["name", "path"],
        },
    },
]

