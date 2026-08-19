#!/usr/bin/env python3
"""F-anjadev-steward pezzo C — steward.py con LLM mockato (ANJA_STEWARD_BIN → script finto).

§7.3: 3 session worth + short; --apply con mock (upsert su slug esistente APPESO, pagina
nuova con 2 session citate, append_overview ≤80 parole sotto ## Recent + stale_after su
overview vecchio, log_append, analysis/delete rifiutate, max 3) → distilled; JSON rotto →
zero patch, non distilled, rc 0; nothing_to_promote → distilled; --propose → pending file,
wiki intatto, pagina nuova rifiutata in lazy; --apply-pending applica; lock; opt-out;
lazy SessionStart decision; compact delle distilled vecchie.

Standalone: python3 tests/test_steward.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
PY = "/opt/homebrew/opt/python@3.12/bin/python3.12" if Path("/opt/homebrew/opt/python@3.12/bin/python3.12").is_file() else sys.executable
sys.path.insert(0, str(PLUGIN / "hooks"))
PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {label}")
    else:
        FAIL += 1; print(f"  ✗ {label} {detail}")


def session_md(sid, day, msgs, duration, prompts, summary="", tools="[Read, Edit]"):
    fm = [f"title: Session {sid}", "type: session", f"created: {day}", f"updated: {day}", f"id: {sid}",
          "transcript_path: /t/x.jsonl", f"duration: {duration}", "scope: project", "agent: cli-claude", "harness: claude",
          "entrypoint: cli", f"date: {day}", "end_reason: prompt_input_exit", f"messages_user: {msgs}", "messages_assistant: 9",
          f"tools_used: {tools}"]
    return ("---\n" + "\n".join(fm) + "\n---\n\n# Session " + sid + "\n\n## Summary\n\n" + (summary or "<!-- Vuoto by design. -->")
            + "\n\n## Stats\n\n- x\n\n## User prompts\n\n" + "\n".join(f"- {p}" for p in prompts) + "\n\n## Notes\n\n<!-- -->\n")


def make_wiki(tmp: Path) -> Path:
    proj = tmp / "proj"; wiki = proj / ".anjawiki" / "wiki"
    for d in ("sessions", "concepts", "entities"): (wiki / d).mkdir(parents=True)
    (proj / ".anjawiki" / "meta.yaml").write_text("name: proj\n")
    (wiki / "concepts" / "core-split.md").write_text("---\ntitle: Core split\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-01\n---\n\n# Core split\n\n## Summary\n\nTesto umano originale.\n\n## Dettagli\n\nd\n")
    old = (date.today() - timedelta(days=100)).isoformat()
    (wiki / "overview.md").write_text(f"---\ntitle: Overview\ntype: overview\ncreated: 2026-01-01\nupdated: {old}\n---\n\n# Overview\n\n## Tesi\n\nLa tesi originale resta.\n")
    (wiki / "log.md").write_text("# log\n\n## [2026-06-01] init | start\n")
    (wiki / "index.md").write_text("# index\n")
    today = date.today().isoformat(); yday = (date.today() - timedelta(days=1)).isoformat()
    d = wiki / "sessions" / today; d.mkdir()
    for i in range(3):
        (d / f"10000{i}-cli-claude-w00{i}.md").write_text(session_md(f"10000{i}-cli-claude-w00{i}", today, 6, "20m",
            [f"decisione sullo split del plugin {i}", "facciamo il design del runtime", "bump e deploy"], summary="- fatto split"))
    d2 = wiki / "sessions" / yday; d2.mkdir()
    for i in range(4):   # short recenti (keep)
        (d2 / f"12000{i}-cli-claude-s00{i}.md").write_text(session_md(f"12000{i}-cli-claude-s00{i}", yday, 1, "40s", ["ok"]))
    oldd = (date.today() - timedelta(days=30)).isoformat()
    d3 = wiki / "sessions" / oldd; d3.mkdir()
    for i in range(3):   # short vecchie (compact archive)
        (d3 / f"13000{i}-cli-claude-s10{i}.md").write_text(session_md(f"13000{i}-cli-claude-s10{i}", oldd, 2, "1m", ["x", "y"]))
    return proj


def fake_llm(tmp: Path, reply: str, name="claude") -> Path:
    b = tmp / "bin"; b.mkdir(exist_ok=True)
    f = b / name
    (tmp / "reply.txt").write_text(reply)
    f.write_text(f"#!/bin/sh\ncat '{tmp}/reply.txt'\n"); f.chmod(0o755)
    return f


def run_steward(proj: Path, mode_args: list, env_extra: dict) -> dict:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(proj.parent), "ANJA_JOURNAL": "0"}
    env.update(env_extra)
    r = subprocess.run([PY, str(PLUGIN / "scripts" / "steward.py"), "--root", str(proj)] + mode_args,
                       capture_output=True, text=True, env=env, timeout=60)
    try:
        return json.loads(r.stdout), r.returncode
    except Exception:
        return {"_raw": r.stdout, "_err": r.stderr, "errors": ["no-json"]}, r.returncode


def main():
    tmp = Path(tempfile.mkdtemp()); proj = make_wiki(tmp); wiki = proj / ".anjawiki" / "wiki"
    sessions_today = sorted((wiki / "sessions" / date.today().isoformat()).glob("*.md"))
    sids = [p.stem for p in sessions_today]
    good = json.dumps({"nothing_to_promote": False, "patches": [
        {"action": "upsert_concept", "slug": "core-split", "title": "NUOVO TITOLO", "section": "Summary",
         "body": "Il runtime vive in anja-hub ([[core-split]]).", "rationale": f"decisione in {sids[0]}"},
        {"action": "append_overview", "slug": "", "section": "", "body": "Split completato, anjadev puro.",
         "rationale": sids[1]},
        {"action": "log_append", "slug": "", "section": "", "body": "steward: split distillato", "rationale": "x"},
        {"action": "upsert_analysis", "slug": "zombie", "section": "Summary", "body": "no", "rationale": "no"},
        {"action": "upsert_entity", "slug": "hub-runtime", "title": "Hub runtime", "section": "Sintesi",
         "body": "Server MCP anja_hub_runtime.", "rationale": f"{sids[0]} e {sids[2]}"},
    ]})
    fake = fake_llm(tmp, good)

    print("dry-run: LLM mock, nessuna scrittura")
    rep, rc = run_steward(proj, [], {"ANJA_STEWARD_BIN": str(fake)})
    check("rc 0, 1 cluster (3 worth stesso giorno), 4 short recenti skippate, 3 vecchie fuori finestra",
          rc == 0 and rep["triage"]["clusters"] == 1 and rep["triage"]["skipped"]["too_short"] == 4 and rep["triage"]["skipped"]["out_of_window"] == 3, str(rep.get("triage")))
    c = rep["clusters"][0]
    check("policy: 3 patch ok (max 3), analysis rifiutata, 5ª fuori per max",
          len(c["patches"]) == 3 and any(r["why"].startswith("action-not-allowed") for r in c["rejected"]) and any(r["why"] == "max-patches" for r in c["rejected"]), str(c["rejected"]))
    check("wiki intatto", "Testo umano originale." in (wiki / "concepts/core-split.md").read_text() and "Recent" not in (wiki / "overview.md").read_text())
    check("session non distilled in dry-run", not any("distilled: true" in p.read_text() for p in sessions_today))

    print("--apply")
    rep, rc = run_steward(proj, ["--apply"], {"ANJA_STEWARD_BIN": str(fake)})
    cs_txt = (wiki / "concepts/core-split.md").read_text()
    check("upsert su slug esistente: APPESO, testo umano intatto, titolo non toccato",
          "Testo umano originale." in cs_txt and "Il runtime vive in anja-hub" in cs_txt and "title: Core split" in cs_txt and "NUOVO TITOLO" not in cs_txt and "_(steward," in cs_txt, cs_txt[:500])
    check("generated.by = anjadev/steward", "generated: { by: anjadev/steward/" in cs_txt, cs_txt[:300])
    ov = (wiki / "overview.md").read_text()
    check("overview: ## Recent appeso, tesi intatta, stale_after settato (overview vecchio)",
          "## Recent" in ov and "Split completato" in ov and "La tesi originale resta." in ov and "stale_after:" in ov, ov)
    check("log_append", "steward: split distillato" in (wiki / "log.md").read_text())
    check("pagina nuova hub-runtime creata (rationale cita 2 session) — solo perché la 5ª era oltre max3? no: max3 la esclude",
          not (wiki / "entities" / "hub-runtime.md").exists())
    check("3 session distilled", all("distilled: true" in p.read_text() for p in sessions_today))
    check("compact: short vecchie archiviate, recenti no", rep["compact"]["archived"] == 3 and (wiki / "sessions" / "archive").is_dir()
          and all(p.exists() for p in (wiki / "sessions" / (date.today() - timedelta(days=1)).isoformat()).glob("*.md")), str(rep["compact"]))
    check(".steward-last scritto", (proj / ".anjawiki" / ".steward-last").is_file())
    rep2, _ = run_steward(proj, ["--apply"], {"ANJA_STEWARD_BIN": str(fake)})
    check("secondo giro: 0 cluster (già distilled)", rep2["triage"]["clusters"] == 0 and rep2["triage"]["skipped"]["already_distilled"] == 3, str(rep2.get("triage")))

    print("pagina nuova con 2 session citate (patch entro max 3)")
    proj2 = make_wiki(tmp / "b"); wiki2 = proj2 / ".anjawiki" / "wiki"
    sids2 = [p.stem for p in sorted((wiki2 / "sessions" / date.today().isoformat()).glob("*.md"))]
    newp = json.dumps({"nothing_to_promote": False, "patches": [
        {"action": "upsert_entity", "slug": "hub-runtime", "title": "Hub runtime", "section": "Sintesi", "body": "Server MCP.", "rationale": f"{sids2[0]} e {sids2[2]}"},
        {"action": "upsert_entity", "slug": "solo-uno", "title": "Solo uno", "section": "Sintesi", "body": "x", "rationale": f"{sids2[0]}"},
        {"action": "append_overview", "slug": "", "section": "", "body": " ".join(["parola"] * 90), "rationale": "y"},
    ]})
    fake2 = fake_llm(tmp / "b", newp)
    rep, rc = run_steward(proj2, ["--apply"], {"ANJA_STEWARD_BIN": str(fake2)})
    c = rep["clusters"][0]
    check("nuova entity creata con title, 1-session rifiutata, Recent >80 parole rifiutato",
          (wiki2 / "entities" / "hub-runtime.md").is_file() and "title: Hub runtime" in (wiki2 / "entities" / "hub-runtime.md").read_text()
          and any(r["why"].startswith("new-page-needs") for r in c["rejected"]) and any(r["why"].startswith("recent>") for r in c["rejected"]), str(c["rejected"]))

    print("JSON rotto / nothing_to_promote / no CLI")
    proj3 = make_wiki(tmp / "c"); wiki3 = proj3 / ".anjawiki" / "wiki"
    bad = fake_llm(tmp / "c", "Ecco le patch: { non json")
    rep, rc = run_steward(proj3, ["--apply"], {"ANJA_STEWARD_BIN": str(bad)})
    check("JSON rotto: rc 0, zero patch, errore loggato, NON distilled",
          rc == 0 and rep["patches_applied"] == 0 and any("invalid-json" in e for e in rep["errors"])
          and not any("distilled: true" in p.read_text() for p in (wiki3 / "sessions" / date.today().isoformat()).glob("*.md")), str(rep["errors"]))
    nothing = fake_llm(tmp / "c", json.dumps({"nothing_to_promote": True, "patches": []}))
    rep, rc = run_steward(proj3, ["--apply"], {"ANJA_STEWARD_BIN": str(nothing)})
    check("nothing_to_promote: distilled settato, zero patch", rep["patches_applied"] == 0 and rep["distilled"] == 3)
    proj4 = make_wiki(tmp / "d")
    rep, rc = run_steward(proj4, ["--apply"], {"ANJA_STEWARD_BIN": "none"})
    check("nessun CLI: errore no-llm-cli, rc 0, nulla distillato", rc == 0 and any("no-llm-cli" in e for e in rep["errors"]) and rep["distilled"] == 0, str(rep["errors"]))

    print("--propose (lazy) → pending, poi --apply-pending")
    proj5 = make_wiki(tmp / "e"); wiki5 = proj5 / ".anjawiki" / "wiki"
    sids5 = [p.stem for p in sorted((wiki5 / "sessions" / date.today().isoformat()).glob("*.md"))]
    prop = json.dumps({"nothing_to_promote": False, "patches": [
        {"action": "upsert_concept", "slug": "core-split", "section": "Summary", "body": "Appendice dallo steward.", "rationale": sids5[0]},
        {"action": "upsert_entity", "slug": "nuova", "title": "Nuova", "section": "Sintesi", "body": "n", "rationale": f"{sids5[0]} {sids5[1]}"},
    ]})
    fake5 = fake_llm(tmp / "e", prop)
    rep, rc = run_steward(proj5, ["--propose"], {"ANJA_STEWARD_BIN": str(fake5)})
    pend = proj5 / ".anjawiki" / ".steward-pending.json"
    check("pending scritto, wiki intatto, pagina nuova rifiutata in lazy",
          pend.is_file() and "Appendice" not in (wiki5 / "concepts/core-split.md").read_text()
          and any(r["why"] == "new-page-not-allowed-in-lazy-mode" for r in rep["clusters"][0]["rejected"]), str(rep["clusters"][0].get("rejected")))
    check("propose: sessioni NON distilled (aspettano l'apply)", not any("distilled: true" in p.read_text() for p in (wiki5 / "sessions" / date.today().isoformat()).glob("*.md")))
    pd = json.loads(pend.read_text())
    rep, rc = run_steward(proj5, ["--apply-pending", pd["clusters"][0]["id"]], {"ANJA_STEWARD_BIN": "none"})
    check("apply-pending: patch appesa senza LLM, pending rimosso, distilled",
          "Appendice dallo steward." in (wiki5 / "concepts/core-split.md").read_text() and not pend.exists() and rep["distilled"] == 3, str(rep)[:300])

    print("lock / opt-out / lazy decision")
    proj6 = make_wiki(tmp / "f")
    (proj6 / ".anjawiki" / ".steward.lock").write_text("1")
    rep, rc = run_steward(proj6, ["--apply"], {"ANJA_STEWARD_BIN": str(fake)})
    check("lock held → esce subito", "lock held" in rep["errors"] and rc == 1)
    (proj6 / ".anjawiki" / ".steward.lock").unlink()
    rep, rc = run_steward(proj6, ["--apply"], {"ANJA_STEWARD_BIN": str(fake), "ANJA_STEWARD": "0"})
    check("ANJA_STEWARD=0 → opt-out", any("opt-out" in e for e in rep["errors"]))
    spec = importlib.util.spec_from_file_location("ss", PLUGIN / "hooks" / "session_start.py")
    ss = importlib.util.module_from_spec(spec); spec.loader.exec_module(ss)
    check("lazy: nessun .steward-last → spawn", ss.steward_lazy_decision(proj6, {}) == "spawn")
    (proj6 / ".anjawiki" / ".steward-last").write_text(str(time.time() - 3600))
    check("lazy: last 1h fa → skip:recent", ss.steward_lazy_decision(proj6, {}) == "skip:recent")
    (proj6 / ".anjawiki" / ".steward-last").write_text(str(time.time() - 25 * 3600))
    check("lazy: last 25h fa → spawn", ss.steward_lazy_decision(proj6, {}) == "spawn")
    check("lazy: ANJA_STEWARD=0 → skip", ss.steward_lazy_decision(proj6, {"ANJA_STEWARD": "0"}) == "skip:opt-out")
    check("lazy: sdk entrypoint → skip", ss.steward_lazy_decision(proj6, {"CLAUDE_CODE_ENTRYPOINT": "sdk-py"}) == "skip:programmatic")

    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


def test_steward():
    main()


if __name__ == "__main__":
    main()
