#!/usr/bin/env python3
"""
tools_md.py — auto-generatore di TOOLS.md per project o hub.

Scansiona tutte le fonti di "capabilities" disponibili nel context corrente:
  - <target>/.mcp.json                       → MCP servers
  - <target>/.claude/skills/<name>/SKILL.md  → skills locali al progetto/hub
  - ~/.claude/skills/<name>/SKILL.md         → skills user-global
  - <plugin>/skills/<name>/SKILL.md          → skills via plugin (anja, anja-hub, ...)
  - <plugin>/commands/*.md                   → slash commands via plugin
  - .claude-plugin/marketplace.json          → plugin nel marketplace locale

E produce <target>/TOOLS.md con sezioni: MCP servers, Skills, Plugins, Project commands.

Usage:
    python3 tools_md.py --target <project-root>      # per progetto
    python3 tools_md.py --hub <hub-root>             # per hub
    python3 tools_md.py --target <root> --dry-run    # solo stampa, no write
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ============================================================
# helpers
# ============================================================

def parse_skill_frontmatter(skill_md: Path) -> dict:
    """Parse name + description da frontmatter di SKILL.md."""
    info = {"name": skill_md.parent.name, "description": ""}
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return info
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return info
    block = m.group(1)
    for line in block.split("\n"):
        if line.startswith("name:"):
            info["name"] = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("description:"):
            d = line.split(":", 1)[1].strip().strip('"').strip("'")
            info["description"] = d[:200]
    return info


def parse_command_frontmatter(cmd_md: Path) -> dict:
    """Parse description da frontmatter di un command markdown."""
    info = {"name": cmd_md.stem, "description": ""}
    try:
        text = cmd_md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return info
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return info
    block = m.group(1)
    for line in block.split("\n"):
        if line.strip().startswith("description:"):
            d = line.split(":", 1)[1].strip().strip('"').strip("'")
            info["description"] = d[:160]
            break
    return info


def find_anja_plugin_root() -> Optional[Path]:
    """Trova plugin root del plugin anja. Strategia: parent del script,
    verifica .claude-plugin/plugin.json (nuovo) o plugin.json (legacy)."""
    here = Path(__file__).resolve()
    candidate = here.parent.parent
    if (candidate / ".claude-plugin" / "plugin.json").is_file():
        return candidate
    if (candidate / "plugin.json").is_file():
        return candidate
    return None


def find_known_plugins() -> list:
    """Ritorna lista plugin sibling (per layout monorepo). Lista vuota se standalone."""
    plugin_root = find_anja_plugin_root()
    if not plugin_root:
        return []
    plugins_parent = plugin_root.parent
    out = []
    for sub in sorted(plugins_parent.iterdir()):
        if not sub.is_dir():
            continue
        pj = sub / ".claude-plugin" / "plugin.json"
        if not pj.is_file():
            pj = sub / "plugin.json"
        if pj.is_file():
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
                out.append({
                    "name": data.get("name", sub.name),
                    "root": sub,
                    "description": data.get("description", "")[:160],
                    "version": data.get("version", ""),
                })
            except Exception:
                continue
    return out


# ============================================================
# scansioni
# ============================================================

def scan_mcp_servers(target: Path) -> list:
    """Legge <target>/.mcp.json. Ritorna lista [{name, kind, summary}]."""
    mcp_path = target / ".mcp.json"
    if not mcp_path.is_file():
        return []
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for name, cfg in (data.get("mcpServers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        kind = cfg.get("type", "stdio")
        if kind == "stdio" or "command" in cfg:
            cmd = cfg.get("command", "")
            args = cfg.get("args", [])
            tail = (args[-1] if args else "")
            summary = f"{cmd} {tail}".strip()[:120]
            out.append({"name": name, "kind": "stdio", "summary": summary})
        else:
            url = cfg.get("url", "")
            out.append({"name": name, "kind": kind, "summary": url[:120]})
    return sorted(out, key=lambda x: x["name"])


def scan_local_skills(target: Path) -> list:
    """Skills in <target>/.claude/skills/<name>/SKILL.md."""
    sk_dir = target / ".claude" / "skills"
    if not sk_dir.is_dir():
        return []
    out = []
    for sub in sorted(sk_dir.iterdir()):
        if sub.is_dir() and (sub / "SKILL.md").is_file():
            info = parse_skill_frontmatter(sub / "SKILL.md")
            info["scope"] = "local"
            out.append(info)
    return out


def scan_user_global_skills() -> list:
    """Skills in ~/.claude/skills/<name>/SKILL.md."""
    sk_dir = Path.home() / ".claude" / "skills"
    if not sk_dir.is_dir():
        return []
    out = []
    for sub in sorted(sk_dir.iterdir()):
        if sub.is_dir() and (sub / "SKILL.md").is_file():
            info = parse_skill_frontmatter(sub / "SKILL.md")
            info["scope"] = "user-global"
            out.append(info)
    return out


def scan_plugin_skills(plugins: list, mode: str) -> list:
    """Skills offerte dai plugin sibling. Filtra per mode: 'project' o 'hub'."""
    out = []
    # quali plugin sono rilevanti per il context
    rel_plugins = [p for p in plugins if (
        (mode == "project" and p["name"] in ("anja",)) or
        (mode == "hub" and p["name"] in ("anja-hub", "anja-routines"))
    )]
    for p in rel_plugins:
        sk_dir = p["root"] / "skills"
        if not sk_dir.is_dir():
            continue
        for sub in sorted(sk_dir.iterdir()):
            if sub.is_dir() and (sub / "SKILL.md").is_file():
                info = parse_skill_frontmatter(sub / "SKILL.md")
                info["scope"] = f"plugin:{p['name']}"
                out.append(info)
    return out


def scan_plugin_commands(plugins: list, mode: str) -> list:
    """Slash commands dai plugin. Filtra per mode."""
    out = []
    rel_plugins = [p for p in plugins if (
        (mode == "project" and p["name"] in ("anja",)) or
        (mode == "hub" and p["name"] in ("anja-hub", "anja-routines"))
    )]
    for p in rel_plugins:
        cmd_dir = p["root"] / "commands"
        if not cmd_dir.is_dir():
            continue
        for f in sorted(cmd_dir.glob("*.md")):
            info = parse_command_frontmatter(f)
            info["plugin"] = p["name"]
            out.append(info)
    return out


# ============================================================
# render TOOLS.md
# ============================================================

def render_tools_md(mode: str, target: Path, mcp: list, skills: list,
                    plugins: list, commands: list) -> str:
    """Costruisce il markdown finale."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    generator = "scripts/tools_md.py"

    lines = []
    lines.append("---")
    lines.append("auto_generated: true")
    lines.append(f"generator: {generator}")
    lines.append(f"updated: {now_iso}")
    lines.append("---")
    lines.append("")
    lines.append(f"# Tools disponibili ({mode}-level)")
    lines.append("")
    lines.append("<!-- AUTO-GENERATED. Do not edit manually. Re-run via:")
    lines.append(f"     python3 {generator} --{'target' if mode == 'project' else 'hub'} {target}")
    lines.append("     Token budget HOT target: ~200. -->")
    lines.append("")

    # MCP servers
    lines.append("## MCP servers")
    lines.append("")
    if not mcp:
        lines.append("(nessun MCP configurato)")
    else:
        for s in mcp:
            tag = "" if s["kind"] == "stdio" else f" `[{s['kind']}]`"
            lines.append(f"- **`{s['name']}`**{tag} — {s['summary']}")
    lines.append("")

    # Skills
    lines.append("## Skills")
    lines.append("")
    if not skills:
        lines.append("(nessuna skill)")
    else:
        # raggruppa per scope
        by_scope = {}
        for sk in skills:
            by_scope.setdefault(sk["scope"], []).append(sk)
        for scope in sorted(by_scope):
            lines.append(f"### {scope}")
            for sk in by_scope[scope]:
                desc = sk.get("description", "").strip()
                lines.append(f"- **`{sk['name']}`** — {desc or '(no description)'}")
            lines.append("")

    # Plugins
    lines.append("## Plugins installati")
    lines.append("")
    if not plugins:
        lines.append("(nessun plugin disponibile)")
    else:
        for p in plugins:
            v = f" v{p['version']}" if p.get("version") else ""
            lines.append(f"- **`{p['name']}`**{v} — {p['description']}")
    lines.append("")

    # Commands
    label = "Project commands" if mode == "project" else "Hub commands"
    lines.append(f"## {label}")
    lines.append("")
    if not commands:
        lines.append("(nessuna)")
    else:
        for c in commands:
            desc = c.get("description", "").strip()
            lines.append(f"- `/{c['name']}` — {desc or '(no description)'}")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# main
