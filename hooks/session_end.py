#!/usr/bin/env python3
"""
session_end.py — hook eseguito a SessionEnd di Claude Code.

CC passa via stdin un JSON con:
    session_id, transcript_path, cwd, hook_event_name, reason

Si fa:
  1. Parse stdin (session metadata + transcript_path)
  2. Estrae messaggi dal transcript JSONL (utente, assistant, tool_use)
  3. Calcola durata reale, count messaggi, tools usati
  4. Scrive file-per-session in `<wiki>/sessions/<date>/<id>.md` con:
     - Frontmatter completo (id, started, ended, duration, scope, agent, reason, ...)
     - Sezione Summary (placeholder; popolabile via MCP tool `sessions.summarize` on-demand)
     - Sezione Stats (count messaggi + tools)
     - Sezione Transcript snippet (primi/ultimi N user prompts)
  5. Trigger cc_memory_to_soul → cc_memory_sync → compose_claude_md
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import journal_policy  # noqa: E402  (hooks/journal_policy.py, condiviso con steward)


def find_anja_root(start: Path):
    """Risale dalla cwd cercando un anja root.
    Ritorna (root: Path, kind: 'project'|'hub'|'agent', sessions_dir: Path) oppure None.

    Order matters (most specific first):
    - agent:   <root> ha `config.json` con type=agent O parent dir == 'agents'
    - project: parent ha `.anjawiki/meta.yaml` → sessions in <root>/.anjawiki/wiki/sessions/
    - hub:     ha `config/projects.json` → sessions in <root>/sessions/
    """
    cur = start.resolve()
    for parent in [cur] + list(cur.parents):
        # Agent marker: parent dir name = 'agents' AND has AGENTS.md/config.json
        if parent.parent.name == "agents" and (parent / "config.json").is_file():
            try:
                cfg = json.loads((parent / "config.json").read_text(encoding="utf-8"))
                if cfg.get("name") == parent.name or cfg.get("scope") in ("hub", "agent"):
                    return (parent, "agent", parent / "sessions")
            except Exception:
                pass
        # Project marker
        anjawiki = parent / ".anjawiki"
        if anjawiki.is_dir() and (anjawiki / "meta.yaml").is_file():
            return (parent, "project", anjawiki / "wiki" / "sessions")
        # Hub marker
        if (parent / "config" / "projects.json").is_file():
            return (parent, "hub", parent / "sessions")
    return None


def parse_stdin() -> dict:
    """Read JSON sent by CC via stdin. Empty dict if no/bad input."""
    try:
        data = sys.stdin.read()
        return json.loads(data) if data.strip() else {}
    except Exception:
        return {}


_CC_NOISE_PREFIXES = (
    "<local-command-",
    "<command-name>",
    "<command-message>",
    "<command-args>",
)


def _is_cc_noise(text: str) -> bool:
    """Skip CC-injected metadata che inquina la lista user prompts:
    tag slash-command, command stdout/stderr placeholder, empty content.
    """
    t = text.strip()
    if not t:
        return True
    if t in ("(no content)", "(no_content)"):
        return True
    return any(t.startswith(p) for p in _CC_NOISE_PREFIXES)


def parse_transcript(path: str) -> dict:
    """Parse CC transcript JSONL. Ritorna stats + snippets per session log.

    Format JSONL CC: ogni line è un evento (UserPromptSubmit, AssistantResponse, ToolUse, ...).
    """
    info = {
        "started": None,
        "ended": None,
        "user_messages": [],
        "assistant_messages_count": 0,
        "tools_used": Counter(),
        "total_lines": 0,
        "entrypoint": "",      # cli | sdk-py | sdk-cli … (CC lo scrive su ogni evento)
    }
    p = Path(path)
    if not p.is_file():
        return info

    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                info["total_lines"] += 1
                if not info["entrypoint"] and ev.get("entrypoint"):
                    info["entrypoint"] = str(ev.get("entrypoint"))

                ts = ev.get("timestamp")
                if ts:
                    if info["started"] is None:
                        info["started"] = ts
                    info["ended"] = ts

                evtype = ev.get("type", "")
                msg = ev.get("message", {}) if isinstance(ev.get("message"), dict) else {}
                role = msg.get("role", "")

                # User prompts: estrai testo
                if evtype == "user" or role == "user":
                    content = msg.get("content", "") if msg else ev.get("content", "")
                    if isinstance(content, list):
                        # blocchi: prendi solo text blocks (no tool_result)
                        text_parts = [b.get("text", "") for b in content
                                      if isinstance(b, dict) and b.get("type") == "text"]
                        text = "\n".join(text_parts)
                    elif isinstance(content, str):
                        text = content
                    else:
                        text = ""
                    text = text.strip()
                    if text and not _is_cc_noise(text):
                        info["user_messages"].append(text[:300])

                # Assistant responses
                if evtype == "assistant" or role == "assistant":
                    info["assistant_messages_count"] += 1
                    # Tool use blocks
                    content = msg.get("content", []) if msg else []
                    if isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_use":
                                tname = b.get("name", "?")
                                info["tools_used"][tname] += 1
    except Exception:
        pass

    return info


def _local_iso(dt: datetime) -> str:
    """ISO 8601 timezone-aware con offset locale (es: 2026-05-07T13:55:11+02:00)."""
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z").replace("+0", "+0").replace("-0", "-0")[:-2] + ":" + dt.strftime("%z")[-2:]


def _format_iso_local(dt_str: str) -> str:
    """Convert ISO string a timezone-aware locale (CEST/local)."""
    if not dt_str:
        return ""
    try:
        # CC timestamps: '2026-05-07T11:55:11.805Z' o simili
        s = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        local = dt.astimezone()
        return local.strftime("%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        return dt_str


def _duration_human(start_iso: str, end_iso: str) -> str:
    if not start_iso or not end_iso:
        return "?"
    try:
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        delta = (e - s).total_seconds()
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta / 60)}m {int(delta % 60)}s"
        return f"{int(delta / 3600)}h {int((delta % 3600) / 60)}m"
    except Exception:
        return "?"


def _find_existing_for_cc_session(date_dir: Path, cc_session_id: str) -> Path | None:
    """Cerca un session file già scritto per questa cc_session_id nella date_dir.

    Serve per UPSERT: compact/resume/exit/Ctrl+C emettono SessionEnd multipli per
    la STESSA cc_session_id. Invece di creare N file (noise) o skippare 'other'
    (perde l'uscita Ctrl+C), aggiorniamo sempre lo stesso file.
    """
    if not cc_session_id or not date_dir.is_dir():
        return None
    needle = f"cc_session_id: {cc_session_id}"
    for f in date_dir.glob("*.md"):
        try:
            if needle in f.read_text(encoding="utf-8"):
                return f
        except Exception:
            continue
    return None


def _extract_populated_summary(path: Path) -> str | None:
    """Ritorna il body della sezione ## Summary se popolato (non placeholder), else None.
    Serve a NON perdere un summary già generato quando facciamo upsert del file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    m = re.search(r"^## Summary\s*\n(.*?)(?=\n## |\Z)", text, re.M | re.DOTALL)
    if not m:
        return None
    body = m.group(1).strip()
    if not body or body.startswith("<!--"):
        return None
    return body


def _duration_seconds(start_iso: str, end_iso: str) -> float:
    try:
        s = datetime.fromisoformat((start_iso or "").replace("Z", "+00:00"))
        e = datetime.fromisoformat((end_iso or "").replace("Z", "+00:00"))
        return max(0.0, (e - s).total_seconds())
    except Exception:
        return 0.0


def write_session_file(sessions_root: Path, kind: str, session_meta: dict, transcript_info: dict) -> Path:
    """Crea (o aggiorna, upsert per cc_session_id) il file-per-session.

    sessions_root: <project>/.anjawiki/wiki/sessions/ (project) o <hub>/sessions/ (hub).
    kind: 'project' | 'hub' (scrive nel frontmatter scope).
    `session_meta["agent"]` lo mette il chiamante (hook CC o adapter); se manca il file
    dice `cli-unknown` — mai mascherare un harness da Claude.
    """
    now_local = datetime.now().astimezone()
    hms = now_local.strftime("%H%M%S")
    short_hash = secrets.token_hex(2)
    agent = session_meta.get("agent") or "cli-unknown"
    if not session_meta.get("agent"):
        print("[anja] WARNING: session_meta senza `agent` → cli-unknown", file=sys.stderr)
    harness = session_meta.get("harness") or (agent[4:] if agent.startswith("cli-") else "unknown")
    entrypoint = session_meta.get("entrypoint") or transcript_info.get("entrypoint") or ""
    anja_id = f"{hms}-{agent}-{short_hash}"

    cc_session_id = session_meta.get("session_id", "")
    reason = session_meta.get("reason", "?")
    transcript_path = session_meta.get("transcript_path", "")

    # Use transcript timestamps se disponibili, altrimenti fallback a now
    started_raw = transcript_info.get("started") or now_local.isoformat()
    ended_raw = transcript_info.get("ended") or now_local.isoformat()
    started_local = _format_iso_local(started_raw)
    ended_local = _format_iso_local(ended_raw)
    duration = _duration_human(started_raw, ended_raw)

    # `today` deve riflettere la data di FINE sessione (dal transcript), non quella
    # in cui il hook scatta — altrimenti se l'hook ritarda di un giorno (idle, run
    # manuale, retry) il file finisce in una cartella di data sbagliata.
    try:
        today = datetime.fromisoformat(ended_raw.replace("Z", "+00:00")).astimezone().date().isoformat()
    except Exception:
        today = date.today().isoformat()

    user_messages = transcript_info.get("user_messages", [])
    assistant_count = transcript_info.get("assistant_messages_count", 0)
    tools_used = transcript_info.get("tools_used", Counter())

    # Layout file-per-session — UPSERT per cc_session_id
    date_dir = sessions_root / today
    date_dir.mkdir(parents=True, exist_ok=True)
    existing = _find_existing_for_cc_session(date_dir, cc_session_id)
    if existing is not None:
        session_file = existing
        anja_id = existing.stem           # riusa l'id originale (link/citazioni stabili)
        preserved_summary = _extract_populated_summary(existing)  # non perdere il summary già generato
    else:
        session_file = date_dir / f"{anja_id}.md"
        preserved_summary = None

    # Build markdown — frontmatter conforming a schema wiki canonico
    # (title + type + created + updated obbligatori, vedi .anjawiki/CLAUDE.md)
    lines = []
    lines.append("---")
    lines.append(f"title: Session {anja_id}")
    lines.append("type: session")
    lines.append(f"created: {today}")
    lines.append(f"updated: {today}")
    lines.append(f"id: {anja_id}")
    if cc_session_id:
        lines.append(f"cc_session_id: {cc_session_id}")
    if transcript_path:
        lines.append(f"transcript_path: {transcript_path}")
    lines.append(f"started: {started_local}")
    lines.append(f"ended: {ended_local}")
    lines.append(f"duration: {duration}")
    lines.append(f"scope: {kind}")
    lines.append(f"agent: {agent}")
    lines.append(f"harness: {harness}")
    if entrypoint:
        lines.append(f"entrypoint: {entrypoint}")
    lines.append(f"date: {today}")
    lines.append(f"end_reason: {reason}")
    lines.append(f"messages_user: {len(user_messages)}")
    lines.append(f"messages_assistant: {assistant_count}")
    if tools_used:
        lines.append(f"tools_used: [{', '.join(tools_used.keys())}]")
    lines.append("---")
    lines.append("")
    lines.append(f"# Session {anja_id}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    if preserved_summary:
        lines.append(preserved_summary)
    else:
        lines.append(f"<!-- Vuoto by design. Popola on-demand via MCP tool: sessions.summarize(session_id=\"{anja_id}\"). -->")
    lines.append("")
    lines.append("## Stats")
    lines.append("")
    lines.append(f"- **Durata**: {duration}")
    lines.append(f"- **Messaggi user**: {len(user_messages)}")
    lines.append(f"- **Messaggi assistant**: {assistant_count}")
    lines.append(f"- **Chiusura**: `{reason}`")
    if tools_used:
        lines.append(f"- **Tools usati** ({sum(tools_used.values())} call):")
        for tname, count in tools_used.most_common(10):
            lines.append(f"  - `{tname}` × {count}")
    lines.append("")
    if user_messages:
        lines.append("## User prompts")
        lines.append("")
        # Lossless: TUTTI i prompt utente (segnale denso, compatto). Prima erano
        # troncati a primi-5 + ultimi-3 → perdita. Il drill-down completo ai turni
        # assistant/tool è via `transcript_path` qui sotto.
        for m in user_messages:
            snippet = m.replace("\n", " ").strip()[:280]
            lines.append(f"- {snippet}")
        lines.append("")
    if transcript_path:
        lines.append("## Transcript (drill-down lossless)")
        lines.append("")
        lines.append("> Recovery deterministico ai turni originali (assistant + tool, oltre i prompt qui sopra):")
        lines.append(f"> `{transcript_path}`")
        lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("<!-- Append note rilevanti durante la sessione: decisioni, blocker, info da ricordare. -->")
    lines.append("")

    session_file.write_text("\n".join(lines), encoding="utf-8")
    return session_file


def best_effort_post_session_hooks(project_root: Path) -> None:
    """Best-effort dopo session_end (ordine logico):
    1. cc_memory_to_soul: assorbe file CC native auto-memory in SOUL.md (single source)
    2. cc_memory_sync: SOUL → anja_user/feedback.md (mirror)
    3. compose_claude_md: rigenera CLAUDE.md fresh per la prossima sessione
    """
    here = Path(__file__).resolve()
    scripts_dir = here.parent.parent / "scripts"
    for script_name in ("cc_memory_to_soul.py", "cc_memory_sync.py", "compose_claude_md.py"):
        script = scripts_dir / script_name
        if not script.is_file():
            continue
        try:
            subprocess.run(
                [sys.executable, str(script), "--target", str(project_root), "--quiet"],
                check=False, capture_output=True, timeout=8,
            )
        except Exception:
            pass


def spawn_bg_summarize(session_file: Path, transcript_info: dict = None, duration_sec: float = 0.0) -> None:
    """Spawn `summarize_session_bg.py` come processo DETACHED — non blocca /exit.

    Il subprocess continua dopo che l'hook session_end termina (start_new_session
    + stdin/stdout/stderr chiusi). Genera il summary via `<harness> -p` e lo
    scrive nella sezione `## Summary` del session file in ~30-60s tipicamente.

    Skip silenzioso se ANJA_AUTO_SUMMARY=0 nell'env (opt-out), o se la sessione non
    vale un summary (journal_policy.is_worth — prima lo spawn era incondizionato:
    456 call haiku su sessioni-macchina di pochi secondi, misura 2026-08-19).
    """
    if os.environ.get("ANJA_AUTO_SUMMARY", "1") == "0":
        return
    if transcript_info is not None and not journal_policy.is_worth(
            len(transcript_info.get("user_messages", [])), duration_sec,
            list(transcript_info.get("tools_used", {}).keys()), transcript_info.get("user_messages", [])):
        return
    here = Path(__file__).resolve()
    script = here.parent.parent / "scripts" / "summarize_session_bg.py"
    if not script.is_file():
        return
    # Disabilita auto-summary nella sub-sessione `claude -p` spawnata da
    # summarize_session_bg: senza questo opt-out, ogni summary genera una
    # nuova SessionEnd che riarma il loop → centinaia di session file fantasma.
    child_env = os.environ.copy()
    child_env["ANJA_AUTO_SUMMARY"] = "0"
    child_env["ANJA_WIKI_EMBED"] = "0"
    child_env["ANJA_JOURNAL"] = "0"      # la sessione del summarizer non è un journal
    try:
        subprocess.Popen(
            [sys.executable, str(script), "--session-file", str(session_file)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # detach: sopravvive alla morte del parent
            env=child_env,
        )
    except Exception:
        pass


def spawn_bg_wiki_embed_check(project_root: Path) -> None:
    """Spawn DETACHED background `wiki_embed.py` per consistency check.

    Cattura modifiche fatte fuori dai trigger inline / PostToolUse (es. edit
    manuale del file dall'utente). Dirty detection idempotente: re-run è no-op
    se nulla è cambiato dall'ultimo embed.

    Skip se ANJA_WIKI_EMBED=0 (opt-out globale) o se manca embed provider
    (silenzioso: il subprocess stesso fa graceful exit con error in stdout).
    """
    if os.environ.get("ANJA_WIKI_EMBED", "1") == "0":
        return
    script = Path(__file__).resolve().parent.parent / "scripts" / "wiki_embed.py"
    if not script.is_file():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(script), str(project_root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _capture_unknown_payload(root: Path, session_meta: dict) -> None:
    """Harness non riconosciuto (es. Grok Build che carica gli hook CC): appende payload
    stdin + hint env in `<root>/.anjawiki/.hook-payloads.log` (cap 200 KB). È lo spike
    A0 del design steward fatto "in produzione": la prossima sessione Grok lascia qui
    il suo wire format, e l'adapter si scrive sui dati, non a memoria."""
    try:
        base = root / ".anjawiki" if (root / ".anjawiki").is_dir() else root
        log = base / ".hook-payloads.log"
        if log.is_file() and log.stat().st_size > 200_000:
            return
        hints = {k: v for k, v in os.environ.items()
                 if k.startswith(("CLAUDE", "GROK", "CODEX", "OPENCODE", "ANJA_HARNESS"))}
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "cwd": str(Path.cwd()), "payload": session_meta, "env_hints": hints}
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False)[:4000] + "\n")
    except Exception:
        pass


def main() -> None:
    session_meta = parse_stdin()

    # CC emette SessionEnd con reason: clear, resume, logout, prompt_input_exit,
    # bypass_permissions_disabled, other. NB: Ctrl+C (SIGINT) NON emette SessionEnd —
    # termina il processo prima del cleanup hook. Le uscite catturabili sono quelle
    # graceful: /exit, Ctrl+D (EOF), /clear, logout, compact.
    # Non skippiamo nessun reason: l'UPSERT per cc_session_id in write_session_file
    # garantisce 1 solo file per sessione (no noise da compact), sempre aggiornato
    # all'ultimo boundary. Il summary già generato viene preservato attraverso gli upsert.
    # IMPORTANTE: questo hook gira con `python3` (su macOS = /usr/bin/python3 = 3.9).
    # `from __future__ import annotations` in testa è obbligatorio: senza, i type hints
    # 3.10+ (`X | None`) crashano l'hook all'import su 3.9 → nessun file scritto.
    reason = session_meta.get("reason", "")

    found = find_anja_root(Path.cwd())
    if found is None:
        sys.exit(0)
    root, kind, sessions_dir = found

    transcript_info = {}
    transcript_path = session_meta.get("transcript_path", "")
    if transcript_path:
        transcript_info = parse_transcript(transcript_path)

    # Chi siamo: harness (claude/codex/grok/opencode/unknown) ed entrypoint (cli/sdk-*).
    # Gli adapter (codex_adapter) passano `agent` esplicito; il path CC lo deriva dall'env.
    harness = session_meta.get("harness") or journal_policy.detect_harness(os.environ, session_meta)
    session_meta.setdefault("harness", harness)
    session_meta.setdefault("agent", journal_policy.agent_for(harness))
    entrypoint = journal_policy.detect_entrypoint(os.environ, transcript_info.get("entrypoint", ""))
    session_meta.setdefault("entrypoint", entrypoint)
    if harness == "unknown":
        _capture_unknown_payload(root, session_meta)   # spike: impara il wire format del harness

    # Sessioni-macchina (SDK, `claude -p`, nostri spawner, 0 messaggi): niente journal,
    # niente summary. Erano il 92% dei file in sessions/ (misura 2026-08-19).
    duration_sec = _duration_seconds(transcript_info.get("started") or "", transcript_info.get("ended") or "")
    why = journal_policy.is_programmatic(os.environ, entrypoint,
                                         len(transcript_info.get("user_messages", [])), duration_sec, reason)
    if why:
        print(f"[anja] journal skipped ({why})", file=sys.stderr)
        sys.exit(0)

    try:
        session_file = write_session_file(sessions_dir, kind, session_meta, transcript_info)
        rel = session_file.relative_to(root)
        print(f"[anja] Session file ({kind}) → {rel}", file=sys.stderr)
        # Spawn auto-summary in background (detached, non blocca /exit) — solo se worth
        spawn_bg_summarize(session_file, transcript_info, duration_sec)
    except Exception as e:
        print(f"[anja] WARNING: session file write failed: {e}", file=sys.stderr)

    # Wiki embedding consistency check (orphan cleanup + dirty pages catch-all)
    if kind == "project":
        spawn_bg_wiki_embed_check(root)

    best_effort_post_session_hooks(root)


if __name__ == "__main__":
    main()
