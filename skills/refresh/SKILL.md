---
name: refresh
description: Workflow per riconciliare il wiki anja con lo stato del codebase. Da usare quando l'utente esegue /anja-refresh, o chiede di "aggiornare il wiki col codice", "refresh dopo i commit recenti", "sincronizza il wiki con HEAD". Calcola diff git vs last codebase-snapshot, aggiorna entity toccate, scrive nuovo snapshot.
---

# Skill: refresh

Riconcilia il wiki di progetto con il codebase corrente via git diff. Output: nuovo `wiki/sources/codebase-snapshot-<YYYY-MM-DD>.md` + entity aggiornate + log entry.

## Pre-condizioni

- `.anjawiki/meta.yaml` esiste (wiki inizializzato)
- cwd è git repo (`git rev-parse --git-dir` no error)
- Esiste almeno uno `wiki/sources/codebase-snapshot-*.md` (baseline da `/anja-init --analyze`)

## Step-by-step

### 1. Trova base SHA

```bash
ls -1 .anjawiki/wiki/sources/codebase-snapshot-*.md 2>/dev/null | sort | tail -1
```

Estrai `git_sha:` dal frontmatter dell'ultimo snapshot. Se argomento `--since SHA` esplicito, override.

Se zero snapshot trovati → output errore "No baseline. Run /anja-init --analyze first." e STOP.

### 2. Diff via Bash

```bash
BASE_SHA="<da step 1>"
HEAD_SHA=$(git rev-parse HEAD)

# Stat sommaria
git diff "$BASE_SHA".."$HEAD_SHA" --stat

# Name-status (M, A, D, R)
git diff "$BASE_SHA".."$HEAD_SHA" --name-status

# Commit messages
git log "$BASE_SHA".."$HEAD_SHA" --oneline | head -50
```

Se `BASE_SHA == HEAD_SHA`: niente da fare. Output "Wiki già sincronizzato con HEAD <sha>." e STOP.

### 3. Filtra file rilevanti

Pattern da SKIPPARE (noise):
- Lockfile: `*.lock`, `package-lock.json`, `pnpm-lock.yaml`, `Cargo.lock`, `go.sum`, `Gemfile.lock`
- Build artifacts: `dist/`, `build/`, `out/`, `target/`, `.next/`, `__pycache__/`, `node_modules/`
- Generated: `*.min.js`, `*.bundle.js`, `__snapshots__/`, `*.generated.*`
- Test fixtures: `fixtures/`, `__fixtures__/`
- Binari: `*.png`, `*.jpg`, `*.gif`, `*.pdf`, `*.zip`

Pattern da KEEP (semantica):
- Sorgenti: `*.py`, `*.ts`, `*.tsx`, `*.js`, `*.jsx`, `*.go`, `*.rs`, `*.java`, `*.kt`, `*.rb`, `*.php`, `*.c`, `*.cpp`, `*.swift`
- Config: `*.yaml`, `*.yml`, `*.json` (eccetto lock), `*.toml`, `*.ini`, `*.env.example`
- Schema: `*.sql`, `*.proto`, `*.graphql`, `*.fbs`
- Infra: `Dockerfile`, `docker-compose.yml`, `.github/`, `k8s/`, `terraform/`
- Doc: `*.md` (non in `node_modules/`)

### 4. Checkpoint se troppi file

Se file_relevant_count > 50:

```
Trovati N file modificati rilevanti. Vuoi:
- (a) Analizzare tutti (deep, ~10-15 min)
- (b) Solo i top 20 per LOC delta
- (c) Solo file in cartelle specifiche (chiedi quali)
- (d) Solo file architetturali (no fix di routine)
```

Via AskUserQuestion.

### 5. Per ogni file rilevante

a. Trova entity/concept wiki che lo riferiscono:
   ```bash
   grep -l "<file_path>" .anjawiki/wiki/entities/ .anjawiki/wiki/concepts/ 2>/dev/null
   ```

b. **Entity/concept esiste**:
   - Read la pagina
   - Calcola sintesi cambiamenti (1-2 righe, leggi il diff per i punti chiave)
   - Update via `wiki.upsert_entity` (o `wiki.upsert_concept`) sezione "Apparizioni" con riga tipo:
     `- [[codebase-snapshot-<DATE>]] — <breve descrizione cambiamento>`
   - O extend sezione "Dettagli" se cambiamento sostanziale

c. **Entity/concept NON esiste E cambiamento significativo** (>50 LOC delta O nuovo file architetturale chiave):
   - AskUserQuestion: "File X cambiato significativamente. Creare entity dedicata? (label, skip, label custom)"
   - Se sì → `wiki.upsert_entity` con slug derivato dal path + frontmatter base

d. **Cambiamento non significativo**: skip, ma countalo nei file_seen.

### 6. Scrivi nuovo snapshot

```python
# Tramite wiki.upsert_source con campi snapshot
slug = f"codebase-snapshot-{TODAY}"
sections = {
    "Sintesi del periodo": "<N commits, M file changed dal <base-sha-short>>",
    "Aree principali analizzate": "- [[entity-1]] — <change>\n- [[entity-2]] — <change>\n...",
    "Cambiamenti rispetto allo snapshot precedente": "<commits list + file delta>",
    "Note": "<eventuale contesto, decisioni emerse>",
}
wiki.upsert_source(
    slug=slug,
    title=f"Codebase snapshot {TODAY}",
    sections=sections,
    subtype="codebase-snapshot",
    git_sha=HEAD_SHA,
    analyzed_at=ISO_NOW,
    tags=["snapshot"],
)
```

### 7. Update overview.md (se serve)

Se la tesi è cambiata (nuove direzioni architetturali, deprecation aree, refactor strutturali):
- `wiki.update_overview` con sezione "Direzione corrente" aggiornata

Altrimenti SKIP. Evita rumore in overview.

### 8. Update index.md

```
wiki.index_update(
    category="Sources",
    entries=[f"- [[codebase-snapshot-{TODAY}]] — snapshot al commit {HEAD_SHA[:8]}"],
)
```

### 9. Log entry

```
wiki.log_append(type="refresh", description=f"N pagine aggiornate, snapshot {HEAD_SHA[:8]}")
```

## Modalità dry-run

Con `--dry-run`: esegui step 1-3, restituisci report:

```
[DRY-RUN] Refresh anteprima
  Base:           <date>-<sha-short>
  Head:           <sha-short>
  Commits:        <N>
  File totali:    <M>
  File rilevanti: <K>
  Entity probabili: <X stimate>
  
File rilevanti:
  M src/auth.py (+45 -12) → entity [[auth-service]]
  A src/oauth.py          → ??? (nessuna entity esistente)
  ...
```

Non scrivere nulla. Suggerire all'utente cosa decidere prima di rilanciare senza dry-run.

## Anti-pattern

- **Non aggiornare ogni file** se >100 cambiati senza checkpoint. Il context si gonfia.
- **Non dedurre nuova entity** da rename o move (R status di git). Usa il diff content per decidere.
- **Non sovrascrivere overview.md** per ogni snapshot. Solo cambi di tesi.
- **Non cancellare snapshot precedenti**: sono memoria storica del wiki.
