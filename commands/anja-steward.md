---
description: Wiki steward — rivedi/applica le patch distillate dai journal (distill + compact)
argument-hint: [--apply | --propose | --since 7d]
allowed-tools: Bash, Read, AskUserQuestion
---

# /anja-steward

Lo steward promuove al wiki ciò che dalle sessioni deve restare (decisioni, entità,
concetti) e toglie i diari dal retrieval. **Tre passi**: triage (zero LLM) → una call LLM
per cluster → scrittura fail-closed (mai riscrive overview, mai `analysis`, mai delete,
pagine esistenti solo in append). Design: `anja-anjadev-steward-design.md` (repo ops).

Argomenti: `$ARGUMENTS`

## Pre-flight

- `.anjawiki/meta.yaml` deve esistere (altrimenti: "Wiki non inizializzato, /anja-init").
- `ANJA_STEWARD=0` nell'env = opt-out: spiegalo e fermati.

## Workflow

1. **Pending?** Se esiste `.anjawiki/.steward-pending.json` (scritto dal lazy SessionStart
   o da un `--propose`), leggilo e mostra all'utente, per cluster: id, session coinvolte,
   le patch (action · slug · section · body · rationale) e le `rejected` con il motivo.
2. Altrimenti (o se l'utente vuole un giro fresco) lancia il dry-run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/steward.py" --root . [--since 7d]
   ```
   Stdout = JSON: `triage` (cluster/skipped), `clusters[].patches` proposte.
   Mostrale come sopra. Se `errors` contiene `no-llm-cli`: spiega `ANJA_STEWARD_BIN`
   (claude|grok|codex|path) e fermati.
3. **Conferma per cluster** con `AskUserQuestion` (apply / skip). Le patch su pagine
   esistenti vengono **appese** (con stamp `steward, <data>, <cluster>`), una pagina nuova
   nasce solo se la rationale cita ≥2 session; `append_overview` va sotto `## Recent`.
4. Applica:
   - se c'era un pending: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/steward.py" --root . --apply-pending <id…>`
   - altrimenti: `… --apply` (rifà triage+LLM e scrive; oppure `--propose` + `--apply-pending`
     per avere esattamente le patch appena viste).
   Le session dei cluster diventano `distilled: true`; il compact archivia distilled/short
   vecchie (`sessions/archive/`, stub con Summary + transcript_path).
5. Riporta: pagine toccate, patch rifiutate e perché, session distillate/archiviate.

Niente LLM per il triage; una call per cluster (max 5) per il distill. Non girare mai a
SessionEnd: lo steward è periodico (lazy 24h in `--propose`, routine notturna in `--apply`).
