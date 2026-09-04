"""Configurazione del server anja_memory: env (ANJA_SCOPE/ANJA_ROOT), versione, secrets, path del plugin."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROTO_VERSION = "2024-11-05"


SERVER_NAME = "anja_memory"


SERVER_VERSION = "0.28.0"


SCOPE = os.environ.get("ANJA_SCOPE", "project")  # project | hub | agent


ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()


def _load_secrets_env() -> int:
    """Auto-load `.secrets.env` via secrets_loader (modulo condiviso con CLI scripts)."""
    try:
        import importlib.util
        sp = SCRIPTS_DIR / "secrets_loader.py"
        spec = importlib.util.spec_from_file_location("secrets_loader", sp)
        sl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sl)
        return sl.load_secrets(ROOT, scope=SCOPE)
    except Exception:
        return 0


_SECRETS_LOADED = _load_secrets_env()


# Cartelle del plugin: `scripts/` (moduli CLI condivisi: code_db, wiki_embed, ...) e la root.
# Due layout: repo/plugin Claude Code (scripts/anja/config.py → scripts/) oppure pacchetto pip
# (site-packages/anja/config.py, moduli CLI nel package `anja_scripts` accanto).
def _resolve_scripts_dir() -> Path:
    here = Path(__file__).resolve().parents[1]
    if (here / "code_db.py").is_file():
        return here
    try:
        import anja_scripts  # pacchetto pip: scripts/ installato come anja_scripts/
        return Path(anja_scripts.__file__).resolve().parent
    except ImportError:
        return here


SCRIPTS_DIR = _resolve_scripts_dir()
PLUGIN_ROOT = SCRIPTS_DIR.parent


# Logging diagnostico opt-in (PIANO 4.2): ANJA_LOG=debug → ogni eccezione recuperata in
# silenzio viene tracciata su stderr (stdout è riservato al JSON-RPC). Default: solo warn.
LOG_LEVEL = (os.environ.get("ANJA_LOG") or "").strip().lower()


def log(msg: str, level: str = "debug") -> None:
    if level == "warn" or LOG_LEVEL == "debug":
        print(f"[anja_memory] {level.upper()} {msg}", file=sys.stderr, flush=True)


def log_exc(where: str, exc: BaseException) -> None:
    """Traccia un'eccezione gestita best-effort (visibile solo con ANJA_LOG=debug)."""
    if LOG_LEVEL == "debug":
        print(f"[anja_memory] DEBUG {where}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
