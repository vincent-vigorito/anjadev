"""Contratto Codex → journal Anja senza toccare gli hook Claude Code."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "hooks" / "codex_adapter.py"


def _load_adapter():
    spec = importlib.util.spec_from_file_location("codex_adapter", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rollout(path: Path, workspace: Path):
    lines = [
        {"type": "session_meta", "timestamp": "2026-07-12T10:00:00Z",
         "payload": {"session_id": "codex-sess-123", "cwd": str(workspace)}},
        {"type": "response_item", "timestamp": "2026-07-12T10:00:01Z",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "analizza il modulo auth"}]}},
        {"type": "response_item", "timestamp": "2026-07-12T10:00:02Z",
         "payload": {"type": "function_call", "name": "functions.exec"}},
        {"type": "response_item", "timestamp": "2026-07-12T10:00:03Z",
         "payload": {"type": "message", "role": "assistant", "content": [
             {"type": "output_text", "text": "Ho trovato il flusso."}]}},
        {"type": "response_item", "timestamp": "2026-07-12T10:00:04Z",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "ora aggiungi un test"}]}},
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp())
    project = tmp / "project"
    (project / ".anjawiki" / "wiki" / "sessions").mkdir(parents=True)
    (project / ".anjawiki" / "meta.yaml").write_text("name: project\ntype: dev\n", encoding="utf-8")
    codex_home = tmp / "codex"
    rollout = codex_home / "sessions" / "2026" / "07" / "12" / "rollout-codex-sess-123.jsonl"
    rollout.parent.mkdir(parents=True)
    _rollout(rollout, project)

    env = dict(os.environ, CODEX_HOME=str(codex_home), ANJA_WIKI_EMBED="0", ANJA_AUTO_SUMMARY="0")
    payload = json.dumps({"cwd": str(project)})
    result = subprocess.run([sys.executable, str(ADAPTER), "stop"], input=payload,
                            text=True, capture_output=True, cwd=str(project), env=env, timeout=30)
    assert result.returncode == 0, result.stderr

    journals = list((project / ".anjawiki" / "wiki" / "sessions").rglob("*.md"))
    assert len(journals) == 1, journals
    journal = journals[0].read_text(encoding="utf-8")
    assert "analizza il modulo auth" in journal
    assert "ora aggiungi un test" in journal
    assert "functions.exec" in journal
    assert "codex-sess-123" in journal
    assert "agent: cli-codex" in journal
    assert (project / ".anjawiki" / "transcripts" / "codex" / "codex-sess-123.jsonl").is_file()
    print("✓ adapter Codex converte rollout → journal con prompt e tool stats")

    adapter = _load_adapter()
    normalized = adapter._response_items_to_cc(rollout)[0]
    assert normalized[0]["message"]["role"] == "user"
    assert normalized[1]["message"]["content"][-1]["name"] == "functions.exec"
    print("✓ conversione rollout preserva messaggi e function call")

    post = adapter._normalize_post_tool_payload({"tool": "edit", "input": {"filePath": "/tmp/wiki.md"}})
    assert post == {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/wiki.md"}}
    print("✓ conversione PostToolUse Codex preserva edit e file path")
    print("\nOK 3/3")


if __name__ == "__main__":
    main()
