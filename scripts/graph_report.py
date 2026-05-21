#!/usr/bin/env python3
"""graph_report.py — compute knowledge graph report sul wiki embeddato.

Combina:
  - **Grafo esplicito**: parse `[[wikilink]]` da ogni body wiki → edges
  - **Grafo semantico**: k-NN cross-kind dallo spazio embedding condiviso
  - **God nodes**: top-N pagine con più backlinks espliciti (degree centrality)
  - **Surprise edges**: pairs wiki-wiki con similarity > threshold MA niente
    wikilink esplicito tra loro
  - **Wiki↔code anchors**: per ogni entity wiki, top-K code chunks vicini
  - **Orphans**: pagine senza incoming/outgoing link AND niente semantic neighbor
  - **Clusters**: connected components sul grafo combinato (stdlib only)

Output:
  - dict structured (per uso programmatico)
  - `<wiki>/GRAPH_REPORT.md` markdown leggibile (per injection in agent context)

Stdlib only. Riusa code_db + skill_parser + embed_providers locali.
"""

from __future__ import annotations

import importlib.util
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_SCRIPTS_DIR = Path(__file__).resolve().parent

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# Explicit graph: wikilinks parsing
# ============================================================

def _slug_to_target(slug: str, all_slugs: set[str]) -> Optional[str]:
    """Resolve wikilink target: prima exact match, poi suffix (entities:auth-service)."""
    slug = slug.strip()
    if slug in all_slugs:
        return slug
    # Match per suffix dopo `:` (entities:foo → foo o entities:foo)
    for s in all_slugs:
        if s.endswith(f":{slug}") or s.split(":")[-1] == slug:
            return s
    return None


def _extract_wikilinks(body: str) -> list[str]:
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(body)]


def _build_explicit_edges(pages: dict[str, dict]) -> list[tuple[str, str]]:
    """Per ogni pagina parsea wikilinks e produce edges (source_slug, target_slug)."""
    all_slugs = set(pages.keys())
    edges = []
    for slug, page in pages.items():
        body = page.get("body", "") or ""
        for link in _extract_wikilinks(body):
            target = _slug_to_target(link, all_slugs)
            if target and target != slug:
                edges.append((slug, target))
    return edges


# ============================================================
# Connected components (stdlib union-find)
# ============================================================

class _DSU:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def _connected_components(nodes: set[str], edges: list[tuple[str, str]]) -> dict[int, list[str]]:
    dsu = _DSU()
    for n in nodes:
        dsu.find(n)
    for a, b in edges:
        if a in nodes and b in nodes:
            dsu.union(a, b)
    groups: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        groups[dsu.find(n)].append(n)
    return {i: sorted(members) for i, members in enumerate(sorted(groups.values(), key=lambda g: -len(g)))}


# ============================================================
# Main report builder
# ============================================================

