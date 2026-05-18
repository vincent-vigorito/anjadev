#!/usr/bin/env python3
"""mcp_office_ops.py — Office Operations MCP server (AnjaOps Tier 1+2+3).

Scope-aware: il caller (env ANJA_SCOPE) determina cosa può fare.
- scope='hub'              → T1 read cross-workspace + T3 bridge (workspace.task)
- scope='workspace:<name>' → T1 read locale + T2 write locale (agent/script/routine/goal)

Tool categorie:
- **Diagnostica (T1, read)**: aggregator stato goal/specialist/script/executions/signals
- **Lifecycle (T2, write)**: modifica config agent del workspace, start/stop script,
  enable/disable routine, riassegna ruoli del team goal
- **Bridge (T3, hub-only)**: chiede a Anja-responsabile di workspace X di fare task

Stdlib only.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCOPE = os.environ.get("ANJA_SCOPE", "hub")
ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()

# Per workspace scope: ROOT è <workspace>/.anjawiki. HUB sta 3 livelli su.
# Per hub scope: ROOT è il hub direttamente.
if SCOPE.startswith("workspace:"):
    # ROOT = <hub>/workspaces/<name>/.anjawiki
    # ROOT.parent = <hub>/workspaces/<name>
    # ROOT.parent.parent = <hub>/workspaces
    # ROOT.parent.parent.parent = <hub>
    WORKSPACE_NAME = ROOT.parent.name
    HUB_ROOT = ROOT.parent.parent.parent
else:
    WORKSPACE_NAME = None
    HUB_ROOT = ROOT


def _is_hub() -> bool:
    return SCOPE == "hub" or SCOPE == ""


def _scope_goal_dir(scope: str, goal_id: str) -> Path:
    """Risolvi la dir del goal in base allo scope target."""
    if scope == "hub":
        return HUB_ROOT / "goals" / goal_id
    if scope.startswith("workspace:"):
        ws = scope.split(":", 1)[1]
        return HUB_ROOT / "workspaces" / ws / ".anjawiki" / "goals" / goal_id
    return HUB_ROOT / "goals" / goal_id


def _resolve_target_scope(provided: Optional[str]) -> str:
    """Se hub scope: usa scope provided o 'hub'. Se workspace scope: forza locked al proprio."""
    if _is_hub():
        return provided or "hub"
    # Workspace caller può solo operare sul proprio scope
    return SCOPE


def _list_all_goals_meta() -> list[dict]:
    """Lista tutti i goal cross-scope (solo per hub caller). Workspace caller vede solo i suoi."""
    out: list[dict] = []
    scopes = []
    if _is_hub():
        scopes.append("hub")
        ws_root = HUB_ROOT / "workspaces"
        if ws_root.is_dir():
            for ws in sorted(ws_root.iterdir()):
                if ws.is_dir():
                    scopes.append(f"workspace:{ws.name}")
    else:
        scopes.append(SCOPE)

    for sc in scopes:
        if sc == "hub":
            goals_root = HUB_ROOT / "goals"
        else:
            goals_root = HUB_ROOT / "workspaces" / sc.split(":", 1)[1] / ".anjawiki" / "goals"
        if not goals_root.is_dir():
            continue
        for gdir in sorted(goals_root.iterdir()):
            if not gdir.is_dir() or gdir.name.startswith("."):
                continue
            gmd = gdir / "goal.md"
            if not gmd.is_file():
                continue
            out.append({
                "id": gdir.name,
                "scope": sc,
                "path": str(gdir),
            })
    return out


def _read_jsonl_tail(path: Path, limit: int = 50) -> list[dict]:
    """Lettura tail di jsonl, una entry per riga."""
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _read_goal_meta(goal_path: Path) -> dict:
    """Leggi frontmatter goal.md (parser semplificato)."""
    gmd = goal_path / "goal.md"
    if not gmd.is_file():
        return {}
    try:
        text = gmd.read_text(encoding="utf-8")
    except Exception:
        return {}
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    meta_raw = m.group(1)
    meta: dict = {}
    cur_list_key: Optional[str] = None
    for raw in meta_raw.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            cur_list_key = None
            continue
        if cur_list_key and line.startswith("  - "):
            meta.setdefault(cur_list_key, []).append(line[4:].strip())
            continue
        if ":" not in line:
            cur_list_key = None
            continue
        cur_list_key = None
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not val:
            cur_list_key = key
            meta[key] = []
            continue
        if val.startswith("{") and val.endswith("}"):
            try:
                meta[key] = json.loads(val); continue
            except Exception: pass
        if val.startswith("[") and val.endswith("]"):
            try:
                meta[key] = json.loads(val); continue
            except Exception: pass
        if val.lower() in ("true", "false"):
            meta[key] = val.lower() == "true"; continue
        if re.match(r"^-?\d+$", val):
            meta[key] = int(val); continue
        if (val.startswith('"') and val.endswith('"')):
            meta[key] = val[1:-1]; continue
        meta[key] = val
    return meta


# =================================================================
# T1 — Diagnostica read-only
# =================================================================

def tool_diagnose(args: dict) -> dict:
    """Aggregator overview di un goal (o tutti i goal visibili al caller).

    args: { goal_id?, scope? (only hub caller), days?=1 }
    Output strutturato: run_stats, error_patterns, specialist_health, signals_summary,
    executions_summary, scripts_status, kanban_open, suggestions_pending.
    """
    goal_id = args.get("goal_id") or ""
    target_scope = _resolve_target_scope(args.get("scope"))
    days = int(args.get("days", 1) or 1)
    cutoff_ts = time.time() - (days * 86400)

    goals_to_inspect: list[dict] = []
    if goal_id:
        gpath = _scope_goal_dir(target_scope, goal_id)
        if not gpath.is_dir():
            return {"error": f"goal '{goal_id}' not found in scope '{target_scope}'"}
        goals_to_inspect.append({"id": goal_id, "scope": target_scope, "path": str(gpath)})
    else:
        goals_to_inspect = _list_all_goals_meta()

    reports = []
    for g in goals_to_inspect:
        gpath = Path(g["path"])
        meta = _read_goal_meta(gpath)
        activity = _read_jsonl_tail(gpath / "activity.jsonl", limit=500)
        recent_activity = [e for e in activity if _parse_iso_ts(e.get("ts_iso", "")) > cutoff_ts]

        # Run stats
        starts = [e for e in recent_activity if e.get("event_type") == "pipeline_start"]
        ends = [e for e in recent_activity if e.get("event_type") == "pipeline_end"]
        errors = [e for e in recent_activity if e.get("level") == "error"]
        timeouts = [e for e in recent_activity if e.get("event_type") == "timeout"]

        # Verdict trend
        verdicts = [e for e in recent_activity if e.get("event_type") == "verdict"]
        verdict_counts: dict = {}
        for v in verdicts:
            payload = v.get("payload") or {}
            # verdict è in msg "verdict: drift" oppure in payload
            txt = v.get("msg", "")
            for vt in ["on_track", "drift", "blocked", "achieved", "failed"]:
                if vt in txt:
                    verdict_counts[vt] = verdict_counts.get(vt, 0) + 1
                    break

        # Specialist health (chi è completato e quanto)
        specialist_health: dict = {}
        for role in ("analyst", "risk-officer", "dev", "executor", "researcher"):
            starts_r = [e for e in recent_activity if e.get("event_type") == "specialist_start" and e.get("agent") == role]
            ends_r = [e for e in recent_activity if e.get("event_type") == "specialist_end" and e.get("agent") == role]
            specialist_health[role] = {
                "starts": len(starts_r),
                "ends": len(ends_r),
                "hanging": len(starts_r) - len(ends_r),
                "last_status": ends_r[-1].get("msg", "")[:80] if ends_r else None,
            }

        # Signals summary
        signals = _read_jsonl_tail(gpath / "signals.jsonl", limit=200)
        signal_counts: dict = {}
        for s in signals:
            et = s.get("event_type", "?")
            signal_counts[et] = signal_counts.get(et, 0) + 1

        # Executions summary
        executions = _read_jsonl_tail(gpath / "executions.jsonl", limit=100)
        exec_errors = [e for e in executions if e.get("status") == "failed" or e.get("error")]
        exec_success = [e for e in executions if e.get("status") == "ok"]
        exec_error_patterns: dict = {}
        for e in exec_errors:
            err = (e.get("error", "") or "")[:80]
            if err:
                exec_error_patterns[err] = exec_error_patterns.get(err, 0) + 1

        # Pending actions
        pending = _read_jsonl_tail(gpath / "pending_actions.jsonl", limit=100)
        pending_by_id: dict = {}
        for p in pending:
            pid = p.get("id")
            if pid:
                pending_by_id[pid] = p
        pending_open = [p for p in pending_by_id.values() if p.get("status") == "pending"]

        reports.append({
            "goal_id": g["id"],
            "scope": g["scope"],
            "title": meta.get("title", ""),
            "status": meta.get("status", ""),
            "autonomy_level": meta.get("autonomy_level", 1),
            "responsabile": meta.get("responsabile", ""),
            "pipeline_cron": meta.get("pipeline_cron", ""),
            "judge_cron": meta.get("judge_cron", ""),
            "run_stats": {
                "pipeline_starts": len(starts),
                "pipeline_ends": len(ends),
                "incomplete": len(starts) - len(ends),
                "errors": len(errors),
                "timeouts": len(timeouts),
                "verdicts_by_type": verdict_counts,
            },
            "specialist_health": specialist_health,
            "signals": {
                "total": len(signals),
                "by_type": signal_counts,
            },
            "executions": {
                "total": len(executions),
                "success": len(exec_success),
                "failed": len(exec_errors),
                "error_patterns": exec_error_patterns,
            },
            "pending_actions_open": len(pending_open),
        })

    # Cross-goal script status
    script_status = tool_script_status({})

    # Diagnose summary patterns (suggerimenti automatici di sintesi)
    issues = []
    for r in reports:
        if r["run_stats"]["incomplete"] > 0:
            issues.append(f"{r['goal_id']}: {r['run_stats']['incomplete']} pipeline incomplete (hanging specialist)")
        if r["executions"]["failed"] > 0 and r["executions"]["error_patterns"]:
            top_err = max(r["executions"]["error_patterns"].items(), key=lambda x: x[1])
            issues.append(f"{r['goal_id']}: executions failing — pattern '{top_err[0]}' ({top_err[1]}x)")
        drift = r["run_stats"]["verdicts_by_type"].get("drift", 0)
        if drift >= 3:
            issues.append(f"{r['goal_id']}: {drift} drift verdicts in last {days}d — review strategy")
        for role, h in r["specialist_health"].items():
            if h["hanging"] > 0:
                issues.append(f"{r['goal_id']}: {role} has {h['hanging']} hanging runs (specialist not completing)")
    return {
        "caller_scope": SCOPE,
        "window_days": days,
        "goals_inspected": len(reports),
        "reports": reports,
        "scripts": script_status.get("scripts", []),
        "issues_detected": issues,
    }


def _parse_iso_ts(s: str) -> float:
    if not s:
        return 0.0
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def tool_notes_recent(args: dict) -> dict:
    """Leggi le notes specialist degli ultimi N run del goal.

    args: { goal_id, scope?, role?, limit?=10 }
    """
    goal_id = (args.get("goal_id") or "").strip()
    if not goal_id:
        return {"error": "goal_id required"}
    target_scope = _resolve_target_scope(args.get("scope"))
    role_filter = (args.get("role") or "").lower()
    limit = int(args.get("limit", 10) or 10)
    gpath = _scope_goal_dir(target_scope, goal_id)
    notes_dir = gpath / "notes"
    if not notes_dir.is_dir():
        return {"notes": [], "count": 0}
    files = sorted(notes_dir.glob("*.md"), reverse=True)
    notes = []
    for f in files:
        if role_filter and role_filter not in f.name.lower():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        # Estrai output JSON dal frontmatter (parser veloce)
        m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        meta = {}
        if m:
            for line in m.group(1).split("\n"):
                if ": " in line:
                    k, _, v = line.partition(": ")
                    k = k.strip()
                    v = v.strip()
                    if v.startswith("{") and v.endswith("}"):
                        try: meta[k] = json.loads(v)
                        except: meta[k] = v
                    else:
                        meta[k] = v.strip('"').strip("'")
        notes.append({
            "filename": f.name,
            "role": meta.get("role", ""),
            "agent": meta.get("agent", ""),
            "ts": meta.get("ts", ""),
            "run_id": meta.get("run_id", ""),
            "llm": meta.get("llm", {}),
            "output": meta.get("output", {}),
        })
        if len(notes) >= limit:
            break
    return {"notes": notes, "count": len(notes), "goal_id": goal_id, "scope": target_scope}


def tool_signals_recent(args: dict) -> dict:
    """Tail signals.jsonl di un goal."""
    goal_id = (args.get("goal_id") or "").strip()
    if not goal_id:
        return {"error": "goal_id required"}
    target_scope = _resolve_target_scope(args.get("scope"))
    limit = int(args.get("limit", 30) or 30)
    gpath = _scope_goal_dir(target_scope, goal_id)
    signals = _read_jsonl_tail(gpath / "signals.jsonl", limit=limit)
    return {"signals": signals, "count": len(signals), "goal_id": goal_id, "scope": target_scope}


def tool_executions_recent(args: dict) -> dict:
    """Tail executions.jsonl con errori inclusi (audit L3)."""
    goal_id = (args.get("goal_id") or "").strip()
    if not goal_id:
        return {"error": "goal_id required"}
    target_scope = _resolve_target_scope(args.get("scope"))
    limit = int(args.get("limit", 20) or 20)
    gpath = _scope_goal_dir(target_scope, goal_id)
    execs = _read_jsonl_tail(gpath / "executions.jsonl", limit=limit)
    # Aggrega errori
    errors = [e for e in execs if e.get("status") == "failed" or e.get("error")]
    return {
        "executions": execs,
        "count": len(execs),
        "errors_count": len(errors),
        "goal_id": goal_id, "scope": target_scope,
    }


def tool_script_status(args: dict) -> dict:
    """Lista script attivi del runtime supervisor. Filtra per workspace caller se scope-locked."""
    state_file = HUB_ROOT / "scripts" / ".runtime_state.json"
    if not state_file.is_file():
        return {"scripts": [], "count": 0}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {"scripts": [], "count": 0}

    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    scripts = []
    for path_str, info in state.items():
        # Scope filtering: workspace caller vede solo i suoi script
        if not _is_hub() and info.get("scope") != SCOPE:
            continue
        # hub caller può filtrare per scope se passed
        scope_filter = args.get("scope")
        if scope_filter and info.get("scope") != scope_filter:
            continue
        pid = info.get("pid", 0)
        scripts.append({
            "path": path_str,
            "filename": Path(path_str).name,
            "goal_id": info.get("goal_id", ""),
            "scope": info.get("scope", ""),
            "pid": pid,
            "alive": _pid_alive(pid),
            "disabled": info.get("disabled", False),
            "restarts": info.get("restarts", 0),
            "started_at": info.get("started_at", ""),
            "last_error": info.get("last_error", ""),
        })
    return {"scripts": scripts, "count": len(scripts)}


def tool_pending_actions(args: dict) -> dict:
    """Lista pending actions di un goal (qualsiasi status, default solo pending)."""
    goal_id = (args.get("goal_id") or "").strip()
    if not goal_id:
        return {"error": "goal_id required"}
    target_scope = _resolve_target_scope(args.get("scope"))
    status_filter = (args.get("status") or "pending").lower()
    gpath = _scope_goal_dir(target_scope, goal_id)
    records = _read_jsonl_tail(gpath / "pending_actions.jsonl", limit=200)
    # Idempotente per id: l'ultimo write vince
    by_id: dict = {}
    for r in records:
        rid = r.get("id")
        if rid:
            by_id[rid] = r
    actions = list(by_id.values())
    if status_filter and status_filter != "all":
        actions = [a for a in actions if a.get("status") == status_filter]
    actions.sort(key=lambda a: a.get("created_at", ""), reverse=True)
    return {"actions": actions, "count": len(actions), "goal_id": goal_id, "scope": target_scope}


# =================================================================
# T2 — Write tools (scope-locked al workspace caller)
# =================================================================

def _require_workspace_scope() -> Optional[dict]:
    """Restituisce error dict se caller non è workspace. Usa nei write tool."""
    if _is_hub():
        return {"error": "REFUSED: questa operazione richiede scope=workspace:<name> (sei in scope hub)."}
    return None


def tool_agent_update(args: dict) -> dict:
    """Modifica un file di config di un agent del workspace caller.

    args: { name, file (AGENTS.md|SOUL.md|TOOLS.md|config.json), content, mode='replace'|'append' }
    """
    err = _require_workspace_scope()
    if err: return err
    name = (args.get("name") or "").strip()
    file = (args.get("file") or "").strip()
    content = args.get("content", "")
    mode = (args.get("mode") or "replace").lower()
    if not name or not file or content is None:
        return {"error": "name + file + content required"}
    if mode not in ("replace", "append"):
        return {"error": "mode must be 'replace' or 'append'"}
    ALLOWED_FILES = {"AGENTS.md", "SOUL.md", "TOOLS.md", "CLAUDE.md", "config.json"}
    if file not in ALLOWED_FILES:
        return {"error": f"file '{file}' not whitelisted (allowed: {sorted(ALLOWED_FILES)})"}
    # ROOT è già <workspace>/.anjawiki
    agent_dir = ROOT / "agents" / name
    if not agent_dir.is_dir():
        return {"error": f"agent '{name}' not found in workspace '{WORKSPACE_NAME}'"}
    target = agent_dir / file
    try:
        if mode == "append" and target.is_file():
            existing = target.read_text(encoding="utf-8")
            new_content = existing + "\n" + content if not existing.endswith("\n") else existing + content
        else:
            new_content = content
        target.write_text(new_content, encoding="utf-8")
        size = target.stat().st_size
        return {
            "ok": True, "path": str(target), "size_bytes": size,
            "agent": name, "file": file, "mode": mode,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_script_lifecycle(args: dict) -> dict:
    """Lifecycle di un monitor script. args: { script_path, action ('start'|'stop'|'restart'|'reset') }"""
    err = _require_workspace_scope()
    if err: return err
    script_path = (args.get("script_path") or "").strip()
    action = (args.get("action") or "").lower()
    if not script_path or not action:
        return {"error": "script_path + action required"}
    if action not in ("start", "stop", "restart", "reset"):
        return {"error": "action must be start|stop|restart|reset"}
    # Import script_runtime (helper webapp)
    # Prova convenzione monorepo, poi env ANJA_HUB_WEBAPP
    candidates = [
        HUB_ROOT.parent / "anja-hub" / "webapp",
        HUB_ROOT.parent / "llm-wiki" / "anja-hub" / "webapp",  # legacy
    ]
    env_path = os.environ.get("ANJA_HUB_WEBAPP")
    if env_path:
        candidates.insert(0, Path(env_path).expanduser().resolve())
    sr = None
    for c in candidates:
        if c.is_dir():
            sys.path.insert(0, str(c))
            try:
                import script_runtime as sr  # noqa
                break
            except Exception:
                continue
    if sr is None:
        return {"error": "script_runtime unavailable", "hint": "this tool requires the anja-hub webapp (set ANJA_HUB_WEBAPP env)"}

    p = Path(script_path)
    if not p.is_absolute():
        p = HUB_ROOT / script_path
    if not p.is_file():
        return {"error": f"script not found: {p}"}

    if action == "start":
        # Need goal_id from state or derive from path
        state = sr._load_state(HUB_ROOT)
        info = state.get(str(p)) or {}
        gid = info.get("goal_id") or args.get("goal_id")
        sc = info.get("scope") or SCOPE
        if not gid:
            return {"error": "goal_id required (or script must be already in state)"}
        return sr.start_script(HUB_ROOT, sc, gid, p)
    if action == "stop":
        return sr.stop_script(HUB_ROOT, p)
    if action == "restart":
        sr.stop_script(HUB_ROOT, p)
        time.sleep(0.5)
        state = sr._load_state(HUB_ROOT)
        info = state.get(str(p)) or {}
        gid = info.get("goal_id") or args.get("goal_id")
        sc = info.get("scope") or SCOPE
        if not gid:
            return {"error": "goal_id required for restart"}
        return sr.start_script(HUB_ROOT, sc, gid, p)
    if action == "reset":
        return sr.reset_script(HUB_ROOT, p)
    return {"error": "unreachable"}


def tool_routine_lifecycle(args: dict) -> dict:
    """Routine lifecycle. args: { name, action ('enable'|'disable'|'run_now') }"""
    err = _require_workspace_scope()
    if err: return err
    name = (args.get("name") or "").strip()
    action = (args.get("action") or "").lower()
    if not name or not action:
        return {"error": "name + action required"}
    if action not in ("enable", "disable", "run_now"):
        return {"error": "action must be enable|disable|run_now"}
    # Routines vivono in <workspace>/.anjawiki/routines/<name>.yaml
    routine_file = ROOT / "routines" / f"{name}.yaml"
    if not routine_file.is_file():
        return {"error": f"routine '{name}' not found in {routine_file}"}
    try:
        content = routine_file.read_text(encoding="utf-8")
        if action == "enable":
            content = re.sub(r"^enabled:\s*\w+", "enabled: true", content, flags=re.M)
            if "enabled:" not in content:
                content = "enabled: true\n" + content
            routine_file.write_text(content)
            return {"ok": True, "name": name, "action": action, "enabled": True}
        if action == "disable":
            content = re.sub(r"^enabled:\s*\w+", "enabled: false", content, flags=re.M)
            if "enabled:" not in content:
                content = "enabled: false\n" + content
            routine_file.write_text(content)
            return {"ok": True, "name": name, "action": action, "enabled": False}
        if action == "run_now":
            # Touch file con timestamp per forzare next tick scheduler
            # (workaround senza tool dedicato)
            return {"ok": True, "name": name, "action": action,
                    "note": "run_now flag: il routine daemon eseguirà al prossimo tick. Per fire immediato usa il bottone UI."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"error": "unreachable"}


def tool_goal_assign_agent(args: dict) -> dict:
    """Riassegna ruolo/agent in un goal. args: { goal_id, role, agent, llm? }"""
    err = _require_workspace_scope()
    if err: return err
    goal_id = (args.get("goal_id") or "").strip()
    role = (args.get("role") or "").strip()
    agent = (args.get("agent") or "").strip()
    llm = args.get("llm") or None
    if not goal_id or not role or not agent:
        return {"error": "goal_id + role + agent required"}
    # Resolve webapp anja-hub: env ANJA_HUB_WEBAPP, poi convenzione monorepo
    env_path = os.environ.get("ANJA_HUB_WEBAPP")
    candidates = []
    if env_path:
        candidates.append(Path(env_path).expanduser().resolve())
    candidates.append(HUB_ROOT.parent / "anja-hub" / "webapp")
    goal_io = None
    for c in candidates:
        if c.is_dir():
            sys.path.insert(0, str(c))
            try:
                import goal_io  # noqa
                break
            except Exception:
                continue
    if goal_io is None:
        return {"error": "goal_io unavailable", "hint": "this tool requires the anja-hub webapp (set ANJA_HUB_WEBAPP env)"}
    g = goal_io.read_goal(HUB_ROOT, SCOPE, goal_id)
    if not g:
        return {"error": f"goal '{goal_id}' not found in scope '{SCOPE}'"}
    meta = g["meta"]
    if role == "responsabile":
        updates = {"responsabile": agent}
        if llm:
            updates["responsabile_llm"] = llm
        return goal_io.update_goal(HUB_ROOT, SCOPE, goal_id, updates)
    if role == "escalation" or role == "ceo":
        updates = {"escalation_to": agent}
        if llm:
            updates["escalation_llm"] = llm
        return goal_io.update_goal(HUB_ROOT, SCOPE, goal_id, updates)
    # Specialist: modifica assigned_agents
    assigned = list(meta.get("assigned_agents") or [])
    found = False
    for a in assigned:
        if (a.get("role") or "").lower() == role.lower():
            a["agent"] = agent
            if llm:
                a["llm"] = llm
            found = True
            break
    if not found:
        new_entry = {"role": role, "agent": agent, "cadence": "on_demand"}
        if llm:
            new_entry["llm"] = llm
        assigned.append(new_entry)
    return goal_io.update_goal(HUB_ROOT, SCOPE, goal_id, {"assigned_agents": assigned})


# =================================================================
# T3 — Hub bridge (hub-only)
# =================================================================

def tool_workspace_task(args: dict) -> dict:
    """Hub → workspace responsabile: chiedi a Anja-responsabile di workspace X di fare task.

    args: { target_workspace, prompt, focus? }
    Restituisce un descrittore della richiesta; l'invocazione effettiva è async via webapp.
    """
    if not _is_hub():
        return {"error": "REFUSED: workspace.task disponibile solo da scope=hub (sei in workspace)"}
    target = (args.get("target_workspace") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    if not target or not prompt:
        return {"error": "target_workspace + prompt required"}
    ws_root = HUB_ROOT / "workspaces" / target / ".anjawiki"
    if not ws_root.is_dir():
        return {"error": f"workspace '{target}' not found"}
    # Persiste richiesta in <workspace>/.anjawiki/inbox/tasks.jsonl
    inbox_dir = ws_root / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_file = inbox_dir / "tasks.jsonl"
    import secrets as _s
    record = {
        "id": f"task_{int(time.time())}_{_s.token_hex(3)}",
        "from": "anja-ceo",
        "to_workspace": target,
        "prompt": prompt,
        "focus": args.get("focus"),
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    try:
        with open(inbox_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {
            "ok": True, "task_id": record["id"], "inbox_path": str(inbox_file),
            "note": f"Task inoltrato a workspace '{target}'. Il responsabile lo processerà.",
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_workspace_diagnose_request(args: dict) -> dict:
    """Hub chiede al manager workspace di fare diagnose + report."""
    if not _is_hub():
        return {"error": "REFUSED: diagnose_request solo da scope=hub"}
    target = (args.get("target_workspace") or "").strip()
    focus = (args.get("focus") or "")
    if not target:
        return {"error": "target_workspace required"}
    return tool_workspace_task({
        "target_workspace": target,
        "prompt": f"Esegui diagnose del tuo workspace e produci report. Focus: {focus or 'general health check'}",
        "focus": focus,
    })


def tool_workspace_list_tasks(args: dict) -> dict:
    """Lista task assegnati al workspace caller (inbox). Utile per il responsabile per checklist."""
    if _is_hub():
        # Hub vede tutti i task assegnati a workspaces
        out = []
        ws_root = HUB_ROOT / "workspaces"
        if ws_root.is_dir():
            for ws in sorted(ws_root.iterdir()):
                if not ws.is_dir():
                    continue
                inbox_file = ws / ".anjawiki" / "inbox" / "tasks.jsonl"
                records = _read_jsonl_tail(inbox_file, limit=100)
                by_id: dict = {}
                for r in records:
                    rid = r.get("id")
                    if rid: by_id[rid] = r
                for t in by_id.values():
                    t["target_workspace"] = ws.name
                    out.append(t)
        return {"tasks": out, "count": len(out)}
    # Workspace caller: solo la sua inbox
    inbox_file = ROOT / "inbox" / "tasks.jsonl"
    records = _read_jsonl_tail(inbox_file, limit=100)
    by_id: dict = {}
    for r in records:
        rid = r.get("id")
        if rid: by_id[rid] = r
    return {"tasks": list(by_id.values()), "count": len(by_id)}


# =================================================================
# Tool registry
# =================================================================

TOOLS = [
    # T1 — Read
    {
        "name": "office.diagnose",
        "description": ("Aggregator overview del goal (run stats, verdict trend, specialist health, "
                        "signals, executions, scripts, pending). Senza goal_id ritorna report cross-goal."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "scope": {"type": "string", "description": "hub o workspace:<name>. Workspace caller forza al proprio scope."},
                "days": {"type": "integer", "default": 1, "description": "window di analisi"},
            },
        },
    },
    {
        "name": "office.notes_recent",
        "description": "Leggi le notes specialist di un goal (analyst/risk-officer/dev/executor/researcher).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "scope": {"type": "string"},
                "role": {"type": "string", "description": "filter per role"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "office.signals_recent",
        "description": "Tail signals.jsonl di un goal (eventi script monitor).",
        "inputSchema": {
            "type": "object",
            "properties": {"goal_id": {"type": "string"}, "scope": {"type": "string"}, "limit": {"type": "integer", "default": 30}},
            "required": ["goal_id"],
        },
    },
    {
        "name": "office.executions_recent",
        "description": "Tail executions.jsonl (audit L3 + errori bybit). Critico per diagnose failure pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {"goal_id": {"type": "string"}, "scope": {"type": "string"}, "limit": {"type": "integer", "default": 20}},
            "required": ["goal_id"],
        },
    },
    {
        "name": "office.script_status",
        "description": "Lista monitor scripts attivi (pid, alive, restarts, errors).",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": {"type": "string", "description": "hub caller può filtrare per scope"}},
        },
    },
    {
        "name": "office.pending_actions",
        "description": "Lista pending actions di un goal (L2 queue).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "scope": {"type": "string"},
                "status": {"type": "string", "default": "pending", "description": "pending|approved|rejected|expired|all"},
            },
            "required": ["goal_id"],
        },
    },
    # T2 — Write (workspace-locked)
    {
        "name": "agent.update",
        "description": ("Modifica un file di config di un agent **del workspace caller**. "
                        "Files whitelisted: AGENTS.md, SOUL.md, TOOLS.md, CLAUDE.md, config.json. "
                        "Mode: 'replace' (default) o 'append'."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "file": {"type": "string", "enum": ["AGENTS.md", "SOUL.md", "TOOLS.md", "CLAUDE.md", "config.json"]},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["replace", "append"], "default": "replace"},
            },
            "required": ["name", "file", "content"],
        },
    },
    {
        "name": "office.script_lifecycle",
        "description": "Lifecycle di monitor script. action: start | stop | restart | reset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script_path": {"type": "string"},
                "action": {"type": "string", "enum": ["start", "stop", "restart", "reset"]},
                "goal_id": {"type": "string"},
            },
            "required": ["script_path", "action"],
        },
    },
    {
        "name": "routine.lifecycle",
        "description": "Routine lifecycle. action: enable | disable | run_now.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "action": {"type": "string", "enum": ["enable", "disable", "run_now"]}},
            "required": ["name", "action"],
        },
    },
    {
        "name": "goal.assign_agent",
        "description": "Modifica team di un goal del workspace caller. role: responsabile | escalation | analyst | risk-officer | dev | executor | researcher",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "role": {"type": "string"},
                "agent": {"type": "string"},
                "llm": {"type": "object", "description": "Opzionale: {provider, model, effort}"},
            },
            "required": ["goal_id", "role", "agent"],
        },
    },
    # T3 — Bridge (hub-only)
    {
        "name": "workspace.task",
        "description": "Hub CEO: assegna task a Anja-responsabile di un workspace. Task in inbox del workspace target.",
        "inputSchema": {
            "type": "object",
            "properties": {"target_workspace": {"type": "string"}, "prompt": {"type": "string"}, "focus": {"type": "string"}},
            "required": ["target_workspace", "prompt"],
        },
    },
    {
        "name": "workspace.diagnose_request",
        "description": "Hub CEO: chiedi al manager workspace di diagnose e riportare.",
        "inputSchema": {
            "type": "object",
            "properties": {"target_workspace": {"type": "string"}, "focus": {"type": "string"}},
            "required": ["target_workspace"],
        },
    },
    {
        "name": "workspace.list_tasks",
        "description": "Lista task in inbox del workspace caller (o cross-workspace se hub).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOL_HANDLERS = {
    "office.diagnose": tool_diagnose,
    "office.notes_recent": tool_notes_recent,
    "office.signals_recent": tool_signals_recent,
    "office.executions_recent": tool_executions_recent,
    "office.script_status": tool_script_status,
    "office.pending_actions": tool_pending_actions,
    "agent.update": tool_agent_update,
    "office.script_lifecycle": tool_script_lifecycle,
    "routine.lifecycle": tool_routine_lifecycle,
    "goal.assign_agent": tool_goal_assign_agent,
    "workspace.task": tool_workspace_task,
    "workspace.diagnose_request": tool_workspace_diagnose_request,
    "workspace.list_tasks": tool_workspace_list_tasks,
}


# =================================================================
# JSON-RPC dispatch (MCP)
# =================================================================

def handle_request(req: dict) -> Optional[dict]:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "anja_office_ops", "version": "0.1.0"},
        }}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown tool: {name}"}}
        try:
            result = handler(args)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "isError": "error" in result,
        }}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle_request(req)
        if resp is not None:
            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
