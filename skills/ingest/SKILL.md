---
name: ingest
description: Workflow per ingerire una fonte nel wiki anja del progetto corrente. Da usare quando l'utente esegue /anja-ingest o chiede di "ingerire", "aggiungere al wiki", "filare" un articolo, paper, URL, o documento esterno.
---

# Skill: ingest

Workflow di ingestione di una fonte nel wiki anja. Lavora sempre nella cwd corrente (`.anjawiki/` deve esistere).

## Pre-condizioni

- `.anjawiki/meta.yaml` deve esistere → verifica con `Read` o `Bash test -f`
- Lo schema `.anjawiki/CLAUDE.md` deve essere stato letto/aggiornato in context (è il manuale del wiki di questo progetto)

## Step-by-step

### 1. Identifica la fonte

Argomento atteso: path locale o URL.

| Caso | Azione |
|---|---|
| Path locale `.md`, `.txt`, `.rst` | `Read` direttamente |
| Path locale `.pdf`, immagine | Tentativo di estrazione testo; se fallisce → `AskUserQuestion` |
| URL | `WebFetch` per ottenere il contenuto; salva una copia in `.anjawiki/raw/<topic>/<slug>.<ext>` |

Per gli URL: se il `topic` non è ovvio dal contenuto o dal contesto, **chiedi all'utente** con `AskUserQuestion`. Esempi di topic: `llm-research`, `ai-articles`, `architecture-patterns`, `bozze-vincent`. Convenzione: cartella tematica al 1° livello, libertà piena dentro.

### 2. Estrai TL;DR e punti chiave

Leggi tutta la fonte. Identifica:

- **TL;DR** (3-5 righe che riassumono il contenuto)
- **Entità menzionate** (persone, prodotti, servizi, sistemi esterni, file di codice per progetti dev)
- **Concetti chiave** (pattern, idee, conclusioni, frame di pensiero)
- **Riferimenti incrociati** (altre fonti citate, utili per future ingest)

### 3. Discuti con l'utente (default: sì)

A meno che `--no-discuss` non sia stato passato, mostra TL;DR + proposed updates con `AskUserQuestion`:

```
TL;DR:
- ...
- ...
- ...

Pagine che propongo di toccare:
- [[entity-x]] (esistente, aggiungo sezione "Apparizioni")
- [[entity-y]] (nuova)
- [[concept-z]] (esistente, estendo definizione)

Procedo? Cosa enfatizzare? Cosa ignorare?
```

Domande utili da porre quando il caso lo richiede:
- "Vuoi che includa anche le quote rilevanti, o solo TL;DR + punti chiave?"
- "Aggiorno anche `overview.md` (la fonte sembra cambiare la tesi su X)?"
- "Topic per il salvataggio in raw/ — suggerito `<X>`, va bene o preferisci altro?"

### 4. Genera slug

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/slugify.py" "<titolo della fonte>"
```

**Convenzione slug per source page**: prefisso data + titolo, es. `2026-05-04-karpathy-llm-wiki`.

### 5. Scrivi la source page

Crea `.anjawiki/wiki/sources/<YYYY-MM-DD>-<slug>.md` seguendo il template Source di `.anjawiki/CLAUDE.md`:

```markdown
---
title: <titolo della fonte>
type: source
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: []
tags: [topic1, topic2]
source_path: ../../raw/<topic>/<file>
---

# <titolo>

> 3-5 righe TL;DR.

## Punti chiave

- ...

## Quote rilevanti

> "..." — autore (se applicabile)

## Pagine wiki coinvolte

