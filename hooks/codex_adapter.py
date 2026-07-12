#!/usr/bin/env python3
"""Adapter lifecycle Codex → core hook Anja.

Codex salva transcript rollout JSONL diversi da Claude Code. Questo adapter li
normalizza al contratto consumato da ``session_end.py`` senza modificare il
percorso Claude: journal, parsing e rendering restano nel core condiviso.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


HOOKS_DIR = Path(__file__).resolve().parent
SESSION_END_PATH = HOOKS_DIR / "session_end.py"
POST_TOOL_USE_PATH = HOOKS_DIR / "post_tool_use.py"


def _read_payload() -> dict:
    try:
        value = json.loads(sys.stdin.read() or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _load_session_end():
    spec = importlib.util.spec_from_file_location("anja_session_end", SESSION_END_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("session_end.py not loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _nested(data: dict, *keys: str) -> Optional[Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _response_items_to_cc(raw_path: Path) -> tuple[list[dict], Optional[dict]]:
    """Converte il rollout locale Codex nel minimo JSONL Claude-compatible."""
    events: list[dict] = []
    session_meta: Optional[dict] = None
    pending_tools: list[dict] = []

    for raw in raw_path.open(encoding="utf-8", errors="replace"):
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = envelope.get("type")
        payload = envelope.get("payload") or {}
        timestamp = envelope.get("timestamp") or datetime.now(timezone.utc).isoformat()
        if kind == "session_meta" and isinstance(payload, dict):
            session_meta = payload
            continue
        if kind != "response_item" or not isinstance(payload, dict):
            continue

        item_type = payload.get("type")
        if item_type == "message":
            role = payload.get("role")
            if role == "user":
                text = _first_text(payload.get("content"))
                if text:
                    events.append({"timestamp": timestamp, "type": "user",
                                   "message": {"role": "user", "content": text}})
            elif role == "assistant":
                content = []
                text = _first_text(payload.get("content"))
                if text:
                    content.append({"type": "text", "text": text})
                content.extend(pending_tools)
                pending_tools = []
                if content:
                    events.append({"timestamp": timestamp, "type": "assistant",
                                   "message": {"role": "assistant", "content": content}})
        elif item_type in ("function_call", "custom_tool_call"):
            name = payload.get("name") or "tool"
            pending_tools.append({"type": "tool_use", "name": str(name)})

    if pending_tools:
        events.append({"timestamp": datetime.now(timezone.utc).isoformat(), "type": "assistant",
                       "message": {"role": "assistant", "content": pending_tools}})
    return events, session_meta


def _session_id(payload: dict, meta: Optional[dict]) -> str:
    candidates = (
        payload.get("session_id"), payload.get("sessionId"), payload.get("thread_id"),
        payload.get("threadId"), _nested(payload, "session", "id"),
        meta.get("session_id") if meta else None, meta.get("id") if meta else None,
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _rollout_workspace(path: Path) -> Optional[Path]:
    """Legge la cwd dal metadata di un rollout Codex."""
    try:
        with path.open(encoding="utf-8", errors="replace") as raw:
            for line in raw:
                envelope = json.loads(line)
                if envelope.get("type") != "session_meta":
                    continue
                cwd = _nested(envelope.get("payload") or {}, "cwd")
                if isinstance(cwd, str) and cwd:
                    return Path(cwd).expanduser().resolve()
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _rollout_path(payload: dict, workspace: Path) -> Optional[Path]:
    for key in ("transcript_path", "transcriptPath", "rollout_path", "rolloutPath"):
        value = payload.get(key)
        if isinstance(value, str) and Path(value).is_file():
            return Path(value)

    sid = _session_id(payload, None)
    if sid and any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in sid):
        return None
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    sessions_root = codex_home / "sessions"
    if sid:
        matches = sorted(sessions_root.rglob(f"*{sid}*.jsonl"), key=lambda p: p.stat().st_mtime)
        return matches[-1] if matches else None

    # Lo schema dello Stop hook non garantisce un session_id. In quel caso scegli
    # il rollout più recente della stessa workspace, mai quello di un altro progetto.
    for candidate in sorted(sessions_root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        if _rollout_workspace(candidate) == workspace:
            return candidate
    return None


def _workspace(payload: dict, meta: Optional[dict]) -> Path:
    for value in (payload.get("cwd"), payload.get("workspace_root"), meta.get("cwd") if meta else None):
        if isinstance(value, str) and value:
            return Path(value).expanduser().resolve()
    return Path.cwd().resolve()


def _write_normalized_transcript(project_root: Path, session_id: str, events: list[dict]) -> Path:
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")[:120] or "unknown"
    target_dir = project_root / ".anjawiki" / "transcripts" / "codex"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_id}.jsonl"
    target.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    return target


def session_end(payload: dict) -> int:
    workspace = _workspace(payload, None)
    rollout = _rollout_path(payload, workspace)
    if rollout is None:
        return 0
    events, meta = _response_items_to_cc(rollout)
    session_id = _session_id(payload, meta)
    workspace = _workspace(payload, meta)
    if not session_id or not events or not workspace.is_dir():
        return 0

    core = _load_session_end()
    found = core.find_anja_root(workspace)
    if found is None:
        return 0
    project_root, kind, sessions_root = found
    transcript = _write_normalized_transcript(project_root, session_id, events)
    transcript_info = core.parse_transcript(str(transcript))
    core.write_session_file(sessions_root, kind, {
        "session_id": session_id,
        "transcript_path": str(transcript),
        "hook_event_name": "Stop",
        "reason": payload.get("reason") or "codex-stop",
        "agent": "cli-codex",
    }, transcript_info)
    if kind == "project":
        core.spawn_bg_wiki_embed_check(project_root)
    return 0


def _normalize_post_tool_payload(payload: dict) -> dict:
    tool_input = payload.get("tool_input") or payload.get("input") or payload.get("arguments") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    name = payload.get("tool_name") or payload.get("tool") or payload.get("name") or ""
    if isinstance(name, dict):
        name = name.get("name") or name.get("type") or ""
    return {
        "tool_name": {"write": "Write", "edit": "Edit", "multiedit": "MultiEdit", "patch": "MultiEdit"}.get(
            str(name).lower(), str(name)),
        "tool_input": {
            "file_path": tool_input.get("file_path") or tool_input.get("filePath") or tool_input.get("path"),
        },
    }


def post_tool_use(payload: dict) -> int:
    normalized = _normalize_post_tool_payload(payload)
    if not normalized["tool_input"]["file_path"]:
        return 0
    result = subprocess.run([sys.executable, str(POST_TOOL_USE_PATH)], input=json.dumps(normalized),
                            text=True, cwd=str(Path.cwd()), check=False, timeout=5)
    return result.returncode


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("stop", "post-tool-use"):
        print("usage: codex_adapter.py stop|post-tool-use", file=sys.stderr)
        return 2
    payload = _read_payload()
    return session_end(payload) if sys.argv[1] == "stop" else post_tool_use(payload)


if __name__ == "__main__":
    sys.exit(main())
