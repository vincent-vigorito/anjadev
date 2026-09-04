#!/usr/bin/env python3
"""Adapter lifecycle Antigravity CLI (`agy`) → core hook Anja.

Validato sul campo (agy 1.1.26, 2026-09-04). Antigravity espone 5 eventi in
`.agents/hooks.json` (formato piatto: `{"anja": {"Evento": [{"type":"command",...}]}}`),
payload JSON su stdin in camelCase, nome dell'evento NON nel payload → passato come argv[1].
Gli hook girano con cwd = `<workspace>/.agents/`: la root del progetto è `workspacePaths[0]`.

  pre-invocation  invocationNum == 0 → session_start.py; il suo stdout torna ad agy come
                  `{"injectSteps": [{"ephemeralMessage": ...}]}` (contesto di sessione:
                  log recenti, focus roadmap, catalogo skill, pending steward).
  stop            → transcript `transcript_full.jsonl` (step USER_INPUT / PLANNER_RESPONSE)
                  normalizzato al JSONL Claude-compatible, poi session_end core: journal
                  upsert per conversationId, auto-summary bg, consistency check embedding.
  post-tool-use   write_to_file / replace_file_content → post_tool_use.py (re-embed pagina
                  wiki). NB: in agy 1.1.26 headless gli hook sui tool non scattano; il
                  consistency check a Stop copre comunque le pagine sporche.

Stdout è riservato alla risposta JSON per agy: tutto il resto va su stderr.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

HOOKS_DIR = Path(__file__).resolve().parent
SESSION_START_PATH = HOOKS_DIR / "session_start.py"
SESSION_END_PATH = HOOKS_DIR / "session_end.py"
POST_TOOL_USE_PATH = HOOKS_DIR / "post_tool_use.py"
HARNESS = "antigravity"
AGENT = "cli-antigravity"
EVENTS = ("pre-invocation", "post-tool-use", "stop")

_WRITE_TOOLS = {"write_to_file": "Write", "replace_file_content": "Edit",
                "multi_replace_file_content": "MultiEdit", "create_file": "Write"}
_USER_WRAPPER_RE = re.compile(r"</?USER_REQUEST>|</?CONTEXT>", re.I)


def _read_payload() -> dict:
    try:
        value = json.loads(sys.stdin.read() or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{path.name} not loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workspace(payload: dict) -> Path:
    paths = payload.get("workspacePaths")
    if isinstance(paths, list):
        for p in paths:
            if isinstance(p, str) and p and Path(p).is_dir():
                return Path(p).resolve()
    env_root = os.environ.get("ANJA_ROOT")
    if env_root and Path(env_root).is_dir():
        return Path(env_root).resolve()
    cwd = Path.cwd().resolve()
    return cwd.parent if cwd.name == ".agents" else cwd


def _env() -> dict:
    env = dict(os.environ)
    env["ANJA_HARNESS"] = HARNESS
    return env


def _arg(args: dict, *keys: str) -> Optional[str]:
    """Gli args dei tool agy arrivano spesso come stringhe JSON-encoded ('"path"')."""
    for k in keys:
        v = args.get(k)
        if isinstance(v, str) and v:
            if v.startswith('"'):
                try:
                    v = json.loads(v)
                except json.JSONDecodeError:
                    v = v.strip('"')
            if isinstance(v, str) and v:
                return v
    return None


# ---------------------------------------------------------------- transcript

def _steps_to_cc(path: Path) -> list[dict]:
    """transcript_full.jsonl (step agy) → eventi JSONL Claude-compatible."""
    events: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events
    for raw in lines:
        try:
            step = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(step, dict):
            continue
        kind = step.get("type")
        ts = step.get("created_at") or ""
        if kind == "USER_INPUT":
            text = _USER_WRAPPER_RE.sub("", str(step.get("content") or "")).strip()
            if text:
                events.append({"timestamp": ts, "type": "user",
                               "message": {"role": "user", "content": text}})
        elif kind == "PLANNER_RESPONSE":
            content: list[dict] = []
            text = step.get("content")
            if isinstance(text, str) and text.strip():
                content.append({"type": "text", "text": text.strip()})
            for tc in step.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("name"):
                    content.append({"type": "tool_use", "name": str(tc["name"])})
            if content:
                events.append({"timestamp": ts, "type": "assistant",
                               "message": {"role": "assistant", "content": content}})
    return events


def _transcript_path(payload: dict) -> Optional[Path]:
    for key in ("transcriptPath", "transcript_path"):
        v = payload.get(key)
        if isinstance(v, str) and Path(v).is_file():
            return Path(v)
    art = payload.get("artifactDirectoryPath")
    if isinstance(art, str):
        for name in ("transcript_full.jsonl", "transcript.jsonl"):
            p = Path(art) / ".system_generated" / "logs" / name
            if p.is_file():
                return p
    return None


def _conversation_id(payload: dict) -> str:
    for key in ("conversationId", "conversation_id", "sessionId", "session_id"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return (os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "").strip()


def _write_normalized(project_root: Path, session_id: str, events: list[dict]) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:120] or "unknown"
    d = project_root / ".anjawiki" / "transcripts" / HARNESS
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{safe}.jsonl"
    target.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return target


# ---------------------------------------------------------------- eventi

def pre_invocation(payload: dict) -> int:
    if int(payload.get("invocationNum") or 0) != 0:
        print("{}")
        return 0
    workspace = _workspace(payload)
    try:
        r = subprocess.run([sys.executable, str(SESSION_START_PATH)], input="{}", text=True,
                           capture_output=True, cwd=str(workspace), env=_env(), timeout=15)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[anja] antigravity pre-invocation: session_start failed: {e}", file=sys.stderr)
        print("{}")
        return 0
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr)
    text = r.stdout.strip()
    if not text:
        print("{}")
        return 0
    print(json.dumps({"injectSteps": [{"ephemeralMessage": text}]}, ensure_ascii=False))
    return 0


def stop(payload: dict) -> int:
    print("{}")   # risposta per agy: comportamento di default (nessun "continue")
    sys.stdout.flush()
    workspace = _workspace(payload)
    transcript = _transcript_path(payload)
    session_id = _conversation_id(payload)
    if transcript is None or not session_id or not workspace.is_dir():
        return 0
    events = _steps_to_cc(transcript)
    if not events:
        return 0
    core = _load("anja_session_end", SESSION_END_PATH)
    found = core.find_anja_root(workspace)
    if found is None:
        return 0
    project_root, kind, sessions_root = found
    os.environ["ANJA_HARNESS"] = HARNESS
    # il core stampa diagnostica: mai su stdout (è la risposta JSON per agy)
    with contextlib.redirect_stdout(sys.stderr):
        normalized = _write_normalized(project_root, session_id, events)
        info = core.parse_transcript(str(normalized))
        duration = core._duration_seconds(info.get("started") or "", info.get("ended") or "")
        why = core.journal_policy.is_programmatic(os.environ, "", len(info.get("user_messages", [])),
                                                  duration, payload.get("terminationReason") or "")
        if why:
            print(f"[anja] journal skipped ({why})", file=sys.stderr)
            return 0
        session_file = core.write_session_file(sessions_root, kind, {
            "session_id": session_id,
            "transcript_path": str(normalized),
            "hook_event_name": "Stop",
            "reason": payload.get("terminationReason") or "agy-stop",
            "agent": AGENT,
            "harness": HARNESS,
        }, info)
        print(f"[anja] Session file ({kind}) → {session_file.relative_to(project_root)}", file=sys.stderr)
        core.spawn_bg_summarize(session_file, info, duration)
        if kind == "project":
            core.spawn_bg_wiki_embed_check(project_root)
    return 0


def _normalize_post_tool_payload(payload: dict) -> dict:
    call = payload.get("toolCall") or payload.get("tool_call") or {}
    if not isinstance(call, dict):
        call = {}
    name = str(call.get("name") or payload.get("tool_name") or "")
    args = call.get("args") or payload.get("tool_input") or {}
    if not isinstance(args, dict):
        args = {}
    return {"tool_name": _WRITE_TOOLS.get(name, name),
            "tool_input": {"file_path": _arg(args, "TargetFile", "AbsolutePath", "file_path", "path")}}


def post_tool_use(payload: dict) -> int:
    print("{}")
    sys.stdout.flush()
    normalized = _normalize_post_tool_payload(payload)
    if normalized["tool_name"] not in ("Write", "Edit", "MultiEdit") or not normalized["tool_input"]["file_path"]:
        return 0
    try:
        subprocess.run([sys.executable, str(POST_TOOL_USE_PATH)], input=json.dumps(normalized), text=True,
                       cwd=str(_workspace(payload)), env=_env(), check=False, timeout=5,
                       stdout=sys.stderr)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[anja] antigravity post-tool-use: {e}", file=sys.stderr)
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in EVENTS:
        print("usage: antigravity_adapter.py pre-invocation|post-tool-use|stop", file=sys.stderr)
        print("{}")
        return 0   # mai far fallire agy per un errore d'uso dell'hook
    payload = _read_payload()
    handler = {"pre-invocation": pre_invocation, "stop": stop, "post-tool-use": post_tool_use}[sys.argv[1]]
    try:
        return handler(payload)
    except Exception as e:  # best-effort: un hook rotto non deve bloccare la sessione
        print(f"[anja] antigravity {sys.argv[1]}: {type(e).__name__}: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
