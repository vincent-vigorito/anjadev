#!/usr/bin/env python3
"""summarize_session_bg.py — generatore auto-summary in background.

Spawnato detached da session_end.py al termine di una sessione CC. NON blocca
il /exit (hook ritorna subito). Spawna `claude -p ... --model haiku` sul
session file, scrive il risultato nella sezione `## Summary` sostituendo il
placeholder. Skip se Summary già popolato (idempotente).

Usage:
  python3 summarize_session_bg.py --session-file <path>

Env opzionale:
  ANJA_CLAUDE_BIN     — path al binario claude (default: 'claude' nel PATH)
  ANJA_SUMMARY_MODEL  — modello: haiku|sonnet|opus (default 'haiku')

Niente output verso stdout/stderr quando lanciato in background. Logging in
`<wiki>/.bg-summarize.log` per debug post-mortem.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _resolve_claude_bin(explicit: str | None = None) -> str | None:
    """Risolve il path assoluto del binario `claude`.

    Quando il summarizer è spawnato da un hook CC, il PATH ereditato è minimale
    e NON include ~/.local/bin (dove vive `claude` tipicamente). Cercare il
    comando nudo fallisce con FileNotFoundError → summary mai generato.
    Risolviamo esplicitamente: env override → which → path noti per OS.
    """
    if explicit and explicit != "claude":
        return explicit  # path esplicito passato dall'utente
    # 1. shutil.which con PATH corrente (funziona se lanciato da shell completa)
    found = shutil.which("claude")
    if found:
        return found
    # 2. PATH allargato con le location note (hook env minimale)
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
        Path.home() / ".claude" / "local" / "claude",
        Path("/usr/bin/claude"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _log(msg: str, log_path: Path | None = None) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n"
    if log_path:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def summarize(session_file: Path, model: str = "haiku", claude_bin: str = "claude",
              log_path: Path | None = None) -> int:
    if not session_file.is_file():
        _log(f"ERROR session not found: {session_file}", log_path)
        return 2

    resolved_bin = _resolve_claude_bin(claude_bin)
    if not resolved_bin:
        _log(f"ERROR claude binary not found (PATH minimale? cercato ~/.local/bin, /usr/local/bin, /opt/homebrew/bin)", log_path)
        return 3
    claude_bin = resolved_bin

    content = session_file.read_text(encoding="utf-8")
    summary_re = re.compile(r"(^## Summary\s*\n)(.*?)(?=\n## |\Z)", re.M | re.DOTALL)
    m = summary_re.search(content)
    existing = (m.group(2).strip() if m else "")
    is_placeholder = (not existing) or existing.startswith("<!--")
    if existing and not is_placeholder:
        _log(f"SKIP already summarized: {session_file.name}", log_path)
        return 0

    prompt = (
        "Leggi il seguente file di sessione di Claude Code (markdown con "
        "frontmatter + stats + lista user prompts). Produci un summary conciso "
        "in italiano: 3-5 bullet point che coprano cosa è stato fatto, decisioni "
        "chiave, e outcome. NIENTE preambolo, NIENTE 'ecco il summary'. Solo "
        "bullet diretti, niente headings.\n\n---\n" + content + "\n---"
    )

    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--model", model],
            capture_output=True, timeout=180, text=True,
        )
    except FileNotFoundError:
        _log(f"ERROR claude CLI not in PATH ('{claude_bin}')", log_path)
        return 3
    except subprocess.TimeoutExpired:
        _log(f"ERROR claude timeout 180s: {session_file.name}", log_path)
        return 4

    if result.returncode != 0:
        _log(f"ERROR claude rc={result.returncode} stderr={result.stderr[:300]}", log_path)
        return result.returncode

    summary = result.stdout.strip()
    if not summary:
        _log(f"ERROR empty summary from claude: {session_file.name}", log_path)
        return 5

    new_block = f"## Summary\n\n{summary}\n"
    if m:
        new_content = content[:m.start()] + new_block + content[m.end():]
    else:
        new_content = content.rstrip() + "\n\n" + new_block
    session_file.write_text(new_content, encoding="utf-8")

    _log(f"OK summarized {session_file.name} ({len(summary)} chars, model={model})", log_path)
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session-file", required=True, help="Path al session .md")
    p.add_argument("--model", default=os.environ.get("ANJA_SUMMARY_MODEL", "haiku"))
    p.add_argument("--claude-bin", default=os.environ.get("ANJA_CLAUDE_BIN", "claude"))
    p.add_argument("--log-path", help="Path file log per debug (default: <wiki>/.bg-summarize.log)")
    args = p.parse_args()

    session_file = Path(args.session_file).resolve()
    if args.log_path:
        log_path = Path(args.log_path)
    else:
        # Ascend cercando .anjawiki/wiki
        log_path = None
        for parent in [session_file.parent] + list(session_file.parents):
            if parent.name == "wiki" and parent.parent.name == ".anjawiki":
                log_path = parent / ".bg-summarize.log"
                break

    _log(f"STARTED {session_file.name} (pid={os.getpid()})", log_path)
    try:
        rc = summarize(session_file, model=args.model, claude_bin=args.claude_bin, log_path=log_path)
        sys.exit(rc)
    except Exception as e:
        _log(f"FATAL {e}\n{traceback.format_exc()}", log_path)
        sys.exit(99)


if __name__ == "__main__":
    main()
