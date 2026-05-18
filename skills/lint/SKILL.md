---
name: lint
description: Workflow di health check del wiki anja del progetto corrente. Da usare quando l'utente esegue /anja-lint o chiede di "controllare il wiki", "verificare integrità", "trovare problemi nel wiki". Combina check meccanici (script Python) e check semantici (Claude legge le pagine).
---

# Skill: lint

Health check del wiki anja. Output: report transient in `.anjawiki/wiki/analysis/lint-<YYYY-MM-DD>.md`.

## Pre-condizioni

- `.anjawiki/meta.yaml` esiste
- `.anjawiki/wiki/` esiste con la struttura attesa

## Step-by-step

### 1. Check meccanici (Python helper)

Esegui:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lint_checks.py" \
  --wiki-root .anjawiki/wiki \
  --stale-days 90
```

Output JSON con campi:
- `pages_total`, `issues_total`
- `by_severity` (counts)
- `issues[]` con per ogni issue: `severity` (error/warning/suggestion), `type`, `page`, `message`

**Tipi rilevati dal Python**:
| Tipo | Severity | Cosa significa |
|---|---|---|
| `broken-link` | error | `[[X]]` referenced ma X non esiste |
| `missing-frontmatter` | error | pagina senza blocco YAML |
| `incomplete-frontmatter` | warning | mancano `title:` e/o `type:` |
| `orphan` | warning | pagina senza inbound link |
| `stale` | suggestion | `updated:` > N giorni fa |

Parsa il JSON e tienilo in memoria per il report finale.

### 2. Check semantici (Claude legge)

Quattro check che richiedono giudizio LLM:

#### 2.1 Concetti ripetuti senza pagina propria

Leggi `.anjawiki/wiki/index.md` e identifica concetti citati in 3+ pagine ma senza una `concepts/<slug>.md` dedicata. Per ogni concetto trovato:

```bash
grep -rli "<concetto>" .anjawiki/wiki/entities/ .anjawiki/wiki/concepts/ 2>/dev/null
```

Se appare in 3+ pagine entity/concept ma non c'è una concept page con quel nome → **suggestion**: "Concetto 'X' citato in N pagine, meriterebbe una pagina propria (`concepts/<slug>.md`)".

#### 2.2 Contraddizioni potenziali

Heuristica leggera:
- Cerca pagine (Grep) che condividono il tag o si linkano vicendevolmente
- Per ogni cluster, leggi le sintesi
- Se trovi affermazioni in tensione (es. due source che dicono cose diverse senza essere segnalate come tali) → **warning**: "Possibile contraddizione tra [[X]] e [[Y]] su tema Z".

Niente analisi profonda — è una heuristica. Falsi positivi sono OK; meglio un check rumoroso che uno silenzioso.

#### 2.3 Index disallineato

Conta le pagine nel filesystem (`Glob`) per categoria (entities/, concepts/, sources/, analysis/) e confronta con il numero elencato in `wiki/index.md`. Se discrepanza significativa (>5 pagine non elencate) → **warning**: "Index ha N pagine elencate ma il filesystem ne ha M (categoria X)".

#### 2.4 Overview disallineato

Leggi `wiki/overview.md`. Confronta con le ultime 3-5 entry significative del log:
- Se l'overview non menziona temi che dominano le ingest recenti → **suggestion**: "Overview potrebbe non riflettere la tesi corrente: tema X dominante in N source recenti ma non menzionato".

### 3. Compila report

Crea `.anjawiki/wiki/analysis/lint-<YYYY-MM-DD>.md`:

```markdown
---
title: Lint report YYYY-MM-DD
type: analysis
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: []
tags: [lint]
transient: true
---

# Lint report del YYYY-MM-DD

## Summary

- Pagine totali: <N>
- Issue totali:  <N>
  - Errors:      <E>
  - Warnings:    <W>
  - Suggestions: <S>

## Errors (<E>)

### broken-link in `[[<page>]]`

> [[X]] referenziato ma non esiste.

**Fix suggerito**: rinominare/creare la pagina, o rimuovere il link.

(...una sezione per ogni error...)

## Warnings (<W>)

### orphan: `[[<page>]]`

> Nessun inbound link.

**Fix suggerito**: aggiungere riferimento da pagine correlate, oppure marcare per merge/cancellazione se obsoleta.

(...una sezione per ogni warning...)

## Suggestions (<S>)

### stale: `[[<page>]]`

> Non aggiornata da <N> giorni.

**Fix suggerito**: `/anja-refresh` (per dev/automation) o re-ingest fonte rilevante.

(...una sezione per ogni suggestion...)

## Note

Report transient — può essere cancellato dopo aver applicato i fix. Il prossimo `/anja-lint` ne genera uno nuovo.
```

**Severity ordering**: errors prima, poi warnings, poi suggestions. Ogni sezione ordinata internamente per gravità o per pagina.

### 4. Aggiorna `wiki/index.md`

Aggiungi entry sotto **Analysis**:

```
- [[lint-YYYY-MM-DD]] — health check (E errors, W warnings, S suggestions)
```

Se esistono già più lint report passati, considera se è il caso di **proporre la cancellazione** dei vecchi (oltre i 30 giorni) — sono transient. Chiedi via `AskUserQuestion`: "Trovo N lint report più vecchi di 30 giorni. Vuoi che li cancelli?"

### 5. Append log entry

```
## [YYYY-MM-DD] lint | E errors, W warnings, S suggestions
```

Se sono presenti errors (severity error), aggiungi una riga di nota:

```
## [YYYY-MM-DD] lint | E errors, W warnings, S suggestions
- Errori critici da risolvere subito (vedi report)
```

## Edge case

| Caso | Cosa fare |
|---|---|
| Wiki vuoto / appena inizializzato | Output: "Wiki vuoto, niente da controllare. Esegui ingest prima." |
| Solo `index.md`, `log.md`, `overview.md` (no contenuto) | Stesso di sopra |
| Python helper crash | Esegui solo i check semantici, segnala l'errore Python nel report |
| Rapporti lint ripetuti nello stesso giorno | Sovrascrivere `lint-<YYYY-MM-DD>.md` (è transient comunque) |
| `--no-file` passato | Mostra il report a video, NON scrivere file. Skip step 4. |

## Output finale all'utente

```
✓ Lint completato.
  Pagine controllate: <N>
  Issue totali:       <N>  (E errors, W warnings, S suggestions)
  Report:             wiki/analysis/lint-<YYYY-MM-DD>.md
  Log entry:          aggiunta
```

Se errors > 0, evidenziali in cima:

```
⚠️  <E> errors da risolvere subito:
  - broken-link: [[X]] in <page>
  - missing-frontmatter: <page>
```
