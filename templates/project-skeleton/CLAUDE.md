# Schema anja per questo progetto

> Questo file istruisce Claude su come trattare il wiki. È il **manuale operativo** del wiki di progetto. Letto a ogni sessione.

> _Template per type=`dev`. Le modalità per altri tipi (`personal`, `research`, `business`, `automation`) verranno introdotte in Fase 4 con piccole varianti._

## Quick reference

| Workflow | Trigger | Output principale |
|---|---|---|
| Ingest | `/anja-ingest <path\|url>` | `sources/<slug>.md` + update entity/concept + log |
| Query | `/anja-query <domanda>` | risposta + opzionale `analysis/<slug>.md` + log |
| Refresh | `/anja-refresh` | `sources/codebase-snapshot-<date>.md` + update entity toccate + log |
| Lint | `/anja-lint` | `analysis/lint-<date>.md` (transient) + log |
| Session | hook `SessionStart`/`SessionEnd` | `sessions/<date>.md` + log entry `session` |

## Identità del progetto

I dati di identità (token, nome, tipo, data, tag) sono in `meta.yaml` accanto a questo file. **`meta.yaml` è la single source of truth** per l'identità — non duplicare quei dati qui.

## Architettura del wiki

Tre livelli, separati per principio:

### 1. `raw/` — fonti immutabili

Articoli, paper, doc esterne, codice scaricato. **Mai modificate da Claude.** Source of truth.

Convenzione: cartella tematica al 1° livello, libertà piena dentro.

```
raw/
├── articolo-pinco-pallino/
│   ├── articolo.md
│   └── immagini/
├── paper-attention-is-all-you-need/
│   └── paper.pdf
└── assets/                  # immagini condivise (opzionale)
```

### 2. `wiki/` — contenuto generato

Markdown con YAML frontmatter e `[[wikilinks]]`. **Owned by Claude.** Il wiki si crea, aggiorna, mantiene.

Sotto-cartelle semantiche:
- `entities/` — entità concrete (moduli, servizi, persone, prodotti, sistemi esterni)
- `concepts/` — concetti/pattern (architettura, convenzioni, idee)
- `sources/` — un riassunto per ogni fonte ingerita (incluso `codebase-snapshot-*` per type `dev`)
- `analysis/` — query trasformate in pagine (confronti, sintesi su richiesta, lint report)
- `sessions/` — journal di sessione (auto via hook)

### 3. Schema (questo file) + identità (`meta.yaml`)

Convenzioni e workflow + identità del progetto. Il "manuale" e la "carta d'identità".

## Tre file speciali

Sono il sistema di memoria del wiki. **Sempre presenti, sempre aggiornati.**

| File | Tipo di memoria | Quando aggiornarlo |
|---|---|---|
| `wiki/index.md` | Semantica (cosa esiste) | A ogni nuova entity/concept/source/analysis page |
| `wiki/log.md` | Episodica (cosa è successo quando) | A ogni operazione (`init`, `ingest`, `query`, `refresh`, `lint`, `session`) |
| `wiki/overview.md` | Sintesi (cosa abbiamo capito) | Solo quando una fonte cambia la tesi corrente in modo significativo |

**Regola fondamentale:** quando devi rispondere a una query, **leggi `wiki/index.md` per primo**. È il catalogo. Identifica le pagine rilevanti, poi drilla.

---

## Workflow

### Ingest

**Trigger:** `/anja-ingest <path|url>` o l'utente chiede "ingerisci questo".

**Step:**

1. **Identifica la fonte**
   - Se path locale: `Read` il file.
   - Se URL: `WebFetch` per scaricare, salva in `raw/<topic>/<slug>.<ext>`. Chiedi `topic` via AskUserQuestion se non ovvio.
   - Binari (PDF, immagini) non leggibili in testo: prova estrazione, altrimenti chiedi all'utente.

2. **Estrai contenuto chiave**
   - 3-5 righe TL;DR.
   - Entità menzionate (persone, prodotti, servizi, sistemi esterni, file di codice).
   - Concetti chiave (pattern, idee, conclusioni).
   - Riferimenti incrociati a altre fonti se presenti nel testo.

3. **Discuti con l'utente** (default: sì)
   - Mostra TL;DR + proposed updates: "aggiornerò [[entity-X]] e [[concept-Y]], creerò [[entity-Z]]".
   - Chiedi conferma, cosa enfatizzare, cosa ignorare.

4. **Scrivi `sources/<slug>.md`** (vedi template `Source` sotto).

