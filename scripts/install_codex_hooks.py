#!/usr/bin/env python3
"""Installa gli hook Anja nella configurazione Codex di un progetto.

Codex non scopre hook dal manifest del plugin. Questo installer conserva eventuali
hook esistenti e aggiunge solo i due lifecycle Anja compatibili: SessionStart e Stop.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _command(script: Path, *args: str) -> str:
    return " ".join(shlex.quote(str(part)) for part in (sys.executable, script, *args))


def _load_hooks(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise ValueError(f"{path} non contiene un oggetto hooks valido")
    return data


def _has_command(groups: list[Any], command: str) -> bool:
    return any(
        isinstance(group, dict)
        and any(isinstance(hook, dict) and hook.get("command") == command
                for hook in group.get("hooks", []))
        for group in groups
    )


def install_hooks(project: Path) -> tuple[Path, bool]:
    hooks_path = project / ".codex" / "hooks.json"
    data = _load_hooks(hooks_path)
    hooks = data["hooks"]
    changed = False
    entries = {
        "SessionStart": _command(PLUGIN_ROOT / "hooks" / "session_start.py"),
        "Stop": _command(PLUGIN_ROOT / "hooks" / "codex_adapter.py", "stop"),
    }
    for event, command in entries.items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.{event} deve essere una lista")
        if not _has_command(groups, command):
            groups.append({"hooks": [{"type": "command", "command": command, "timeout": 10}]})
            changed = True
    if changed:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return hooks_path, changed


def enable_hooks_feature(project: Path) -> bool:
    config_path = project / ".codex" / "config.toml"
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    section = re.search(r"(?ms)^\[features\]\n(.*?)(?=^\[|\Z)", text)
    if section:
        body = section.group(1)
        if re.search(r"^hooks\s*=\s*true\s*$", body, re.M):
            return False
        if re.search(r"^hooks\s*=\s*(?:true|false)\s*$", body, re.M):
            replacement = re.sub(r"^hooks\s*=\s*(?:true|false)\s*$", "hooks = true", body, flags=re.M)
        else:
            replacement = body + ("" if body.endswith("\n") else "\n") + "hooks = true\n"
        text = text[:section.start(1)] + replacement + text[section.end(1):]
    else:
        text += ("" if not text or text.endswith("\n") else "\n") + "\n[features]\nhooks = true\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=".", help="root del progetto Codex (default: cwd)")
    args = parser.parse_args()
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        parser.error(f"project non trovato: {project}")
    hooks_path, hooks_changed = install_hooks(project)
    feature_changed = enable_hooks_feature(project)
    print(f"{'aggiornato' if hooks_changed else 'gia presente'}: {hooks_path}")
    print(f"{'abilitato' if feature_changed else 'gia abilitato'}: {project / '.codex' / 'config.toml'} [features].hooks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
