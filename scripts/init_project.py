#!/usr/bin/env python3
"""
init_project.py — scaffolding base per anja (Fase 1.3, modalità cold).

Crea `.anjawiki/` nel target:
  - genera token unico `anja_<uuid7-canonical>` (RFC 9562 UUIDv7, time-sortable)
  - copia `templates/project-skeleton/` (relativo allo script)
  - sostituisce i placeholder `{{TOKEN}}`, `{{NAME}}`, `{{TYPE}}`, `{{CREATED}}`,
    `{{INIT_MODE}}` nei file `meta.yaml`, `wiki/index.md`, `wiki/log.md`,
    `wiki/overview.md`.

Usato dal comando `/anja-init`. Solo stdlib (niente dipendenze).
"""

import argparse
import secrets
import shutil
import sys
import time
import uuid
from datetime import date
from pathlib import Path

VALID_TYPES = ("personal", "research", "business", "dev", "automation")
VALID_MODES = ("cold", "analyze")

PLACEHOLDER_FILES = (
    "meta.yaml",
    "wiki/index.md",
    "wiki/log.md",
    "wiki/overview.md",
)

# Triade files (scritte in project-root, fuori da .anjawiki/)
TRIADE_FILES = ("AGENTS.md", "SOUL.md", "TOOLS.md")

# Mapping type → baseline soul filename
SOUL_BASELINE_MAP = {
    "dev": "dev.md",
    "research": "research.md",
    "business": "business.md",
    "personal": "personal.md",
    "automation": "personal.md",  # automation usa baseline personal (richiama tool quotidiani)
}


def _uuid7_canonical() -> str:
    """RFC 9562 UUIDv7 canonical fallback per Python < 3.14.

    48-bit unix_ts_ms | 4-bit version=7 | 12-bit rand | 2-bit variant=0b10 | 62-bit rand.
    """
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = secrets.token_bytes(10)
    b = bytearray(16)
    b[0:6] = ts_ms.to_bytes(6, "big")
    b[6] = 0x70 | (rand[0] & 0x0F)
    b[7] = rand[1]
    b[8] = 0x80 | (rand[2] & 0x3F)
    b[9] = rand[3]
    b[10:16] = rand[4:10]
    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def generate_token() -> str:
    """`anja_<uuid7-canonical>` — RFC 9562 time-sortable identifier."""
    try:
        return f"anja_{uuid.uuid7()}"  # Python 3.14+
    except AttributeError:
        return f"anja_{_uuid7_canonical()}"


def _detect_user_name() -> str:
    import os, getpass
    return os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser() or "user"


def _write_config_json(target: Path) -> None:
    """Crea <target>/.anjawiki/config.json con memory section default. Non-destructive."""
    import json
    cfg_path = target / "config.json"
    if cfg_path.is_file():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if "memory" in existing:
            return  # già presente, skip
    else:
        existing = {}
    existing.setdefault("memory", {
        "hot_budget_tokens": 1500,
        "warm_budget_tokens": 3000,
        "log_entries_count": 3,
        "session_summaries_count": 5,
        "wiki_match_max_pages": 3,
        "cc_memory_mirror": True,
    })
    cfg_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# Schema version del wiki: bump alla rottura del wire format (path/frontmatter/log format).
# Consumatori esterni (hub, tool di sync) leggono .anjawiki/.schema-version per gate migration.
SCHEMA_VERSION = "1.0"


def _write_schema_version(target: Path) -> None:
    """Scrive <target>/.anjawiki/.schema-version. Non-destructive: skippa se già presente."""
    sv = target / ".schema-version"
    if not sv.is_file():
        sv.write_text(SCHEMA_VERSION + "\n", encoding="utf-8")


# Tool groups attivi nel plugin standalone (Fase P-Plugin).
# Esclude: agents, tasks (scheduling), workspace, kanban, goals (richiedono hub/daemon).
# Include: memory, sessions, soul, user (Identity), skills, wiki.
PLUGIN_DEFAULT_TOOL_GROUPS = "memory,sessions,soul,user,skills,wiki"


