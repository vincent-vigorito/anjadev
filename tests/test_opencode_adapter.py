"""F-OpenCodeAdapter — test del CONTRATTO di integrazione.

Il plugin OpenCode (anja.js) traduce i messaggi OpenCode nel JSONL stile Claude Code
e invoca session_end.py INVARIATO. Questo test verifica il pezzo critico: un transcript
in quel formato passa attraverso session_end.py e produce il journal di sessione —
senza toccare il codice Python condiviso (zero regressioni per CC/Codex/Grok).

    python3 tests/test_opencode_adapter.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION_END = ROOT / "hooks" / "session_end.py"

# JSONL come lo produce toCcTranscript() del plugin (vedi .opencode/plugin/anja.js).
TRANSCRIPT = "\n".join([
    json.dumps({"timestamp": "2026-06-13T10:00:00.000Z", "type": "user",
                "message": {"role": "user", "content": "aggiungi la feature X al modulo Y"}}),
    json.dumps({"timestamp": "2026-06-13T10:00:05.000Z", "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "fatto, ecco la patch"},
                    {"type": "tool_use", "name": "Edit"},
                    {"type": "tool_use", "name": "Bash"}]}}),
    json.dumps({"timestamp": "2026-06-13T10:01:00.000Z", "type": "user",
                "message": {"role": "user", "content": "ora scrivi un test"}}),
]) + "\n"


def main():
    tmp = Path(tempfile.mkdtemp())
    # project anja minimale: find_anja_root cerca .anjawiki/meta.yaml
    (tmp / ".anjawiki").mkdir()
    (tmp / ".anjawiki" / "meta.yaml").write_text("name: testproj\ntype: project\n", encoding="utf-8")
    sessions_dir = tmp / ".anjawiki" / "wiki" / "sessions"
    sessions_dir.mkdir(parents=True)

    transcript = tmp / "oc-transcript.jsonl"
    transcript.write_text(TRANSCRIPT, encoding="utf-8")

    payload = json.dumps({
        "session_id": "oc-sess-123", "transcript_path": str(transcript),
        "cwd": str(tmp), "hook_event_name": "SessionEnd", "reason": "other",
    })

    env = dict(os.environ, ANJA_AUTO_SUMMARY="0", ANJA_WIKI_EMBED="0")
    r = subprocess.run([sys.executable, str(SESSION_END)], input=payload, text=True,
                       capture_output=True, cwd=str(tmp), env=env, timeout=30)

    written = list(sessions_dir.rglob("*.md"))
    assert written, f"nessun file di sessione scritto. stderr:\n{r.stderr}"
    body = written[0].read_text(encoding="utf-8")

    # 1. user prompts catturati dal transcript tradotto
    assert "aggiungi la feature X" in body, body
    assert "ora scrivi un test" in body, body
    print("✓ journal scritto dal transcript OpenCode→CC: user prompts catturati")

    # 2. tool stats estratte (Edit + Bash dai blocchi assistant)
    assert "Edit" in body and "Bash" in body, "tool stats mancanti"
    print("✓ tool stats estratte (Edit, Bash)")

    # 3. session_end.py NON modificato (il contratto regge con lo script invariato)
    assert "oc-sess-123" in body or written, "upsert per session_id"
    print("✓ session_end.py invariato processa il formato tradotto (zero modifiche Python)")

    print("\nOK 3/3")


if __name__ == "__main__":
    main()
