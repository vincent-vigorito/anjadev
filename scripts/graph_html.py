#!/usr/bin/env python3
"""graph_html.py — genera `<wiki>/graph.html` standalone Cytoscape visualizer.

Single-file output: Cytoscape.js da CDN, dati JSON embedded, sidebar sinistra
con search FTS client-side + filtri kind/type + toggle archi semantici, pannello
destro con dettagli sul nodo cliccato.

No server, no build step: apri il file nel browser.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Optional


_SCRIPTS_DIR = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_cytoscape_elements(report: dict) -> dict:
    """Trasforma report → {nodes, edges} formato Cytoscape."""
    if "error" in report:
        return {"nodes": [], "edges": [], "_error": report["error"]}

    wiki_nodes: dict[str, dict] = {}

    for g in report.get("god_nodes") or []:
        wiki_nodes[g["slug"]] = {
            "id": g["slug"],
            "label": g["slug"].split(":")[-1],
            "kind": "wiki",
            "page_type": g["page_type"],
            "degree": g["total"],
            "incoming": g["incoming"],
            "outgoing": g["outgoing"],
        }

    for c in report.get("clusters") or []:
        for slug in c["members"]:
            if slug not in wiki_nodes:
                wiki_nodes[slug] = {
                    "id": slug,
                    "label": slug.split(":")[-1],
                    "kind": "wiki",
                    "page_type": "page",
                    "degree": 0, "incoming": 0, "outgoing": 0,
                }
            wiki_nodes[slug]["cluster"] = c["label"]

    for o in report.get("orphans") or []:
        if o["slug"] not in wiki_nodes:
            wiki_nodes[o["slug"]] = {
                "id": o["slug"],
                "label": o["slug"].split(":")[-1],
                "kind": "wiki",
                "page_type": o["page_type"],
                "degree": 0, "incoming": 0, "outgoing": 0,
                "orphan": True,
            }

    sem_nb = report.get("semantic_neighbors") or {}
    for slug in sem_nb:
        if slug not in wiki_nodes:
            wiki_nodes[slug] = {
                "id": slug, "label": slug.split(":")[-1],
                "kind": "wiki", "page_type": "page",
                "degree": 0, "incoming": 0, "outgoing": 0,
            }
        for nb in sem_nb[slug]:
            target_slug = nb["slug"]
            if target_slug not in wiki_nodes:
                wiki_nodes[target_slug] = {
                    "id": target_slug, "label": target_slug.split(":")[-1],
                    "kind": "wiki", "page_type": nb.get("page_type", "page"),
                    "degree": 0, "incoming": 0, "outgoing": 0,
                }

    code_nodes: dict[str, dict] = {}
    for slug, anchors in (report.get("wiki_code_anchors") or {}).items():
        for a in anchors:
            cid = f"code::{a['file_path']}"
            if a.get("line_range") and a["line_range"][0]:
                cid = f"{cid}::{a['line_range'][0]}-{a['line_range'][1]}"
            label = Path(a["file_path"]).name
            if a.get("func_name"):
                label = f"{label} :: {a['func_name']}"
            code_nodes[cid] = {
                "id": cid, "label": label, "kind": "code",
                "file_path": a["file_path"], "lang": a.get("lang", ""),
                "func_name": a.get("func_name", ""), "line_range": a.get("line_range", []),
            }

    nodes = [{"data": n} for n in wiki_nodes.values()] + [{"data": n} for n in code_nodes.values()]

    edges = []
    eid = 0
    for e in report.get("explicit_edges") or []:
        eid += 1
        edges.append({"data": {"id": f"e{eid}", "source": e["from"], "target": e["to"], "edge_kind": "explicit"}})

    seen_sem: set[tuple[str, str]] = set()
    for slug, neighbors in sem_nb.items():
        for nb in neighbors:
            pair = tuple(sorted([slug, nb["slug"]]))
            if pair in seen_sem:
                continue
            seen_sem.add(pair)
            score = nb["score"]
            edge_kind = "semantic_strong" if score >= 0.8 else (
                "semantic_medium" if score >= 0.65 else "semantic_weak"
            )
            eid += 1
            edges.append({"data": {"id": f"e{eid}", "source": pair[0], "target": pair[1],
                                   "edge_kind": edge_kind, "score": score}})

    for slug, anchors in (report.get("wiki_code_anchors") or {}).items():
        for a in anchors:
            cid = f"code::{a['file_path']}"
            if a.get("line_range") and a["line_range"][0]:
                cid = f"{cid}::{a['line_range'][0]}-{a['line_range'][1]}"
            eid += 1
            score = a["score"]
            edge_kind = "anchor_strong" if score >= 0.75 else "anchor_medium"
            edges.append({"data": {"id": f"e{eid}", "source": slug, "target": cid,
                                   "edge_kind": edge_kind, "score": score}})

    return {"nodes": nodes, "edges": edges, "stats": report.get("stats", {})}


def write_html(root: Path, report: dict, target: Optional[Path] = None) -> Path:
    """Genera graph.html standalone in `<wiki>/graph.html`."""
    if target is None:
        target = root / ".anjawiki" / "wiki" / "graph.html"

    elements = _build_cytoscape_elements(report)
    title = root.name or "Knowledge Graph"
    template_path = _SCRIPTS_DIR / "graph_html_template.html"
    template = template_path.read_text(encoding="utf-8")
    html = template.replace("__TITLE__", title).replace(
        "__DATA__", json.dumps(elements, ensure_ascii=False)
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def build_and_write_html(root: Path, target: Optional[Path] = None) -> dict:
    """One-shot: build report (full) + write html. Helper per il tool MCP."""
    gr = _load("graph_report")
    report = gr.build_report(root, include_sessions=False)
    if "error" in report:
        return report
    path = write_html(root, report, target=target)
    elements = _build_cytoscape_elements(report)
    return {
        "written": str(path),
        "nodes_count": len(elements["nodes"]),
        "edges_count": len(elements["edges"]),
        "stats": report.get("stats", {}),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Generate standalone graph.html from embedded wiki")
    ap.add_argument("root", help="project root")
    ap.add_argument("--target", help="output path override")
    args = ap.parse_args()
    root = Path(args.root).expanduser().resolve()
    target = Path(args.target).expanduser().resolve() if args.target else None
    result = build_and_write_html(root, target=target)
    print(json.dumps(result, indent=2, default=str))