def _register_anja_memory_mcp(project_root: Path, *, force_update_env: bool = False) -> None:
    """Aggiunge anja_memory MCP server al <project>/.mcp.json (merge non-destructive).

    Se `force_update_env=True` aggiorna l'env del server esistente (usato da upgrade_triade
    per backfill di ANJA_TOOL_GROUPS su progetti vecchi).
    """
    import json
    mcp_server_path = get_plugin_root() / "scripts" / "mcp_memory_server.py"
    if not mcp_server_path.is_file():
        return
    mcp_path = project_root / ".mcp.json"
    data = {"mcpServers": {}}
    if mcp_path.is_file():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            if "mcpServers" not in data:
                data["mcpServers"] = {}
        except Exception:
            pass

    server_cfg = {
        "command": sys.executable,
        "args": [str(mcp_server_path)],
        "env": {
            "ANJA_SCOPE": "project",
            "ANJA_ROOT": str(project_root),
            "ANJA_TOOL_GROUPS": PLUGIN_DEFAULT_TOOL_GROUPS,
        },
    }

    if "anja_memory" in data["mcpServers"]:
        if not force_update_env:
            return  # già presente, skip
        # Backfill missing keys solo (non sovrascrivere customizzazioni utente)
        existing = data["mcpServers"]["anja_memory"]
        env = existing.setdefault("env", {})
        if "ANJA_TOOL_GROUPS" not in env:
            env["ANJA_TOOL_GROUPS"] = PLUGIN_DEFAULT_TOOL_GROUPS
            print(f"[anja] backfilled ANJA_TOOL_GROUPS in existing anja_memory MCP")
        elif env.get("ANJA_TOOL_GROUPS"):
            # Ensure 'wiki' è incluso (Fase P-Plugin added later)
            groups = [g.strip() for g in env["ANJA_TOOL_GROUPS"].split(",") if g.strip()]
            if "wiki" not in groups:
                groups.append("wiki")
                env["ANJA_TOOL_GROUPS"] = ",".join(groups)
                print(f"[anja] added 'wiki' to ANJA_TOOL_GROUPS")
            else:
                return
        else:
            env["ANJA_TOOL_GROUPS"] = PLUGIN_DEFAULT_TOOL_GROUPS
    else:
        data["mcpServers"]["anja_memory"] = server_cfg

    mcp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[anja] anja_memory MCP registrato in {mcp_path.relative_to(project_root.parent) if project_root.parent.exists() else mcp_path}")


def _regenerate_tools_md(project_root: Path) -> None:
    """Best-effort: rigenera TOOLS.md chiamando tools_md.py. Skip su errore."""
    import subprocess
    script = get_plugin_root() / "scripts" / "tools_md.py"
    if not script.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(script), "--target", str(project_root)],
            check=False, capture_output=True, timeout=10,
        )
    except Exception:
        pass  # non-blocking


def _compose_claude_md(project_root: Path) -> None:
    """Best-effort: rigenera CLAUDE.md composed da AGENTS+SOUL+TOOLS."""
    import subprocess
    script = get_plugin_root() / "scripts" / "compose_claude_md.py"
    if not script.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(script), "--target", str(project_root)],
            check=False, capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _type_description(project_type: str) -> str:
    return {
        "dev": "progetto di sviluppo software",
        "research": "progetto di ricerca / investigazione",
        "business": "progetto di business / analisi",
        "personal": "wiki personale",
        "automation": "progetto di automazione",
    }.get(project_type, project_type)


def get_plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_template_dir() -> Path:
    template = get_plugin_root() / "templates" / "project-skeleton"
    if not template.is_dir():
        sys.exit(f"ERROR: template directory not found: {template}")
    return template


def get_triade_template_dir() -> Path:
    return get_plugin_root() / "templates" / "triade-skeleton"