- [[entity-x]] — note specifiche
- [[concept-y]] — creata
```

### 6. Aggiorna entity/concept pages

Per ogni entità/concetto identificato:

```bash
grep -rl "<termine>" .anjawiki/wiki/entities/ .anjawiki/wiki/concepts/ 2>/dev/null
```

**Pagina esistente**:
- `Read`
- Aggiungi sezione "Apparizioni" o estendi sezioni esistenti
- Aggiorna `sources:` nel frontmatter (aggiungi `<source-id>`)
- Aggiorna `updated:` alla data odierna
- **Contraddizioni**: NON sovrascrivere silenziosamente. Aggiungi nota:
  > *Secondo [[source-X]] è X, ma [[source-Y]] dice Y. Discrepanza da risolvere.*

**Pagina non esistente**:
- Genera slug del titolo
- Crea con il template Entity o Concept di `.anjawiki/CLAUDE.md`
- `sources: [<source-id>]` nel frontmatter

**Cross-reference bidirezionali sempre**: la source page elenca le entity/concept toccate; le entity/concept linkano alla source.

### 7. Aggiorna `wiki/index.md`

- Entry sotto **Sources**: `- [[<source-id>]] — one-liner del TL;DR`
- Aggiungi nuove **Entities** create
- Aggiungi nuovi **Concepts** creati

Mantieni l'index leggibile: categorizza, raggruppa, evita wall-of-links. Se inizia a essere lungo, considera sotto-sezioni per tag.

### 8. Aggiorna `wiki/overview.md` (SE serve)

**Solo se** la fonte cambia significativamente la tesi corrente. Esempi:
- Nuova architettura proposta diversa da quella attuale
- Cambio di direzione strategica
- Risultato che invalida un'assunzione
- Conferma forte di una tesi precedentemente ipotetica

Per micro-aggiornamenti (un dettaglio in più, una conferma incrementale): **skip**.

### 9. Append log entry

In `.anjawiki/wiki/log.md`, append:

```
## [YYYY-MM-DD] ingest | <titolo della fonte>
```

Se l'ingest ha implicato decisioni notevoli (contraddizioni segnalate, scope tagliato), aggiungi una riga di nota sotto:

```
## [YYYY-MM-DD] ingest | <titolo>
- Segnalata contraddizione su [[entity-x]] tra [[source-X]] e [[source-Y]]
- Skip update di overview.md (tesi non cambia)
```

## Quando delegare al subagent `wiki-maintainer`

Se l'ingest tocca **>5 pagine** in modo non-banale (creazioni multiple, sezioni riscritte), delega via Task tool con `subagent_type: wiki-maintainer`.

L'agente ha tool ristretti (Read, Write, Edit, Grep, Glob), niente WebFetch o Bash → contesto dedicato e leggero. Il main agent rimane libero per orchestrazione e dialogo con l'utente.

Pattern di delega:
1. Main agent fa step 1-5 (identifica, estrae, discute, scrive source page)
2. Main agent invoca Task → wiki-maintainer fa step 6 (aggiornamento entity/concept)
3. Main agent fa step 7-9 (index, overview, log) sulla base del summary del subagent

## Edge case

| Caso | Cosa fare |
|---|---|
| Fonte molto lunga (>30k token) | Spezza in chunk semantici; ingerisci come fonte unica con sezione "Quote rilevanti" che sintetizza i chunk |
| URL morto o inaccessibile | Errore esplicito + suggerisci salvataggio manuale in `raw/` |
| Fonte già ingerita (stesso titolo + path) | `AskUserQuestion`: "ri-ingerire (sovrascrivi source page)" o "skip" |
| Fonte in lingua diversa | Ingerisci nella lingua originale; il wiki è multi-lingua se serve |
| Binario non parseable | `AskUserQuestion`: "salvo solo in raw/ + creo source page placeholder che descrive la fonte?" |
| Fonte enorme con 50+ entity citate | Ferma e chiedi all'utente: "è troppo. Vuoi che mi concentri su ambito X?" |

## Output finale

```
✓ Ingerito: <titolo>
  Source:     wiki/sources/<slug>.md (<n> righe)
  Aggiornate: [[page-1]], [[page-2]], ...
  Create:     [[new-page-1]], [[new-page-2]], ...
  Log entry aggiunta.
```

Se ci sono state contraddizioni segnalate o decisioni notevoli, riportale anche qui sotto l'output.
