#!/usr/bin/env python3
"""journal_policy.py — regole condivise del journal (hook + steward), stdlib, py3.9-safe.

Tre domande, una risposta deterministica ciascuna:
  * `detect_harness(env, payload)`   — chi sta girando (claude | codex | grok | opencode | unknown)
  * `is_programmatic(...)`           — è una sessione-macchina (SDK, `claude -p`, nostri spawner)?
                                       Niente journal, niente summary.
  * `is_worth(...)`                  — vale un summary / il triage dello steward?

Misura che ha motivato il filtro (wiki AnjaHub, 2026-08-19): 493 session, 456 con
≤2 messaggi utente e durata di secondi, TUTTE con entrypoint sdk-py/sdk-cli (commitment
sensor, summarizer, e2e) — zero sessioni umane corte. Le umane erano 37, già riassunte.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Optional

PROGRAMMATIC_ENTRYPOINTS = ("sdk-py", "sdk-ts", "sdk-cli", "sdk")
HARNESS_AGENT = {"claude": "cli-claude", "codex": "cli-codex", "grok": "cli-grok",
                 "opencode": "cli-opencode"}

# Tool che indicano lavoro "che lascia traccia" (usati da is_worth)
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit",
               "wiki.upsert_entity", "wiki.upsert_concept", "wiki.upsert_source",
               "wiki.upsert_analysis", "wiki.update_overview", "wiki.log_append",
               "roadmap.add", "roadmap.update", "roadmap.complete", "skill.save", "skill.patch"}
_WRITE_TOOL_SUFFIXES = ("__Write", "__Edit", "wiki_upsert_", "wiki_update_overview", "wiki_log_append",
                        "roadmap_add", "roadmap_update", "roadmap_complete", "skill_save", "skill_patch")
SIGNAL_KW = ("decisione", "decision", "split", "design", "breaking", "ship", "release",
             "migra", "migrat", "refactor", "architett", "architecture", "roadmap", "piano",
             "deploy", "bump", "security", "sicurezza")


def detect_harness(env: Optional[dict] = None, payload: Optional[dict] = None) -> str:
    """claude | codex | grok | opencode | unknown. Override esplicito: ANJA_HARNESS."""
    env = os.environ if env is None else env
    forced = (env.get("ANJA_HARNESS") or "").strip().lower()
    if forced:
        return forced
    if env.get("CLAUDECODE") == "1" or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    if env.get("CODEX_SANDBOX") or env.get("CODEX_HOME") or env.get("CODEX_CLI"):
        return "codex"
    if any(k.startswith("GROK") for k in env):
        return "grok"
    if any(k.startswith("OPENCODE") for k in env):
        return "opencode"
    p = payload or {}
    blob = " ".join(str(k) for k in p.keys()).lower()
    if "grok" in blob:
        return "grok"
    if "codex" in blob:
        return "codex"
    return "unknown"


def agent_for(harness: str) -> str:
    return HARNESS_AGENT.get(harness, "cli-" + (harness or "unknown"))


def detect_entrypoint(env: Optional[dict] = None, transcript_entrypoint: str = "") -> str:
    """cli | sdk-py | sdk-cli | … — dall'env del processo harness, poi dal transcript."""
    env = os.environ if env is None else env
    return (env.get("CLAUDE_CODE_ENTRYPOINT") or transcript_entrypoint or "").strip()


def is_programmatic(env: Optional[dict] = None, entrypoint: str = "",
                    messages_user: int = 0, duration_sec: float = 0.0, reason: str = "") -> Optional[str]:
    """Ritorna il MOTIVO (stringa) se la sessione è macchina → niente journal; None se umana.

    1. ANJA_JOURNAL=0 nell'env: opt-out esplicito (i nostri spawner lo settano).
    2. entrypoint sdk-*: Agent SDK / `claude -p` (commitment sensor, summarizer, e2e…).
    3. Rete di sicurezza per harness senza entrypoint: 0 messaggi utente, oppure 1
       messaggio in < 30 s chiuso con reason `other` (non è una persona).
    """
    env = os.environ if env is None else env
    if env.get("ANJA_JOURNAL", "1") == "0":
        return "env:ANJA_JOURNAL=0"
    ep = (entrypoint or "").lower()
    if ep and (ep in PROGRAMMATIC_ENTRYPOINTS or ep.startswith("sdk")):
        return "entrypoint:" + ep
    if messages_user <= 0:
        return "no-user-messages"
    if messages_user == 1 and duration_sec < 30 and (reason or "").lower() in ("other", ""):
        return "one-shot<30s"
    return None


def _has_write_tool(tools: Iterable[str]) -> bool:
    for t in tools or ():
        if t in WRITE_TOOLS or any(s in t for s in _WRITE_TOOL_SUFFIXES):
            return True
    return False


def is_worth(messages_user: int, duration_sec: float, tools: Iterable[str] = (),
             prompts: Iterable[str] = ()) -> bool:
    """Vale un summary / il triage: ≥3 msg utente E ≥5 min E (≥8 msg O tool di scrittura
    O keyword di segnale nei prompt). Una sessione da 4 turni su uno split è oro; venti
    'ok / sistema il test' sono rumore — ma entrano comunque per volume."""
    if messages_user < 3 or duration_sec < 300:
        return False
    if messages_user >= 8 or _has_write_tool(tools):
        return True
    blob = " ".join(prompts or ()).lower()
    return any(k in blob for k in SIGNAL_KW)


_DUR_RE = re.compile(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?")


def duration_to_seconds(s: str) -> float:
    """'1h 12m' | '4m 30s' | '13s' | '0s' → secondi. Formato di `duration:` nel frontmatter."""
    s = (s or "").strip()
    if not s or s == "?":
        return 0.0
    m = _DUR_RE.fullmatch(s)
    if not m:
        return 0.0
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se


def parse_frontmatter(text: str) -> dict:
    """Frontmatter YAML flat `k: v` → dict di stringhe (quanto basta per le session)."""
    out = {}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end < 0:
        return out
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def session_stats(text: str) -> dict:
    """Dal file session: messages_user, duration_sec, tools (lista), prompts (lista)."""
    fm = parse_frontmatter(text)
    try:
        mu = int(fm.get("messages_user", "0") or 0)
    except ValueError:
        mu = 0
    tools_raw = fm.get("tools_used", "")
    tools = [t.strip() for t in tools_raw.strip("[]").split(",") if t.strip()] if tools_raw else []
    prompts = []
    m = re.search(r"^## User prompts\s*\n(.*?)(?=\n## |\Z)", text, re.M | re.S)
    if m:
        prompts = [ln[2:].strip() for ln in m.group(1).splitlines() if ln.startswith("- ")]
    return {"messages_user": mu, "duration_sec": duration_to_seconds(fm.get("duration", "")),
            "tools": tools, "prompts": prompts, "frontmatter": fm}
