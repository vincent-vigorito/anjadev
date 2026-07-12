"""Verifica che l'installer Codex preservi hook/config esistenti e sia idempotente."""

import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_codex_hooks.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("install_codex_hooks", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    tmp = Path(tempfile.mkdtemp())
    codex = tmp / ".codex"
    codex.mkdir()
    hooks_path = codex / "hooks.json"
    hooks_path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing"}]}]}}), encoding="utf-8")
    (codex / "config.toml").write_text("[mcp_servers.test]\ncommand = 'test'\n\n[features]\nhooks = false\n", encoding="utf-8")

    installer = _load_installer()
    assert installer.install_hooks(tmp)[1] is True
    assert installer.enable_hooks_feature(tmp) is True
    assert installer.install_hooks(tmp)[1] is False
    assert installer.enable_hooks_feature(tmp) is False

    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
    assert hooks["Stop"][0]["hooks"][0]["command"] == "existing"
    assert len(hooks["Stop"]) == 2
    assert len(hooks["SessionStart"]) == 1
    assert "hooks = true" in (codex / "config.toml").read_text(encoding="utf-8")
    print("OK installer Codex preserva config, aggiunge hook Anja ed e idempotente")


if __name__ == "__main__":
    main()