def get_soul_baseline(project_type: str) -> str:
    """Read baseline SOUL personality text for given type."""
    fname = SOUL_BASELINE_MAP.get(project_type, "personal.md")
    baseline_path = get_plugin_root() / "templates" / "soul-baselines" / fname
    if not baseline_path.is_file():
        return f"(baseline mancante: {fname})"
    return baseline_path.read_text(encoding="utf-8").strip()


def write_triade(project_root: Path, replacements: dict) -> None:
    """Copia AGENTS.md/SOUL.md/TOOLS.md in project-root con placeholder substitution.

    Skip silenzioso se i file esistono già (non-destructive).
    """
    triade_src = get_triade_template_dir()
    if not triade_src.is_dir():
        print(f"[anja] WARNING: triade template not found at {triade_src}, skipping triade", file=sys.stderr)
        return

    for fname in TRIADE_FILES:
        src = triade_src / fname
        dst = project_root / fname
        if not src.is_file():
            continue
        if dst.exists():
            print(f"[anja] {fname} esiste già in project-root, skip")
            continue
        text = src.read_text(encoding="utf-8")
        for key, val in replacements.items():
            text = text.replace(f"{{{key}}}", val)
        dst.write_text(text, encoding="utf-8")
        print(f"[anja] scritto {dst.relative_to(project_root.parent) if project_root.parent.exists() else dst}")


def make_claude_md_symlink(project_root: Path) -> None:
    """LEGACY (M-Mem 7-bis sostituito da compose_claude_md.py).
    Kept per back-compat con upgrade_triade.py vecchio."""
    pass


def copy_template(src: Path, dst: Path) -> None:
    if dst.exists():
        sys.exit(f"ERROR: target already exists: {dst}")
    shutil.copytree(src, dst)


def substitute_placeholders(target: Path, replacements: dict[str, str]) -> None:
    for rel in PLACEHOLDER_FILES:
        f = target / rel
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        for key, val in replacements.items():
            text = text.replace(f"{{{{{key}}}}}", val)
        f.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="init_project.py",
        description="Initialize a anja wiki (cold scaffolding).",
    )
    p.add_argument("--type", required=True, choices=VALID_TYPES)
    p.add_argument("--mode", required=True, choices=VALID_MODES)
    p.add_argument(
        "--target",
        required=True,
        help="path where to create .anjawiki/ (es. './.anjawiki')",
    )
    p.add_argument(
        "--name",
        default=None,
        help="project name (default: basename of target's parent directory)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    target = Path(args.target).resolve()
    name = args.name if args.name else target.parent.name

    template_dir = get_template_dir()
    token = generate_token()
    created = date.today().isoformat()

    replacements = {
        "TOKEN": token,
        "NAME": name,
        "TYPE": args.type,
        "CREATED": created,
        "INIT_MODE": args.mode,
    }

    copy_template(template_dir, target)
    substitute_placeholders(target, replacements)
    _write_config_json(target)
    _write_schema_version(target)

    # Triade in project-root (parent di .anjawiki/)
    project_root = target.parent
    soul_baseline = get_soul_baseline(args.type)
    triade_replacements = {
        "DATE": created,
        "PROJECT_NAME": name,
        "PROJECT_DESCRIPTION": f"Progetto {args.type} anja-managed",
        "PROJECT_TYPE": args.type,
        "PROJECT_TYPE_DESCRIPTION": _type_description(args.type),
        "SOUL_BASELINE": soul_baseline,
        "USER_NAME": _detect_user_name(),
        "USER_LANG": "it",
        "USER_TONE": "diretto e conciso",
    }
    write_triade(project_root, triade_replacements)
    _register_anja_memory_mcp(project_root)
    _regenerate_tools_md(project_root)
    # compose CLAUDE.md DOPO TOOLS.md auto-gen (così include il content fresh)
    _compose_claude_md(project_root)

    print(f"[anja] initialized in {target}")
    print(f"  Token: {token}")
    print(f"  Type:  {args.type}")
    print(f"  Mode:  {args.mode}")
    print(f"  Name:  {name}")
    print(f"  Triade scritta in: {project_root}/AGENTS.md, SOUL.md, TOOLS.md")


if __name__ == "__main__":
    main()
