#!/usr/bin/env python3
"""F-anjadev-steward pezzo B — compact_sessions + retrieval senza diari.

- classify: machine (entrypoint/0 msg/one-shot) · short vecchia → archive · short recente
  / worth → keep · distilled vecchia → archive.
- dry-run non scrive; --apply archivia con stub (frontmatter + archived + Summary +
  transcript) e purga le machine con --purge-machine; idempotente.
- server: sessions.list esclude archive/ (include_archived=true le mostra), wiki.stats
  conta archived a parte, wiki.lint emette `session-volume` quando i diari > 3× pagine.

Standalone: python3 tests/test_compact_sessions.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
PYTHON = "/opt/homebrew/opt/python@3.12/bin/python3.12" if Path("/opt/homebrew/opt/python@3.12/bin/python3.12").is_file() else sys.executable
spec = importlib.util.spec_from_file_location("compact", PLUGIN / "scripts" / "compact_sessions.py")
cs = importlib.util.module_from_spec(spec); spec.loader.exec_module(cs)

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {label}")
    else:
        FAIL += 1; print(f"  ✗ {label} {detail}")


def session_md(sid, day, msgs, duration, reason="other", entrypoint=None, summary="", distilled=False, transcript="/t/x.jsonl"):
    fm = [f"title: Session {sid}", "type: session", f"created: {day}", f"updated: {day}", f"id: {sid}",
          f"transcript_path: {transcript}", f"duration: {duration}", "scope: project", "agent: cli-claude",
          "harness: claude", f"date: {day}", f"end_reason: {reason}", f"messages_user: {msgs}", "messages_assistant: 3",
          "tools_used: [Read, Edit]"]
    if entrypoint:
        fm.insert(10, f"entrypoint: {entrypoint}")
    if distilled:
        fm.append("distilled: true")
    body = "---\n" + "\n".join(fm) + "\n---\n\n# Session " + sid + "\n\n## Summary\n\n" + \
           (summary or "<!-- Vuoto by design. -->") + "\n\n## Stats\n\n- **Durata**: " + duration + \
           "\n\n## User prompts\n\n" + "\n".join(f"- prompt {i} design" for i in range(msgs)) + "\n\n## Notes\n\n<!-- -->\n"
    return body


def make_wiki(tmp: Path) -> Path:
    proj = tmp / "proj"
    wiki = proj / ".anjawiki" / "wiki"
    (wiki / "sessions").mkdir(parents=True)
    (proj / ".anjawiki" / "meta.yaml").write_text("name: proj\n")
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "only-one.md").write_text("---\ntitle: Only one\ntype: concept\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n\n# Only one\n\n## Summary\n\nx\n")
    (wiki / "index.md").write_text("# index\n\n[[only-one]]\n")
    (wiki / "log.md").write_text("# log\n")
    old = (date.today() - timedelta(days=30)).isoformat()
    recent = (date.today() - timedelta(days=2)).isoformat()
    S = {}
    def put(sid, day, **kw):
        d = wiki / "sessions" / day; d.mkdir(exist_ok=True)
        (d / f"{sid}.md").write_text(session_md(sid, day, **kw)); S[sid] = d / f"{sid}.md"
    put("100000-cli-claude-m001", old, msgs=1, duration="8s", entrypoint="sdk-py")            # machine: entrypoint
    put("100001-cli-claude-m002", old, msgs=0, duration="0s", reason="resume")                 # machine: 0 msg
    put("100002-cli-claude-m003", recent, msgs=1, duration="12s", reason="other")              # machine: one-shot (anche se recente)
    put("100003-cli-claude-s001", old, msgs=2, duration="1m 10s", reason="prompt_input_exit", summary="- ha chiesto una cosa")   # short umana vecchia → archive
    put("100004-cli-claude-s002", recent, msgs=2, duration="2m", reason="prompt_input_exit")  # short recente → keep
    put("100005-cli-claude-w001", old, msgs=12, duration="40m", summary="- split fatto")       # worth vecchia → keep
    put("100006-cli-claude-d001", old, msgs=9, duration="30m", summary="- distillata", distilled=True)   # distilled vecchia → archive
    put("100007-cli-claude-d002", recent, msgs=9, duration="30m", distilled=True)              # distilled recente → keep
    for i in range(8):   # volume per far scattare il lint (> 3× pagine)
        put(f"11000{i}-cli-claude-v00{i}", recent, msgs=5, duration="10m")
    return proj


def rpc(proj: Path, msgs):
    env = {"ANJA_SCOPE": "project", "ANJA_ROOT": str(proj), "PATH": "/usr/bin:/bin", "HOME": str(proj.parent)}
    allm = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}] + msgs
    p = subprocess.run([PYTHON, str(PLUGIN / "scripts" / "mcp_memory_server.py")],
                       input="\n".join(json.dumps(m) for m in allm) + "\n", capture_output=True, text=True, env=env, timeout=30)
    out = {}
    for line in p.stdout.splitlines():
        if line.startswith("{"):
            d = json.loads(line)
            if "id" in d: out[d["id"]] = d
    return out


def res(o): return json.loads(o["result"]["content"][0]["text"])


def main():
    tmp = Path(tempfile.mkdtemp()); proj = make_wiki(tmp); sroot = proj / ".anjawiki" / "wiki" / "sessions"

    print("dry-run")
    rep = cs.run(proj, apply=False, purge_machine=True, older_than=14, distilled_after=14)
    check("3 machine da purgare", len(rep["purged"]) == 3, str(rep["purged"]))
    check("2 archive (short vecchia + distilled vecchia)", len(rep["archived"]) == 2 and
          {a["file"].split("/")[-1][:21] for a in rep["archived"]} == {"100003-cli-claude-s00", "100006-cli-claude-d00"}, str(rep["archived"]))
    check("kept = 3 + 8 volume", rep["kept"] == 11, str(rep["kept"]))
    check("dry-run: nessun file mosso", len(list(sroot.rglob("*.md"))) == 16 and not (sroot / "archive").exists())
    rep0 = cs.run(proj, apply=False, purge_machine=False, older_than=14, distilled_after=14)
    check("senza --purge-machine le machine vanno in archive", len(rep0["purged"]) == 0 and len(rep0["archived"]) == 5, str(rep0["by_reason"]))

    print("apply")
    rep = cs.run(proj, apply=True, purge_machine=True, older_than=14, distilled_after=14)
    check("machine cancellate", not any("m00" in f.name for f in sroot.rglob("*.md")))
    stubs = list((sroot / "archive").rglob("*.md"))
    check("2 stub in archive/<date>/", len(stubs) == 2 and all(p.parent.parent.name == "archive" for p in stubs), str(stubs))
    st = next(p for p in stubs if "s001" in p.name).read_text()
    check("stub: archived: true, Summary conservato, transcript, niente User prompts",
          "archived: true" in st and "- ha chiesto una cosa" in st and "/t/x.jsonl" in st and "## User prompts" not in st and "## Stats" not in st, st[:400])
    check("originali archiviate rimosse dalla cartella data", not any("s001" in f.name or "d001" in f.name for f in sroot.rglob("*.md") if "archive" not in f.parts))
    check("worth/recenti intatte", (sroot.rglob("*w001*") and any("w001" in f.name for f in sroot.rglob("*.md"))) and any("s002" in f.name for f in sroot.rglob("*.md")))
    rep2 = cs.run(proj, apply=True, purge_machine=True, older_than=14, distilled_after=14)
    check("idempotente", not rep2["archived"] and not rep2["purged"] and rep2["kept"] == 11, str(rep2))
    # stub riclassificato keep (already-archived) anche se letto direttamente
    a, why = cs.classify(st, stubs[0], date.today(), 14, 14)
    check("classify su stub → keep already-archived", a == "keep" and why == "already-archived")

    print("server: sessions.list / wiki.stats / wiki.lint")
    out = rpc(proj, [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "sessions.list", "arguments": {"limit": 50}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sessions.list", "arguments": {"limit": 50, "include_archived": True}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "wiki.stats", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "wiki.lint", "arguments": {"categories": ["frontmatter"]}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "sessions.read", "arguments": {"id": "100003-cli-claude-s001"}}},
    ])
    r = res(out[2]); ids = [s.get("id") for s in r["sessions"]]
    check("sessions.list esclude archive (11)", r["count"] == 11 and not any("s001" in (i or "") for i in ids), str(ids))
    r = res(out[3])
    check("include_archived=true → 13", r["count"] == 13, str(r["count"]))
    r = res(out[4])
    check("wiki.stats: session_count 11, archived 2", r.get("session_count") == 11 and r.get("archived_session_count") == 2, str({k: r.get(k) for k in ("session_count", "archived_session_count")}))
    r = res(out[5])
    check("wiki.lint: warning session-volume (11 session vs 1 concept)",
          any(w.get("code") == "session-volume" for w in r.get("warnings", [])) and r["summary"]["session_volume"]["sessions"] == 11, str(r.get("summary")))
    r = res(out[6])
    check("sessions.read trova ancora lo stub archiviato per id", "error" not in r and "archived" in json.dumps(r), str(r)[:200])

    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


def test_compact_sessions():
    main()


if __name__ == "__main__":
    main()
