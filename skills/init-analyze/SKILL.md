---
name: init-analyze
description: Workflow per popolare il wiki anja di un progetto esistente leggendo il codice e la documentazione. Da usare quando l'utente esegue /anja-init --analyze su type dev/automation, oppure subito dopo /anja-init --cold per "fare l'analisi del progetto" successivamente. Output: overview + entity per i sottosistemi + concept per i pattern + codebase-snapshot.
---

# Skill: init-analyze

Workflow di **popolamento iniziale** del wiki anja analizzando un progetto esistente. Tipico per type `dev` e `automation`.

> **Differenza con `ingest`**: ingest aggiunge UNA fonte esterna; init-analyze legge il PROGETTO STESSO come fonte e crea la prima generazione di pagine entity/concept/snapshot.

## Pre-condizioni

- `.anjawiki/` deve esistere nella cwd (creato da `/anja-init --cold` o dalla prima parte di `/anja-init --analyze`)
- Lo schema `.anjawiki/CLAUDE.md` deve essere stato letto in context

## Step-by-step

### 1. Leggi memoria CC esistente (se presente)

Cerca:

```bash
# Encoded path di Claude Code: prefisso "-" + cwd con "/" → "-"
ls ~/.claude/projects/ | grep -F "$(echo "$(pwd)" | tr '/' '-')"
```

Se trovato, leggi `~/.claude/projects/<encoded-path>/memory/MEMORY.md` (se esiste). Sono tue note dalle sessioni passate su questo progetto: preferenze utente, decisioni di approccio, note di lavoro recente. **Punto di partenza informato**, non parti da zero.

### 2. Esplora struttura via Explore subagent

Invoca via Task tool con `subagent_type: Explore`:

```
Mappa la struttura del progetto in <cwd>. Identifica:
- Linguaggio/framework principali (da package.json, pyproject.toml, Cargo.toml, go.mod, etc.)
- Directory top-level e loro ruolo
- File di documentazione esistenti (README, CLAUDE.md, ARCHITECTURE.md, CHANGELOG.md, docs/)
- Entry point principali
- Eventuali sotto-pacchetti / sottosistemi distinti
- File di config rilevanti (.mcp.json, docker-compose.yml, ecc.)
Restituisci un summary strutturato in <500 righe.
```

L'Explore subagent ritorna in context separato → main context resta pulito anche su monorepo grandi.

### 3. Batch read di file di "punto fisso"

In parallelo (multiple Read in un single tool batch):

- **`README.md`** — overview esterno
- **`CLAUDE.md`** del progetto se esiste — spesso fonte primaria di verità (handoff)
- **`ARCHITECTURE.md`** se esiste
- **`CHANGELOG.md`** se esiste — capire l'evoluzione recente
- **Package files** — `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `requirements.txt`
- **Config rilevanti** — `.mcp.json`, `docker-compose.yml`, `Makefile`, `pyproject.toml`

> **Insight dal test reale (bybit-mcp-trading)**: quando il progetto ha un `CLAUDE.md` ricco (35KB, ben strutturato), questo è la **fonte primaria** e basta da solo per popolare l'80% del wiki iniziale. Skip lettura singoli file di codice.

Se il `CLAUDE.md` esiste ed è > 5KB: leggilo per primo. Spesso ha già la struttura mentale del progetto, sezioni datate (sessioni), TODO espliciti, fix applicati.

### 4. Git history (se progetto è git)

```bash
# Verifica
test -d .git && echo "is git" || echo "not git"

# Se git:
git log --oneline -100
git shortlog -sn -100        # autori e quanti commit
git diff --stat HEAD~30..HEAD  # aree calde (file modificati di recente)
```

Se non git → skip. Annota in codebase-snapshot che `git_sha` non è disponibile.

### 5. Discussione con l'utente (AskUserQuestion)

**Critico**: prima di scrivere, presenta cosa hai capito e chiedi conferma/restringimento.

Esempio di domanda strutturata:

```
Ho identificato N sottosistemi nel progetto:

1. <sottosistema-1> — <descrizione 1 riga>
2. <sottosistema-2> — <descrizione 1 riga>
...

Propongo di creare:
- 1 overview.md con tesi corrente
- N entity (1 per sottosistema)
- M concept (pattern architetturali, AI workflow, risk, etc.)
- 1 codebase-snapshot