5. **Aggiorna entity/concept pages**
   - Pagina esiste → leggi, aggiungi sezione "Apparizioni in [[source-X]]" o estendi sezioni esistenti. Aggiorna `sources` nel frontmatter.
   - Pagina non esiste → crea con il template appropriato.
   - Cross-reference bidirezionali: source ↔ entity ↔ concept.

6. **Aggiorna `index.md`**
   - Entry sotto Sources con link e one-liner.
   - Aggiungi nuove entity/concept se ne hai create.

7. **Aggiorna `overview.md`** SE la fonte cambia la tesi corrente. Altrimenti **skip**.

8. **Log entry**: `## [YYYY-MM-DD] ingest | titolo della fonte`

**Regole anti-rumore:**
- **Niente duplicazione.** Se un'entità esiste già con altro nome, aggiorna; non creare doppia. Usa `Grep` nel wiki/ per controllare.
- **Niente sovrascrittura silenziosa.** Se nuove info contraddicono il wiki, **segnala la tensione**: "secondo [[source-X]] è X, ma [[source-Y]] dice Y. Discrepanza da risolvere."

### Query

**Trigger:** `/anja-query <domanda>` o l'utente fa una domanda sul progetto.

**Step:**

1. **Leggi `wiki/index.md`** — sempre, primo. È il catalogo.
2. Identifica pagine candidate dall'index e dai tag rilevanti.
3. Leggi le pagine candidate (parallel reads dove possibile).
4. Sintetizza la risposta:
   - Cita con `[[wikilinks]]`.
   - Se serve, cita anche le source originali `[[source-X]]`.
   - Segnala contraddizioni o gap se li trovi.
5. **Chiedi all'utente** (default: sì) se filare la risposta come pagina:
   - Se sì: scrivi `wiki/analysis/<slug>.md` con `type: analysis` e `sources` che elenca le pagine usate.
   - Aggiorna `index.md` sotto Analysis.
6. **Log entry**: `## [YYYY-MM-DD] query | domanda riassunta`

**Quando NON filare come pagina:**
- Domande triviali ("quante source ci sono?")
- Domande di navigazione ("dove sta X?")
- Domande di conferma ("ho capito bene che...?")

### Refresh (solo type `dev` e `automation`)

**Trigger:** `/anja-refresh` o l'utente dice "aggiorna il wiki col codice".

**Step:**

1. **Trova ultimo snapshot**: `ls wiki/sources/codebase-snapshot-*.md | sort | tail -1` via Bash.
2. **Estrai `git_sha`** dal frontmatter dello snapshot.
3. **Diff vs HEAD** via Bash:
   ```
   git diff <base-sha>..HEAD --stat
   git log <base-sha>..HEAD --oneline
   ```
4. **Filtra file modificati significativi:**
   - Ignora: lockfile (`*.lock`, `package-lock.json`, `pnpm-lock.yaml`, `Cargo.lock`), generated, build artifacts (`dist/`, `build/`, `out/`), test snapshots (`__snapshots__/`).
   - Mantieni: codice sorgente, config, doc, schema, infra-as-code.
5. **Per ogni file rilevante:**
   - `Grep` il path nel wiki/ per trovare entity/concept che lo riferiscono.
   - Entity esiste → aggiorna sintesi cambiamenti.
   - Entity non esiste e cambiamento è significativo → AskUserQuestion se crearla.
6. **Scrivi nuovo snapshot** in `wiki/sources/codebase-snapshot-<YYYY-MM-DD>.md` (vedi template).
7. **Aggiorna `overview.md`** SE la tesi è cambiata significativamente.
8. **Log entry**: `## [YYYY-MM-DD] refresh | aggiornate N pagine, snapshot <sha>`

**Note:**
- Snapshot precedenti **non si cancellano** — sono la storia del wiki nel tempo.
- Se molti file sono cambiati (>50), checkpoint con utente prima del deep-dive.

### Lint

**Trigger:** `/anja-lint` o periodicamente (suggerito ogni ~10 ingest).

**Check meccanici** (via Bash + Grep, helper Python in futuro):
1. **Orfani** — pagine in `wiki/` mai citate via `[[link]]`.
2. **Link rotti** — `[[X]]` dove X non corrisponde a nessun file.
3. **Frontmatter** mancante o malformato.
4. **Stale** — `updated` > 90 giorni per pagine attive (ancora citate da altre).

**Check semantici** (Claude legge):
5. **Concetti citati ripetutamente** ma senza pagina propria — meritano una concept page.
6. **Possibili contraddizioni** tra pagine (heuristica).
7. **Index/overview disallineati** col contenuto effettivo.