def build_report(
    root: Path,
    top_n_god: int = 8,
    surprise_threshold: float = 0.72,
    anchor_threshold: float = 0.6,
    k_per_node: int = 5,
    include_sessions: bool = False,
) -> dict:
    """Computa il report completo. Ritorna dict; non scrive file (vedi `write_report`).

    Default thresholds tarati per modelli embedding moderni (OpenAI/Voyage/local 1024d).
    """
    code_db = _load("code_db")
    embed_providers = _load("embed_providers")

    anjawiki = root / ".anjawiki"
    if not (anjawiki / "code-index.db").exists():
        return {"error": "index not built — run wiki.embed (and code.reindex) first"}

    provider = embed_providers.get_provider()
    if provider is None:
        return {"error": "no embed provider configured"}

    db = code_db.open_db(anjawiki, dim=provider.dim, create_if_missing=False)

    try:
        # 1. Carica tutte le wiki pages + body raw da disco
        wiki_rows = code_db.list_wiki_pages(db)
        pages: dict[str, dict] = {}
        for r in wiki_rows:
            slug = r["slug"]
            if not include_sessions and r["page_type"] == "session":
                continue
            md_path = Path(r["file_path"])
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Strip frontmatter for wikilink parsing
            body = text
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end > 0:
                    body = text[end + 4:].lstrip("\n")
            pages[slug] = {
                "id": r["id"],
                "page_type": r["page_type"],
                "file_path": r["file_path"],
                "body": body,
            }

        if not pages:
            return {"error": "no wiki pages in index"}

        # 2. Edges espliciti via wikilink parsing
        explicit_edges = _build_explicit_edges(pages)
        explicit_edges_set = {(a, b) for a, b in explicit_edges}

        # 3. Degree centrality (incoming = "quanto è citato")
        in_degree = Counter(b for a, b in explicit_edges)
        out_degree = Counter(a for a, b in explicit_edges)
        total_degree = Counter()
        for s in pages:
            total_degree[s] = in_degree[s] + out_degree[s]

        god_nodes = [
            {
                "slug": s,
                "page_type": pages[s]["page_type"],
                "incoming": in_degree[s],
                "outgoing": out_degree[s],
                "total": total_degree[s],
            }
            for s, _ in total_degree.most_common(top_n_god)
            if total_degree[s] > 0
        ]

        # 4. Semantic neighbors per ogni pagina wiki
        semantic_neighbors: dict[str, list[dict]] = {}
        wiki_code_anchors: dict[str, list[dict]] = {}
        surprise_edges: list[dict] = []
        seen_pairs: set[tuple[str, str]] = set()

        for slug, page in pages.items():
            self_vec = code_db.get_embedding_vector(db, page["id"])
            if not self_vec:
                continue
            neighbors = code_db.vector_search(
                db,
                query_vec=self_vec,
                limit=k_per_node + 1,
                exclude_id=page["id"],
            )
            sem_list = []
            anchor_list = []
            for r in neighbors:
                score = 1.0 - float(r["distance"])
                if r["kind"] == "wiki":
                    target_slug = r["func_name"]
                    if target_slug == slug or target_slug not in pages:
                        continue
                    sem_list.append({
                        "slug": target_slug,
                        "score": round(score, 4),
                        "page_type": r["lang"],
                    })
                    # Surprise: high similarity but no explicit link in either direction
                    if score >= surprise_threshold:
                        pair = tuple(sorted([slug, target_slug]))
                        if pair in seen_pairs:
                            continue
                        has_link = (slug, target_slug) in explicit_edges_set or (target_slug, slug) in explicit_edges_set
                        if not has_link:
                            surprise_edges.append({
                                "a": pair[0],
                                "b": pair[1],
                                "score": round(score, 4),
                                "a_type": pages[pair[0]]["page_type"],
                                "b_type": pages[pair[1]]["page_type"],
                            })
                            seen_pairs.add(pair)
                elif r["kind"] == "code" and score >= anchor_threshold:
                    anchor_list.append({
                        "file_path": r["file_path"],
                        "line_range": [r["line_start"], r["line_end"]],
                        "func_name": r["func_name"],
                        "lang": r["lang"],
                        "score": round(score, 4),
                    })
            if sem_list:
                semantic_neighbors[slug] = sem_list[:k_per_node]
            if anchor_list:
                wiki_code_anchors[slug] = anchor_list[:3]

        surprise_edges.sort(key=lambda x: -x["score"])

        # 5. Orphans: no explicit edges AND no semantic neighbor above threshold
        weak_threshold = 0.55
        orphans = []
        for slug, page in pages.items():
            if total_degree[slug] > 0:
                continue
            sem = semantic_neighbors.get(slug, [])
            if any(s["score"] >= weak_threshold for s in sem):
                continue
            orphans.append({"slug": slug, "page_type": page["page_type"]})

        # 6. Clusters via connected components (esplicito + semantico forte)
        sem_edges = []
        for slug, neighbors in semantic_neighbors.items():
            for n in neighbors:
                if n["score"] >= 0.7:  # strong semantic = use as cluster edge
                    sem_edges.append((slug, n["slug"]))
        all_edges = explicit_edges + sem_edges
        clusters = _connected_components(set(pages.keys()), all_edges)

        # Filtra cluster singleton (nodo singolo) per leggibilità
        named_clusters = []
        for cid, members in clusters.items():
            if len(members) >= 2:
                # Etichetta cluster con il god node interno (più cited tra i membri)
                label = max(members, key=lambda m: total_degree.get(m, 0))
                named_clusters.append({
                    "id": cid,
                    "label": label,
                    "size": len(members),
                    "members": members,
                })
        named_clusters.sort(key=lambda c: -c["size"])

        # 7. Stats summary
        stats = {
            "wiki_pages": len(pages),
            "explicit_edges": len(explicit_edges),
            "semantic_edges_strong": len(sem_edges),
            "god_nodes_count": len(god_nodes),
            "surprise_edges_count": len(surprise_edges),
            "wiki_code_anchors_count": sum(len(v) for v in wiki_code_anchors.values()),
            "orphans_count": len(orphans),
            "clusters_count": len(named_clusters),
            "embed_provider": provider.name,
            "embed_model": provider.model,
            "embed_dim": provider.dim,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "stats": stats,
            "god_nodes": god_nodes,
            "surprise_edges": surprise_edges,
            "wiki_code_anchors": wiki_code_anchors,
            "orphans": orphans,
            "clusters": named_clusters,
            "semantic_neighbors": semantic_neighbors,
            "explicit_edges": [{"from": a, "to": b} for a, b in explicit_edges],
        }
    finally:
        db.close()