Vuoi che:
(a) procedo con l'analisi completa di tutti i sottosistemi?
(b) mi concentro su uno specifico (quale?)
(c) skip alcuni di questi (quali?)
(d) aggiungere cose che non ho identificato (quali?)
```

> **Insight dal test reale**: io (l'agente) ho saltato l'AskUserQuestion nel primo test e sono andato avanti diretto. Funziona, ma su un monorepo enorme bisogna restringere. **Default: chiedi sempre**, l'utente può sempre dire "vai avanti su tutto".

### 6. Deep-dive mirato (solo sui sottosistemi confermati)

Per ciascun sottosistema confermato:

- Lista i file principali via `Glob`/`Bash ls`
- Leggi entry point + 2-3 file più rappresentativi (non tutti)
- Estrai: ruolo, dipendenze interne, pattern usati, integrazioni esterne
- Identifica concept che attraversano più entity (sono candidati per pagine concept)

> **Insight dal test reale**: NON serve leggere ogni file. Il `CLAUDE.md` del progetto + esplorazione di nomi file + lettura di 2-3 file chiave per sottosistema bastano per overview di qualità. La granularità di dettaglio si ottiene poi via `/anja-ingest` su file specifici se serve.

### 7. Scrittura wiki (può essere delegata al subagent)

Scrivi in batch parallelo (Write multipli in un single tool call):

- **`wiki/overview.md`** — sintesi + tesi corrente + tabella sottosistemi + stato implementazione + TODO ad alto valore
- **`wiki/entities/<slug>.md`** per ogni sottosistema confermato (template Entity da CLAUDE.md)
- **`wiki/concepts/<slug>.md`** per ogni pattern/concept ricorrente (template Concept)
- **`wiki/sources/codebase-snapshot-<YYYY-MM-DD>.md`** con frontmatter `subtype: codebase-snapshot`, `git_sha:` (o vuoto), `analyzed_at:`
- Aggiorna **`wiki/index.md`** con tutte le pagine create (categorizzate)

Se >5 pagine entity/concept da creare e l'analisi è approfondita, considera di delegare la scrittura batch al subagent `wiki-maintainer` (vedi `agents/wiki-maintainer.md`).

#### Granularità consigliata (dal test reale)

Per progetti **medi** (3-5 sottosistemi, ~30-50 file di codice rilevanti):
- 1 overview
- 4-5 entity (1 per sottosistema)
- 6-10 concept (pattern architetturali, AI workflow, risk, integrazioni)
- 1 codebase-snapshot

Per progetti **piccoli** (1-2 sottosistemi):
- 1 overview
- 1-2 entity
- 3-5 concept

Per progetti **grandi/monorepo** (>5 sottosistemi): chiedi all'utente di restringere lo scope.

### 8. Cross-reference

Mentre scrivi le pagine, garantisci:

- Ogni entity linka ai concept che usa
- Ogni concept ha sezione "Connessioni" che linka alle entity dove è applicato
- Source page (codebase-snapshot) elenca tutte le entity create
- Index categorizzato (no wall of links)

> **Insight dal test reale**: il lint_checks.py post-init ha confermato 0 issue → cross-reference rispettate naturalmente seguendo i template di CLAUDE.md.

### 9. Append log entry

In `.anjawiki/wiki/log.md`:

```
## [YYYY-MM-DD] init-analyze | snapshot iniziale (commit <sha>) — N entity, M concept create
```

Se git non disponibile, sostituisci `(commit <sha>)` con `(no git_sha — progetto non in git)`.

## Anti-timeout per design

Tre meccanismi che lavorano insieme:

1. **Explore subagent** in step 2 → context separato, main context resta pulito
2. **Iterativo + checkpoint utente** in step 5 → tagli scope quando troppo larga
3. **Skip lettura singoli file di codice** quando il `CLAUDE.md` esiste e copre il progetto

Tempo tipico misurato: **15-30 min** su progetto medio (~30 file rilevanti) con `CLAUDE.md` ricco.

## Edge case

| Caso | Cosa fare |
|---|---|
| Progetto senza documentazione esistente (no README, no CLAUDE.md) | Lettura più approfondita dei file di codice + entry point; overview più conservativo (less assumption) |
| Monorepo enorme (>1000 file) | Step 5 obbligatorio: chiedi scope prima di tutto. Considera di fare init-analyze per package, non per intero monorepo. |
| Progetto in lingua non Italian/English | Documenta nel wiki nella lingua del progetto se diversa |
| Codice generato (no source) | Snapshot del codice generato + segnala come "snapshot di artifact, non source" |
| `CLAUDE.md` esiste ma è obsoleto | Nota in codebase-snapshot: "doc obsoleta, sezione X non riflette stato attuale del codice" |
| Progetto è multi-package (sub-progetti distinti) | Considera 1 entity per sub-package; oppure init-analyze separato per ognuno |

## Output finale all'utente

```
✓ init-analyze completato.
  Overview:        wiki/overview.md
  Entity create:   <N> ([[entity-1]], [[entity-2]], ...)
  Concept create:  <M> ([[concept-1]], [[concept-2]], ...)
  Snapshot:        wiki/sources/codebase-snapshot-<YYYY-MM-DD>.md
  Index:           aggiornato
  Log:             entry init-analyze aggiunta

Suggerimento: lancia /anja-status per vedere il riepilogo, /anja-lint per validare integrità.
```

## Quando NON delegare al subagent wiki-maintainer

- Quando il numero di pagine è basso (<5) — overhead di delega non vale
- Quando l'utente vuole vedere ogni pagina mentre la scrivi (interactive feedback)
- Step 1-6 non delegare MAI (sono conversazionali / context-aware)

## Quando delegare al subagent wiki-maintainer

- Step 7 con >5 pagine batch da scrivere
- Pattern: main agent fa step 1-6, prepara la lista pagine + contenuti chiave, poi `Task(subagent_type=wiki-maintainer, prompt="scrivi queste N pagine seguendo i template, ecco i contenuti chiave: ...")`. Subagent scrive in batch, ritorna summary.

## Connessioni

- Triggered da `/anja-init --analyze` (commands/anja-init.md, step 5)
- Usa template di pagina in `.anjawiki/CLAUDE.md` (Source, Entity, Concept, Codebase-snapshot)
- Può delegare a `agents/wiki-maintainer.md` per batch writes
- Output validabile con `/anja-lint` e `/anja-status`
- Complementare a `/anja-refresh` (che riconcilia incrementalmente vs HEAD dopo init)
