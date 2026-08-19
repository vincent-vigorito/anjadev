#!/usr/bin/env python3
"""F-anjadev-steward A-1/A1/A3 — igiene del journal alla fonte.

- journal_policy: harness/entrypoint, sessioni-macchina, worth, durata.
- session_end.py e2e (subprocess, come lo lancia l'harness): umana → journal con
  agent/harness/entrypoint; sdk-py / ANJA_JOURNAL=0 / 0 messaggi → niente journal;
  harness ignoto → cli-unknown + payload catturato; meta senza agent → cli-unknown.
- summarize_session_bg: nessun CLI in PATH → rc 0 e placeholder intatto; CLI finto
  `grok` in PATH → summary scritto via `-p`; ANJA_SUMMARY_BIN path esplicito.

Standalone: python3 tests/test_journal_policy.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
import journal_policy as jp   # noqa: E402

HOOK_PY = "/usr/bin/python3" if Path("/usr/bin/python3").is_file() else sys.executable   # come hooks.json (python3 di sistema)
PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def make_project(tmp: Path) -> Path:
    proj = tmp / "proj"
    (proj / ".anjawiki" / "wiki" / "sessions").mkdir(parents=True)
    (proj / ".anjawiki" / "meta.yaml").write_text("name: proj\ntype: dev\n")
    return proj


def make_transcript(tmp: Path, n_user: int, entrypoint: str = "cli", span_sec: int = 600, name="t.jsonl") -> Path:
    p = tmp / name
    t0 = 1787100000
    lines = []
    for i in range(n_user):
        ts = t0 + int(i * span_sec / max(1, n_user))
        lines.append({"type": "user", "entrypoint": entrypoint, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
                      "message": {"role": "user", "content": f"prompt numero {i}: facciamo il design dello split"}})
        lines.append({"type": "assistant", "entrypoint": entrypoint, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts + 5)),
                      "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "name": "Edit"}]}})
    if not lines:
        lines.append({"type": "system", "entrypoint": entrypoint, "timestamp": "2026-08-19T10:00:00Z"})
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def run_hook(proj: Path, payload: dict, env_extra: dict) -> tuple[int, str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(proj.parent), "ANJA_AUTO_SUMMARY": "0", "ANJA_WIKI_EMBED": "0"}
    env.update(env_extra)
    r = subprocess.run([HOOK_PY, str(PLUGIN / "hooks" / "session_end.py")], input=json.dumps(payload),
                       capture_output=True, text=True, cwd=str(proj), env=env, timeout=30)
    return r.returncode, r.stderr


def sessions(proj: Path) -> list[Path]:
    return sorted((proj / ".anjawiki" / "wiki" / "sessions").rglob("*.md"))


def main():
    tmp = Path(tempfile.mkdtemp())
    print("journal_policy")
    check("harness claude da CLAUDECODE", jp.detect_harness({"CLAUDECODE": "1"}) == "claude")
    check("harness override ANJA_HARNESS", jp.detect_harness({"ANJA_HARNESS": "grok", "CLAUDECODE": "1"}) == "grok")
    check("harness unknown senza indizi", jp.detect_harness({}, {}) == "unknown")
    check("agent_for", jp.agent_for("grok") == "cli-grok" and jp.agent_for("unknown") == "cli-unknown")
    check("programmatic: sdk-py", jp.is_programmatic({}, "sdk-py", 10, 900, "other") == "entrypoint:sdk-py")
    check("programmatic: ANJA_JOURNAL=0", jp.is_programmatic({"ANJA_JOURNAL": "0"}, "cli", 10, 900, "other") == "env:ANJA_JOURNAL=0")
    check("programmatic: 0 messaggi", jp.is_programmatic({}, "cli", 0, 0, "resume") == "no-user-messages")
    check("programmatic: 1 msg <30s other", jp.is_programmatic({}, "", 1, 13, "other") == "one-shot<30s")
    check("umana: 1 msg ma prompt_input_exit", jp.is_programmatic({}, "cli", 1, 13, "prompt_input_exit") is None)
    check("umana: cli 5 msg", jp.is_programmatic({}, "cli", 5, 600, "other") is None)
    check("worth: 4 msg 10min + Edit", jp.is_worth(4, 600, ["Read", "Edit"], []))
    check("worth: 4 msg 10min + keyword", jp.is_worth(4, 600, [], ["facciamo lo split"]))
    check("not worth: 4 msg 10min senza segnale", not jp.is_worth(4, 600, ["Read"], ["ok", "grazie"]))
    check("worth: 8 msg per volume", jp.is_worth(8, 600, [], ["ok"] * 8))
    check("not worth: 20 msg ma 2 min", not jp.is_worth(20, 120, ["Edit"], []))
    check("not worth: 2 msg", not jp.is_worth(2, 6000, ["Edit"], ["split"]))
    check("duration parse", jp.duration_to_seconds("1h 12m") == 4320 and jp.duration_to_seconds("4m 30s") == 270 and jp.duration_to_seconds("13s") == 13)
    st = jp.session_stats("---\ntitle: x\nmessages_user: 4\nduration: 6m 2s\ntools_used: [Read, Edit]\n---\n\n## User prompts\n\n- ciao\n- split\n\n## Notes\n")
    check("session_stats", st["messages_user"] == 4 and st["duration_sec"] == 362 and st["tools"] == ["Read", "Edit"] and st["prompts"] == ["ciao", "split"], str(st))

    print("session_end.py e2e")
    proj = make_project(tmp)
    t_cli = make_transcript(tmp, 5, "cli")
    rc, err = run_hook(proj, {"session_id": "s-human", "transcript_path": str(t_cli), "reason": "prompt_input_exit"},
                       {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"})
    files = sessions(proj)
    check("umana: journal scritto", rc == 0 and len(files) == 1, err[-300:])
    txt = files[0].read_text() if files else ""
    check("agent cli-claude, harness claude, entrypoint cli", "agent: cli-claude" in txt and "harness: claude" in txt and "entrypoint: cli" in txt, txt[:400])
    check("messages_user 5", "messages_user: 5" in txt)

    t_sdk = make_transcript(tmp, 1, "sdk-py", span_sec=10, name="sdk.jsonl")
    rc, err = run_hook(proj, {"session_id": "s-sdk", "transcript_path": str(t_sdk), "reason": "other"},
                       {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "sdk-py"})
    check("sdk-py: nessun journal, log 'skipped (entrypoint:sdk-py)'", len(sessions(proj)) == 1 and "entrypoint:sdk-py" in err, err[-200:])
    t_sdk2 = make_transcript(tmp, 12, "cli", name="big.jsonl")
    rc, err = run_hook(proj, {"session_id": "s-optout", "transcript_path": str(t_sdk2), "reason": "other"},
                       {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "ANJA_JOURNAL": "0"})
    check("ANJA_JOURNAL=0: nessun journal anche se lunga", len(sessions(proj)) == 1 and "ANJA_JOURNAL=0" in err, err[-200:])
    # entrypoint solo nel transcript (env senza CLAUDE_CODE_ENTRYPOINT)
    t_sdk3 = make_transcript(tmp, 3, "sdk-cli", name="sdkcli.jsonl")
    rc, err = run_hook(proj, {"session_id": "s-sdkcli", "transcript_path": str(t_sdk3), "reason": "other"}, {"CLAUDECODE": "1"})
    check("entrypoint sdk-cli letto dal transcript → skip", len(sessions(proj)) == 1 and "entrypoint:sdk-cli" in err, err[-200:])
    t_zero = make_transcript(tmp, 0, "cli", name="zero.jsonl")
    rc, err = run_hook(proj, {"session_id": "s-zero", "transcript_path": str(t_zero), "reason": "resume"}, {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"})
    check("0 messaggi (resume fantasma): skip", len(sessions(proj)) == 1 and "no-user-messages" in err, err[-200:])

    t_unk = make_transcript(tmp, 6, "", name="unk.jsonl")
    rc, err = run_hook(proj, {"session_id": "s-unk", "transcript_path": str(t_unk), "reason": "stop", "vendor_field": "x"}, {})
    files = sessions(proj)
    check("harness ignoto: journal con cli-unknown + harness unknown", len(files) == 2 and any("cli-unknown" in f.name for f in files), err[-200:])
    cap = proj / ".anjawiki" / ".hook-payloads.log"
    check("payload catturato in .hook-payloads.log (spike A0)", cap.is_file() and "vendor_field" in cap.read_text(), str(cap))
    # upsert per cc_session_id: seconda SessionEnd della stessa sessione umana non crea un secondo file
    rc, err = run_hook(proj, {"session_id": "s-human", "transcript_path": str(t_cli), "reason": "other"},
                       {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"})
    check("upsert per cc_session_id invariato", len(sessions(proj)) == 2)

    print("write_session_file senza agent → cli-unknown")
    import importlib.util
    spec = importlib.util.spec_from_file_location("se", PLUGIN / "hooks" / "session_end.py")
    se = importlib.util.module_from_spec(spec); spec.loader.exec_module(se)
    sroot = tmp / "sess2"; sroot.mkdir()
    f = se.write_session_file(sroot, "project", {"session_id": "x", "reason": "other"}, {"user_messages": ["a"] * 3, "assistant_messages_count": 3, "tools_used": {}})
    check("file cli-unknown", "cli-unknown" in f.name and "harness: unknown" in f.read_text())

    print("summarize_session_bg harness-agnostico")
    target = [p for p in sessions(proj) if "cli-claude" in p.name][0]
    r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "summarize_session_bg.py"), "--session-file", str(target)],
                       capture_output=True, text=True, env={"PATH": str(tmp / "emptybin"), "HOME": str(tmp), "ANJA_SUMMARY_BIN": "none"}, timeout=30)
    check("ANJA_SUMMARY_BIN=none / nessun CLI: rc 0, placeholder intatto", r.returncode == 0 and "<!-- Vuoto by design" in target.read_text(), r.stderr[-200:])
    fakebin = tmp / "bin"; fakebin.mkdir()
    grok = fakebin / "grok"
    grok.write_text("#!/bin/sh\n# finto: stampa il flag e 2 bullet\n[ \"$1\" = \"-p\" ] || { echo 'no -p' >&2; exit 2; }\necho '- bullet uno'\necho '- bullet due'\n")
    grok.chmod(grok.stat().st_mode | stat.S_IEXEC)
    r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "summarize_session_bg.py"), "--session-file", str(target)],
                       capture_output=True, text=True, env={"PATH": str(fakebin), "HOME": str(tmp)}, timeout=30)
    t2 = target.read_text()
    check("grok finto in PATH (nessun claude): summary scritto via -p", r.returncode == 0 and "- bullet uno" in t2 and "<!-- Vuoto" not in t2, r.stderr[-200:] + (proj / ".anjawiki/wiki/.bg-summarize.log").read_text()[-300:] if (proj / ".anjawiki/wiki/.bg-summarize.log").is_file() else "")
    r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "summarize_session_bg.py"), "--session-file", str(target)],
                       capture_output=True, text=True, env={"PATH": str(fakebin), "HOME": str(tmp)}, timeout=30)
    check("idempotente: già riassunto → skip rc 0", r.returncode == 0)
    claude_fake = fakebin / "claude"; claude_fake.write_text("#!/bin/sh\necho \"- via claude $4\"\n"); claude_fake.chmod(0o755)
    target2 = [p for p in sessions(proj) if "cli-unknown" in p.name][0]
    r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "summarize_session_bg.py"), "--session-file", str(target2)],
                       capture_output=True, text=True, env={"PATH": str(fakebin), "HOME": str(tmp), "ANJA_SUMMARY_BIN": str(claude_fake)}, timeout=30)
    check("ANJA_SUMMARY_BIN path esplicito → claude con --model", "- via claude haiku" in target2.read_text(), target2.read_text()[-200:])

    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


def test_journal_policy():
    main()


if __name__ == "__main__":
    main()