**Output:**
- `wiki/analysis/lint-<YYYY-MM-DD>.md` con `type: analysis`, `transient: true`.
- Issue ordinate per severity: `errors` > `warnings` > `suggestions`.
- Suggerimento di azioni concrete per fixare.

**Log entry**: `## [YYYY-MM-DD] lint | N issue (E errors, W warnings, S suggestions)`

I lint report sono **transient** — il vecchio si può cancellare quando ne fai uno nuovo.

### Session journal

**`SessionStart`** (hook automatico):
- Apri/crea `wiki/sessions/YYYY-MM-DD.md` (un file per giorno).
- Se è una nuova entry: header con timestamp di inizio.
- Carica nel context: ultime 5 entry di `wiki/log.md` (= "dove eravamo").

**Durante la sessione:**
- Aggiungi note se prendi decisioni notevoli ("scelto X over Y perché Z").
- Aggiungi note se trovi un problema inaspettato.
- Per cose triviali, lascia perdere — il `SessionEnd` cattura l'essenziale.

**`SessionEnd`** (hook automatico):
- Scrivi summary 3-5 righe: cosa fatto, decisioni, prossimi passi.
- Aggiungi entry a `wiki/log.md`: `## [YYYY-MM-DD] session | summary`

---

## Convenzioni

### Frontmatter YAML — obbligatorio su ogni pagina

```yaml
---
title: <titolo leggibile>
type: entity | concept | source | analysis | session | overview | index | log
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [source-id-1, source-id-2]   # solo se applicabile
tags: [tag1, tag2]
---
```

Casi speciali:
- `source` page: aggiungi `source_path: ../../raw/<topic>/<file>` per puntare al raw.
- `analysis` page: aggiungi `transient: true` se è un report cancellabile (lint, ecc.). Aggiungi `question:` se la pagina nasce da una query.
- `codebase-snapshot` (in `sources/`): aggiungi `subtype: codebase-snapshot`, `git_sha:`, `analyzed_at:`.

### Link interni — sempre `[[wikilinks]]`

```markdown
Vedi [[auth-service]] per i dettagli.
Riferimento a sezione: [[auth-service#refresh-flow]].
Link con label custom: [[event-driven-architecture|architettura a eventi]].
```

**Mai** path relativi (`./entities/foo.md`) — rompono se sposti i file.

Per linkare un file in `raw/` usa path relativo standard markdown:
```markdown
Fonte originale: [articolo](../../raw/<topic>/<file>)
```

### Slug naming

| Tipo pagina | Pattern | Esempio |
|---|---|---|
| Entity | nome significativo, kebab-case | `auth-service`, `event-bus` |
| Concept | nome significativo, kebab-case | `event-driven-architecture` |
| Source | data + slug breve | `2026-04-26-karpathy-llm-wiki` |
| Analysis | tema della query, kebab-case | `auth-comparison-frameworks` |
| Codebase snapshot | data | `codebase-snapshot-2026-04-26` |
| Session | data | `2026-04-26` |

### Log format — strict, parsabile

```
## [YYYY-MM-DD] tipo | descrizione breve in una riga
```

**Tipi validi**: `init`, `init-analyze`, `ingest`, `query`, `refresh`, `lint`, `session`.

**Pattern del tipo**: alfanumerici + hyphens (es. `init-analyze`). Niente spazi né caratteri speciali. Pattern regex parser: `(\w[\w-]*)`.

Comando utile: `grep "^## \[" wiki/log.md | tail -20`

---

## Template di pagina

### Source

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
- ...

## Quote rilevanti

> "..." — autore (se applicabile)

## Pagine wiki coinvolte

- [[entity-x]] — aggiornata: nuovi dettagli su X
- [[concept-y]] — creata
```

### Entity

```markdown
---
title: <Nome entità>
type: entity
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [source-1, source-2]
tags: [...]
---

# <Nome>

## Sintesi

Cosa è, perché esiste, ruolo nel sistema (1-3 paragrafi).

## Dettagli

(Strutturato secondo l'entità.
Servizio: API, dipendenze, deploy, owner.
Persona: ruolo, contatti, collaborazioni.
Sistema esterno: cosa fa, come ci integriamo.)

## Apparizioni

- [[source-1]] — ruolo nella fonte (es. "introduce il pattern X")
- [[source-2]] — ...

## Connessioni