# ============================================================
# Markdown rendering
# ============================================================

def render_markdown(report: dict, project_name: str = "") -> str:
    """Renderizza il report come GRAPH_REPORT.md leggibile dall'agent."""
    stats = report.get("stats", {})
    out = [
        f"---",
        f"title: Knowledge Graph Report",
        f"type: analysis",
        f"transient: true",
        f"generated_at: {stats.get('generated_at', '')}",
        f"---",
        f"",
        f"# Knowledge Graph Report"
        + (f" — {project_name}" if project_name else ""),
        f"",
        f"> Auto-generato da `graph.report`. Layer combinato: wikilinks espliciti + similarity semantica.",
        f"",
        f"## Stats",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Wiki pages indexed | {stats.get('wiki_pages', 0)} |",
        f"| Explicit edges (`[[wikilink]]`) | {stats.get('explicit_edges', 0)} |",
        f"| Strong semantic edges (sim ≥ 0.7) | {stats.get('semantic_edges_strong', 0)} |",
        f"| God nodes | {stats.get('god_nodes_count', 0)} |",
        f"| Surprise edges | {stats.get('surprise_edges_count', 0)} |",
        f"| Wiki↔code anchors | {stats.get('wiki_code_anchors_count', 0)} |",
        f"| Orphans | {stats.get('orphans_count', 0)} |",
        f"| Clusters (≥2 nodes) | {stats.get('clusters_count', 0)} |",
        f"| Embedding provider | {stats.get('embed_provider', '?')} / {stats.get('embed_model', '?')} ({stats.get('embed_dim', '?')}d) |",
        f"",
    ]

    # God nodes
    out.append("## God nodes (top-cited pages)")
    out.append("")
    gn = report.get("god_nodes") or []
    if gn:
        out.append("Pagine più centrali nel grafo esplicito. Usa queste come entry-point per orientarti.")
        out.append("")
        out.append("| Slug | Type | In | Out | Total |")
        out.append("|---|---|---|---|---|")
        for g in gn:
            out.append(f"| [[{g['slug']}]] | {g['page_type']} | {g['incoming']} | {g['outgoing']} | {g['total']} |")
    else:
        out.append("_Nessuna pagina con backlinks. Probabilmente wiki giovane o sotto-collegato._")
    out.append("")

    # Surprise edges
    out.append("## Surprise edges (high similarity, no explicit link)")
    out.append("")
    se = report.get("surprise_edges") or []
    if se:
        out.append("Pagine semanticamente vicine ma non connesse via `[[wikilink]]`. **Candidati per formalizzare** o consolidare.")
        out.append("")
        for s in se[:15]:
            out.append(f"- [[{s['a']}]] ({s['a_type']}) ↔ [[{s['b']}]] ({s['b_type']}) — score `{s['score']:.3f}`")
    else:
        out.append("_Nessun candidato sopra threshold._")
    out.append("")

    # Wiki ↔ code anchors
    out.append("## Wiki ↔ code anchors (semantic mapping entity → file)")
    out.append("")
    anchors = report.get("wiki_code_anchors") or {}
    if anchors:
        out.append("Per ogni entity/concept, top-3 chunk di codice semanticamente vicini. Suggerimenti per la sezione \"Apparizioni\" delle entity.")
        out.append("")
        for slug in sorted(anchors.keys()):
            out.append(f"### [[{slug}]]")
            out.append("")
            for a in anchors[slug]:
                func = f" · `{a['func_name']}`" if a.get("func_name") else ""
                lines = f"L{a['line_range'][0]}-{a['line_range'][1]}" if a.get('line_range') and a['line_range'][0] else ""
                out.append(f"- `{a['file_path']}{(':' + lines) if lines else ''}`{func} — score `{a['score']:.3f}` ({a.get('lang', '?')})")
            out.append("")
    else:
        out.append("_Nessun anchor wiki↔code. Indicizza il codebase con `code.reindex` per popolare._")
    out.append("")

    # Clusters
    out.append("## Clusters (connected components)")
    out.append("")
    cl = report.get("clusters") or []
    if cl:
        out.append("Gruppi di pagine connesse via wikilink + similarity semantica. Cluster ≥2 mostrati.")
        out.append("")
        for c in cl[:20]:
            members = ", ".join(f"[[{m}]]" for m in c["members"][:10])
            more = f" (+{len(c['members']) - 10} more)" if len(c["members"]) > 10 else ""
            out.append(f"- **{c['label']}** ({c['size']} nodes): {members}{more}")
    else:
        out.append("_Nessun cluster (tutte pagine isolate)._")
    out.append("")

    # Orphans
    out.append("## Orphans (isolated pages)")
    out.append("")
    orphans = report.get("orphans") or []
    if orphans:
        out.append("Pagine senza backlinks e senza neighbor semantico forte. Da rivedere: integrare nel wiki o cancellare.")
        out.append("")
        for o in orphans:
            out.append(f"- [[{o['slug']}]] ({o['page_type']})")
    else:
        out.append("_Nessuna pagina orfana. Buono._")
    out.append("")

    out.append("---")
    out.append("")
    out.append("**Cosa fare con questo report:**")
    out.append("")
    out.append("1. **God nodes** = leggi prima per orientarti su un nuovo argomento.")
    out.append("2. **Surprise edges** = decidi se aggiungere `[[wikilink]]` espliciti.")
    out.append("3. **Wiki↔code anchors** = aggiorna sezione \"Apparizioni\" delle entity.")
    out.append("4. **Orphans** = integra o rimuovi.")
    out.append("5. **Clusters** = aree tematiche del progetto. Cluster troppo grandi → spezzali.")
    out.append("")

    return "\n".join(out)


