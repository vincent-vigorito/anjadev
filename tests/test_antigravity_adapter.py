"""Contratto Antigravity CLI (agy) → journal/contesto Anja senza toccare gli hook Claude Code.
Payload e formato transcript = quelli osservati sul campo (agy 1.1.26, 2026-09-04)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _helpers import cov_env

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "hooks" / "antigravity_adapter.py"
INSTALLER = ROOT / "scripts" / "install_antigravity.py"
INIT = ROOT / "scripts" / "init_project.py"
PY = os.environ.get("ANJA_TEST_PYTHON") or sys.executable
PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _transcript(path: Path, project: Path) -> None:
    steps = [
        {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
         "created_at": "2026-09-04T13:50:42Z", "content": "<USER_REQUEST>\nanalizza il modulo auth\n</USER_REQUEST>"},
        {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
         "created_at": "2026-09-04T13:51:00Z", "thinking": "...", "tool_calls": [
             {"name": "view_file", "args": {"AbsolutePath": f"\"{project}/auth.py\"", "toolAction": "\"Viewing\""}}]},
        {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE",
         "created_at": "2026-09-04T13:51:01Z", "content": "Created At: ... (tool result)"},
        {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
         "created_at": "2026-09-04T13:53:00Z", "content": "Ho trovato il flusso di login.", "tool_calls": [
             {"name": "write_to_file", "args": {"TargetFile": f"\"{project}/.anjawiki/wiki/concepts/auth.md\"",
                                                "CodeContent": "\"# Auth\\n\"", "Overwrite": "true"}}]},
        {"step_index": 4, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
         "created_at": "2026-09-04T13:55:10Z", "content": "<USER_REQUEST>\nora aggiungi un test\n</USER_REQUEST>"},
        {"step_index": 5, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
         "created_at": "2026-09-04T13:57:30Z", "content": "<USER_REQUEST>\nperfetto, decisione: split del modulo\n</USER_REQUEST>"},
        {"step_index": 6, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE",
         "created_at": "2026-09-04T13:57:31Z", "content": "The following is a <SYSTEM_MESSAGE> ..."},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8")


def _run(event: str, payload: dict, cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, ANJA_AUTO_SUMMARY="0", ANJA_WIKI_EMBED="0", ANJA_STEWARD="0", **cov_env())
    env.pop("CLAUDECODE", None); env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env.update(env_extra or {})
    return subprocess.run([PY, str(ADAPTER), event], input=json.dumps(payload), text=True,
                          capture_output=True, cwd=str(cwd), env=env, timeout=60)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="anja-agy-"))
    home = tmp / "home"; home.mkdir()
    project = tmp / "project"; project.mkdir()
    r = subprocess.run([PY, str(INIT), "--type", "dev", "--mode", "cold", "--target", str(project / ".anjawiki"),
                        "--name", "project"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    (project / ".agents").mkdir()
    brain = tmp / "brain" / "5e3c0a3f-ba12-4e39-8e7c-b500649460a8"
    transcript = brain / ".system_generated" / "logs" / "transcript_full.jsonl"
    _transcript(transcript, project)
    common = {"artifactDirectoryPath": str(brain), "conversationId": "5e3c0a3f-ba12-4e39-8e7c-b500649460a8",
              "modelName": "gemini-3.8-flash-high", "transcriptPath": str(transcript), "workspacePaths": [str(project)]}
    hooks_cwd = project / ".agents"   # agy lancia gli hook da qui
    env = {"HOME": str(home), "ANTIGRAVITY_CONVERSATION_ID": common["conversationId"]}

    print("§1 stop → journal")
    r = _run("stop", {**common, "error": "", "executionNum": 0, "fullyIdle": True, "terminationReason": "NO_TOOL_CALL"}, hooks_cwd, env)
    check("exit 0 e stdout è solo '{}' (risposta per agy)", r.returncode == 0 and r.stdout.strip() == "{}", f"{r.returncode} {r.stdout!r} {r.stderr[-300:]}")
    journals = list((project / ".anjawiki" / "wiki" / "sessions").rglob("*.md"))
    check("un session file scritto", len(journals) == 1, str(journals) + r.stderr[-300:])
    text = journals[0].read_text(encoding="utf-8") if journals else ""
    check("agent: cli-antigravity + harness", "agent: cli-antigravity" in text and "harness: antigravity" in text, text[:300])
    check("prompt utente senza wrapper <USER_REQUEST>", "analizza il modulo auth" in text and "USER_REQUEST" not in text)
    check("tool agy nelle stats", "write_to_file" in text and "view_file" in text)
    check("cc_session_id = conversationId", common["conversationId"] in text)
    check("transcript normalizzato in .anjawiki/transcripts/antigravity/", (project / ".anjawiki" / "transcripts" / "antigravity" / f"{common['conversationId']}.jsonl").is_file())
    _run("stop", {**common, "terminationReason": "NO_TOOL_CALL"}, hooks_cwd, env)
    check("secondo Stop (ogni turno) → upsert, non un secondo file", len(list((project / ".anjawiki" / "wiki" / "sessions").rglob("*.md"))) == 1)

    print("§2 pre-invocation → contesto iniettato")
    r = _run("pre-invocation", {**common, "invocationNum": 0, "initialNumSteps": 1}, hooks_cwd, env)
    out = {}
    try:
        out = json.loads(r.stdout)
    except Exception:
        pass
    steps = out.get("injectSteps") or []
    check("invocationNum 0 → injectSteps con ephemeralMessage", bool(steps) and "ephemeralMessage" in steps[0], f"{r.stdout[:200]!r} {r.stderr[-200:]}")
    check("il messaggio è l'output di session_start (Sessione aperta)", bool(steps) and "Sessione aperta" in steps[0].get("ephemeralMessage", ""), str(steps)[:200])
    r = _run("pre-invocation", {**common, "invocationNum": 2, "initialNumSteps": 7}, hooks_cwd, env)
    check("invocationNum > 0 → {}", r.stdout.strip() == "{}", r.stdout[:100])

    print("§3 post-tool-use: normalizzazione")
    ad = _load(ADAPTER)
    n = ad._normalize_post_tool_payload({"toolCall": {"name": "write_to_file", "args": {"TargetFile": "\"/x/y.md\"", "CodeContent": "\"c\""}}, "stepIdx": 3})
    check("write_to_file/TargetFile (JSON-encoded) → Write/file_path", n == {"tool_name": "Write", "tool_input": {"file_path": "/x/y.md"}}, str(n))
    n = ad._normalize_post_tool_payload({"toolCall": {"name": "replace_file_content", "args": {"TargetFile": "/x/z.md"}}})
    check("replace_file_content → Edit", n["tool_name"] == "Edit" and n["tool_input"]["file_path"] == "/x/z.md")
    n = ad._normalize_post_tool_payload({"toolCall": {"name": "run_command", "args": {"CommandLine": "\"ls\""}}})
    check("run_command → ignorato (nessun file_path)", n["tool_input"]["file_path"] is None)
    r = _run("post-tool-use", {**common, "toolCall": {"name": "write_to_file", "args": {"TargetFile": f"\"{project}/.anjawiki/wiki/concepts/auth.md\""}}, "stepIdx": 3}, hooks_cwd, env)
    check("post-tool-use exit 0, stdout {}", r.returncode == 0 and r.stdout.strip() == "{}", r.stderr[-200:])
    check("evento ignoto → {} senza crash", _run("bogus", {}, hooks_cwd, env).stdout.strip() == "{}")

    print("§4 installer .agents/ (merge idempotente)")
    (project / ".agents" / "hooks.json").write_text(json.dumps({"lint": {"PreToolUse": [{"type": "command", "command": "./lint.sh", "matcher": "run_command"}]}}), encoding="utf-8")
    (project / ".agents" / "mcp_config.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    r = subprocess.run([PY, str(INSTALLER), "--project", str(project)], capture_output=True, text=True, timeout=30)
    check("installer exit 0", r.returncode == 0, r.stderr[-200:])
    hooks = json.loads((project / ".agents" / "hooks.json").read_text(encoding="utf-8"))
    check("hook 'anja' con PreInvocation/PostToolUse/Stop in formato piatto", set(hooks.get("anja", {})) == {"PreInvocation", "PostToolUse", "Stop"}
          and all("command" in h and h["type"] == "command" for ev in hooks["anja"].values() for h in ev), str(hooks)[:300])
    check("hook 'lint' preesistente intatto", hooks.get("lint", {}).get("PreToolUse", [{}])[0].get("command") == "./lint.sh")
    mcp = json.loads((project / ".agents" / "mcp_config.json").read_text(encoding="utf-8"))
    check("anja_memory registrato accanto al server esistente", "anja_memory" in mcp["mcpServers"] and "other" in mcp["mcpServers"]
          and mcp["mcpServers"]["anja_memory"]["env"]["ANJA_ROOT"] == str(project.resolve()), str(mcp)[:300])
    before = (project / ".agents" / "hooks.json").read_text() + (project / ".agents" / "mcp_config.json").read_text()
    r = subprocess.run([PY, str(INSTALLER), "--project", str(project)], capture_output=True, text=True, timeout=30)
    after = (project / ".agents" / "hooks.json").read_text() + (project / ".agents" / "mcp_config.json").read_text()
    check("seconda esecuzione: nessuna modifica", before == after and "già presente" in r.stdout)

    print("§5 journal_policy")
    jp = _load(ROOT / "hooks" / "journal_policy.py")
    check("harness antigravity da ANTIGRAVITY_CONVERSATION_ID", jp.detect_harness({"ANTIGRAVITY_CONVERSATION_ID": "x"}) == "antigravity")
    check("agent_for antigravity", jp.agent_for("antigravity") == "cli-antigravity")
    sb = _load(ROOT / "scripts" / "summarize_session_bg.py")
    check("summarizer: harness antigravity → CLI agy", sb._HARNESS_BIN.get("antigravity") == "agy" and "agy" in sb._KNOWN_BINS)
    check("summarizer: comando agy headless", sb._command("/x/agy", "agy", "p", "haiku")[:3] == ["/x/agy", "-p", "p"] and "--output-format" in sb._command("/x/agy", "agy", "p", "haiku"))

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


def test_antigravity_adapter():
    main()


if __name__ == "__main__":
    main()