# ============================================================

def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", help="project root path (un livello sopra .anjawiki/)")
    g.add_argument("--hub", help="hub root path")
    p.add_argument("--dry-run", action="store_true", help="non scrive TOOLS.md")
    args = p.parse_args()

    if args.target:
        mode = "project"
        target = Path(args.target).expanduser().resolve()
    else:
        mode = "hub"
        target = Path(args.hub).expanduser().resolve()

    if not target.is_dir():
        sys.exit(f"ERROR: target not found: {target}")

    plugins = find_known_plugins()
    mcp_servers = scan_mcp_servers(target)
    skills = scan_local_skills(target) + scan_user_global_skills() + scan_plugin_skills(plugins, mode)
    commands = scan_plugin_commands(plugins, mode)

    md = render_tools_md(mode, target, mcp_servers, skills, plugins, commands)

    out_path = target / "TOOLS.md"
    if args.dry_run:
        print(md)
        print(f"\n[dry-run] non scritto in {out_path}", file=sys.stderr)
        return

    out_path.write_text(md, encoding="utf-8")
    print(f"✓ {out_path} aggiornato ({len(mcp_servers)} MCP, {len(skills)} skill, {len(plugins)} plugin, {len(commands)} command)")

    # Trigger compose_claude_md.py per riflettere TOOLS.md fresh in CLAUDE.md
    import subprocess
    compose_script = Path(__file__).resolve().parent / "compose_claude_md.py"
    if compose_script.is_file():
        try:
            subprocess.run(
                [sys.executable, str(compose_script), "--target", str(target), "--quiet"],
                check=False, capture_output=True, timeout=8,
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