def write_report(root: Path, report: dict, target: Optional[Path] = None) -> Path:
    """Scrive GRAPH_REPORT.md sotto `<root>/.anjawiki/wiki/`. Ritorna il path."""
    if target is None:
        target = root / ".anjawiki" / "wiki" / "GRAPH_REPORT.md"
    project_name = root.name
    md = render_markdown(report, project_name=project_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")
    return target


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Generate knowledge graph report from embedded wiki")
    ap.add_argument("root", help="project root (parent of .anjawiki/)")
    ap.add_argument("--top-god", type=int, default=8)
    ap.add_argument("--surprise-threshold", type=float, default=0.72)
    ap.add_argument("--anchor-threshold", type=float, default=0.6)
    ap.add_argument("--k-per-node", type=int, default=5)
    ap.add_argument("--include-sessions", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="Print JSON, don't write GRAPH_REPORT.md")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    report = build_report(
        root,
        top_n_god=args.top_god,
        surprise_threshold=args.surprise_threshold,
        anchor_threshold=args.anchor_threshold,
        k_per_node=args.k_per_node,
        include_sessions=args.include_sessions,
    )
    if "error" in report:
        print(json.dumps(report, indent=2))
        raise SystemExit(1)

    if args.no_write:
        print(json.dumps(report.get("stats", {}), indent=2))
    else:
        target = write_report(root, report)
        print(json.dumps({"written": str(target), "stats": report.get("stats", {})}, indent=2))