- relata a [[entity-z]] — (perché sono collegate)
- usa [[concept-w]] — (modo)
```

### Concept

```markdown
---
title: <Nome concetto>
type: concept
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [source-1, ...]
tags: [...]
---

# <Nome>

## Definizione

Cosa è il concetto in 1-3 frasi.

## Perché conta in questo progetto

Connessione concreta: dove appare, perché è rilevante per noi.

## Esempi nel progetto

- [[entity-x]] — usa questo pattern per ...
- [[entity-y]] — variante: ...

## Riferimenti

- [[source-1]] — introduce il concetto / lo applica / lo critica
```

### Analysis

```markdown
---
title: <tema della query>
type: analysis
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [pagine-usate]
tags: [...]
question: "<la domanda originale>"
---

# <tema>

## Domanda

> <la domanda originale>

## Risposta

(sintesi con [[wikilinks]])

## Pagine usate

- [[entity-x]]
- [[concept-y]]

## Gap o contraddizioni emerse

- (se nulla, ometti la sezione)
```

### Codebase snapshot (in `sources/`)

```markdown
---
title: Codebase snapshot YYYY-MM-DD
type: source
subtype: codebase-snapshot
created: YYYY-MM-DD
git_sha: <full-sha>
analyzed_at: YYYY-MM-DDTHH:MM:SSZ
tags: [snapshot]
---

# Snapshot del codebase al <data>

Commit: `<sha>` — <subject del commit>

## Aree principali analizzate

- [[entity-auth-service]]
- [[entity-data-pipeline]]
- ...

## Cambiamenti rispetto allo snapshot precedente

(solo per snapshot di refresh, non per quello iniziale di init)

- ...

## Note

(eventuale contesto, decisioni emerse durante l'analisi)
```

### Session

```markdown
---
title: Session YYYY-MM-DD
type: session
created: YYYY-MM-DD
tags: [session]
---

# Sessione del YYYY-MM-DD

## Inizio: HH:MM

(Scritto da `SessionStart` hook)

Ultime 5 entry log:
- ...

## Note durante la sessione

(da Claude o utente, durante)

## Fine: HH:MM

(Scritto da `SessionEnd` hook)

Summary: cosa fatto, decisioni, prossimi passi.
```

---

## Memoria a due livelli

Il sistema usa due memorie complementari, **distinte e non sovrapposte**:

| Memoria | Posizione | Scopo |
|---|---|---|
| **CC memory** | `~/.claude/projects/<path-encoded>/memory/` | Come collaborare con TE: preferenze, feedback, ruolo |
| **Wiki** | questo `.anjawiki/wiki/` | Conoscenza di DOMINIO: cosa sappiamo del progetto |

Il wiki è la memoria del progetto su tre file: `index.md` (semantica), `log.md` (episodica), `overview.md` (sintesi). Le due memorie si compongono ma non si duplicano: la prima dice **come** lavorare, il secondo dice **cosa**.

---

## Anti-pattern (cose da NON fare)

1. **Non sovrascrivere silenziosamente.** Se nuove info contraddicono il wiki, segnala la tensione invece di nascondere il vecchio.
2. **Non duplicare pagine.** Prima di creare, verifica con `Grep` che non esista già con nome diverso.
3. **Non lasciare link rotti.** Se citi `[[X]]` e X non esiste, o crei la pagina o usa testo normale.
4. **Non skippare il log.** Ogni operazione lascia traccia.
5. **Non scrivere fuori dal wiki/** — `raw/` è immutabile, eccetto download da ingest.
6. **Non aggiornare `overview.md` per ogni piccola cosa.** È sintesi di alto livello: aggiornala solo quando la tesi cambia.
7. **Non far diventare `index.md` un wall of links.** Categorizza, raggruppa, mantieni leggibile.
8. **Non scrivere comments inutili nelle pagine wiki.** Se la sintesi è chiara, non serve aggiungere "questa pagina è stata aggiornata il...".

---

## Quando in dubbio

| Dilemma | Default |
|---|---|
| Aggiorno X o creo Y? | Aggiorna se è la stessa cosa con più info; crea se è un nuovo concetto separato. In dubbio: AskUserQuestion. |
| Quanto deep dovrei andare? | Inizia leggera, fai checkpoint, deep-dive solo dove serve. |
| L'utente è ambiguo? | AskUserQuestion. Mai indovinare su cose importanti. |
| Devo ingerire una fonte enorme? | Spezza in chunk, discuti TL;DR per ogni chunk, non scrivere tutto in un colpo. |
| Trovo info che contraddice il wiki? | Segnala la tensione esplicitamente, non sovrascrivere. |
