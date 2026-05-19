---
description: Reconcile wiki ↔ codebase: diff vs last snapshot, aggiorna entity toccate, scrive nuovo codebase-snapshot
argument-hint: [--since SHA] [--max-files N] [--dry-run]
allowed-tools: Bash, Read, Write, Grep, Glob, AskUserQuestion
---

# /anja-refresh

Riconcilia il wiki con lo stato corrente del codebase. Lo workflow tipico:

1. Trova ultimo `wiki/sources/codebase-snapshot-*.md`, estrae `git_sha`
2. `git diff <last>..HEAD` per identificare file modificati significativi
3. Per ogni file rilevante: trova entity wiki che lo referenziano, suggerisci update
4. Scrive nuovo `wiki/sources/codebase-snapshot-<date>.md`
5. Log entry

Argomenti: `$ARGUMENTS`

## Pre-flight

- Verifica `.anjawiki/meta.yaml` esista. Se no: errore "Wiki non inizializzato. Lancia /anja-init --analyze prima."
- Verifica che cwd sia un git repo (`git rev-parse --git-dir`). Se no: errore "Refresh richiede git repo (snapshot diff via git log)."

## Workflow

Esegui il workflow refresh definito in `${CLAUDE_PLUGIN_ROOT}/skills/refresh/SKILL.md`. Sintesi:

1. **Trova base SHA**: ultimo `codebase-snapshot-*.md` in `wiki/sources/`, estrai `git_sha:` dal frontmatter.
   - Se non esiste alcuno snapshot: errore "No baseline snapshot. Lancia /anja-init --analyze prima."
   - Se utente passa `--since SHA` esplicito, usa quello.

2. **Diff via Bash**:
   ```bash
   git diff <base-sha>..HEAD --stat
   git diff <base-sha>..HEAD --name-status
   git log <base-sha>..HEAD --oneline | head -20
   ```

3. **Filtra file rilevanti** (ignora rumore):
   - SKIP: `*.lock`, `package-lock.json`, `pnpm-lock.yaml`, `Cargo.lock`, `go.sum`
   - SKIP: `dist/`, `build/`, `out/`, `target/`, `__pycache__/`, `node_modules/`
   - SKIP: `__snapshots__/`, `*.min.js`, `*.bundle.js`
   - KEEP: codice sorgente, config (yaml/json/toml), doc (md), schema (sql/proto), infra (Dockerfile, k8s/, .github/)

4. **Per ogni file KEEP**:
   - `Grep` il path nel wiki/ per trovare entity/concept che lo riferiscono
   - Entity esiste → aggiorna sezione sintesi cambiamenti via `wiki.upsert_entity`
   - Entity non esiste E cambiamento significativo (>50 LOC delta o nuovo file architetturale) → AskUserQuestion se crearla

5. **Se >50 file cambiati**: checkpoint con AskUserQuestion prima del deep-dive (per evitare context bombing). Mostra elenco filtrato, chiedi quali approfondire.

6. **Compila nuovo snapshot** `wiki/sources/codebase-snapshot-<YYYY-MM-DD>.md` via `wiki.upsert_source` con:
   - `subtype: codebase-snapshot`
   - `git_sha: <HEAD>`
   - `analyzed_at: <ISO timestamp>`
   - Sezione "Aree principali analizzate": [[wikilinks]] entity toccate
   - Sezione "Cambiamenti rispetto allo snapshot precedente": commits + file delta

7. **Aggiorna `overview.md`** SE la tesi del progetto è cambiata significativamente (nuove direzioni, deprecation di area). Altrimenti **skip**.

8. **Aggiorna `index.md`** sotto Sources con link al nuovo snapshot.

9. **Append log entry**: `## [YYYY-MM-DD] refresh | N pagine aggiornate, snapshot <sha-short>`

## Modalità dry-run

Con `--dry-run`: esegui steps 1-3, mostra report "cosa farei" senza scrivere file. Utile per preview.

## Output finale

```
✓ Refresh completato.
  Base snapshot:      <date>-<sha-short>
  Commits analizzati: <N>
  File rilevanti:     <K> (su <M> totali)
  Entity aggiornate:  <X>
  Entity nuove:       <Y>
  Snapshot scritto:   wiki/sources/codebase-snapshot-<date>.md
  Log entry:          aggiunta
```

Se cambiamenti grossi su aree non documentate, evidenzia in cima con istruzioni concrete.
