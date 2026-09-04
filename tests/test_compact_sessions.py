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

from _helpers import cov_env

PLUGIN = Path(__file__).resolve().parents[1]
PYTHON = os.environ.get("ANJA_TEST_PYTHON") or sys.executable
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
    old = (date.today() - timedelta(days=20)).isoformat()   # > 14 (short/distilled) ma < 30 (worth stale)
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
    env = {"ANJA_SCOPE": "project", "ANJA_ROOT": str(proj), "PATH": "/usr/bin:/bin", "HOME": str(proj.parent), **cov_env()}
    allm = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}] + msgs
    p = subprocess.run([PYTHON, str(PLUGIN / "scripts" / "mcp_memory_server.py")],
                       input="\n".join(json.dumps(m) for m in allm) + "\n", capture_output=True, text=True, env=env, timeout=30)
    out = {}
    for line in p.stdout.splitlines():
        if line.startswith("{"):
            d = json.loads(line)
            if "id" in d:
                out[d["id"]] = d
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

    print("v0.30: ritenzione — worth stale, archivio (purge/cap), budget, policy da config, last_compact")
    tmp2 = Path(tempfile.mkdtemp()); proj2 = make_wiki(tmp2); sroot2 = proj2 / ".anjawiki" / "wiki" / "sessions"
    stale = (date.today() - timedelta(days=45)).isoformat()
    d = sroot2 / stale; d.mkdir()
    (d / "100010-cli-claude-ws01.md").write_text(session_md("100010-cli-claude-ws01", stale, msgs=10, duration="30m", summary="- decisione presa"))
    rep = cs.run(proj2, apply=False, purge_machine=True)
    check("worth mai distillata a 45d → archive (col summary), worth a 20d → keep",
          any("ws01" in a["file"] and "worth stale" in a["why"] for a in rep["archived"]) and not any("w001" in a["file"] for a in rep["archived"]), str(rep["archived"]))
    check("policy default nel report", rep["policy"]["archive_worth_after_days"] == 30 and rep["policy"]["archive_max"] == 500)
    # archivio: stub senza summary vecchio → purge; con summary → resta; cap soft
    adir = sroot2 / "archive"
    ancient = (date.today() - timedelta(days=200)).isoformat(); mid = (date.today() - timedelta(days=100)).isoformat()
    def stub(name, day, summary):
        (adir / day).mkdir(parents=True, exist_ok=True)
        body = f"---\ntitle: Session {name}\ntype: session\ncreated: {day}\nupdated: {day}\nid: {name}\ndate: {day}\narchived: true\n---\n\n# Session {name} (archived)\n\n## Summary\n\n{summary}\n"
        (adir / day / f"{name}.md").write_text(body)
    stub("a-nosum-old", ancient, "<!-- Vuoto by design. -->")
    stub("a-sum-old", ancient, "- summary vero")
    stub("a-nosum-mid", mid, "<!-- Vuoto by design. -->")
    stub("a-sum-mid", mid, "- altro summary")
    rep = cs.run(proj2, apply=False, purge_machine=True)
    pa = [p["file"] for p in rep["purged_archive"]]
    check("purge: stub senza summary a 200d via; con summary resta; a 100d resta", any("a-nosum-old" in f for f in pa) and not any("a-sum-old" in f or "a-nosum-mid" in f for f in pa), str(pa))
    rep = cs.run(proj2, apply=False, purge_machine=True, archive_max=1)
    pa = [p["file"] for p in rep["purged_archive"]]
    check("cap soft 1: via anche a-nosum-mid, mai gli stub con summary → over_cap segnalato",
          any("a-nosum-mid" in f for f in pa) and not any("a-sum" in f for f in pa) and rep.get("archive_over_cap", 0) >= 1, str(rep.get("archive_over_cap")) + str(pa))
    rep = cs.run(proj2, apply=False, purge_machine=True, budget=2)
    check("budget 2: al massimo 2 azioni e budget_exhausted", rep["actions"] == 2 and rep.get("budget_exhausted") is True, str(rep["actions"]))
    (proj2 / ".anjawiki" / "config.json").write_text(json.dumps({"sessions": {"archive_worth_after_days": 60, "purge_archive_after_days": 400}}))
    rep = cs.run(proj2, apply=False, purge_machine=True)
    check("policy da config.json: worth 60 → ws01 resta, purge 400 → niente purge",
          not any("ws01" in a["file"] for a in rep["archived"]) and not rep["purged_archive"] and rep["policy"]["archive_worth_after_days"] == 60, str(rep["policy"]))
    (proj2 / ".anjawiki" / "config.json").unlink()
    rep = cs.run(proj2, apply=True, purge_machine=True)
    meta = (proj2 / ".anjawiki" / "meta.yaml").read_text()
    check("--apply: last_compact in meta.yaml + .compact-last", "last_compact:" in meta and (proj2 / ".anjawiki" / ".compact-last").is_file(), meta)
    check("--apply: a-nosum-old cancellato, a-sum-old ancora lì, ws01 archiviato col summary",
          not list(adir.rglob("a-nosum-old.md")) and list(adir.rglob("a-sum-old.md")) and "decisione presa" in next(adir.rglob("100010-cli-claude-ws01.md")).read_text())
    rep = cs.run(proj2, apply=True, purge_machine=True)
    check("idempotente", not rep["archived"] and not rep["purged_archive"])
    # lazy compact a SessionStart
    spec = importlib.util.spec_from_file_location("ss", PLUGIN / "hooks" / "session_start.py")
    ss = importlib.util.module_from_spec(spec); spec.loader.exec_module(ss)
    check("lazy compact: .compact-last appena scritto → skip:recent", ss.compact_lazy_decision(proj2, {}) == "skip:recent")
    (proj2 / ".anjawiki" / ".compact-last").unlink()
    check("lazy compact: nessun .compact-last → spawn", ss.compact_lazy_decision(proj2, {}) == "spawn")
    check("lazy compact: ANJA_COMPACT=0 → skip", ss.compact_lazy_decision(proj2, {"ANJA_COMPACT": "0"}) == "skip:opt-out")
    check("lazy compact: sdk → skip", ss.compact_lazy_decision(proj2, {"CLAUDE_CODE_ENTRYPOINT": "sdk-py"}) == "skip:programmatic")
    r = subprocess.run([PYTHON, str(PLUGIN / "scripts" / "status.py"), "--target", str(proj2 / ".anjawiki")], capture_output=True, text=True, timeout=30)
    st = json.loads(r.stdout).get("sessions", {})
    check("status.py: sessions.active/archived/last_compact", st.get("archived", 0) >= 3 and st.get("last_compact"), str(st))
    shutil.rmtree(tmp2, ignore_errors=True)

    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


def test_compact_sessions():
    main()


if __name__ == "__main__":
    main()
