---
name: query
description: Workflow per interrogare il wiki anja e sintetizzare risposte con citazioni. Da usare quando l'utente esegue /anja-query o pone una domanda sostanziale sul progetto che richiede sintesi tra più pagine ("cosa abbiamo deciso su X?", "quali sono i tradeoff di Y?", "come si confronta A con B?").
---

# Skill: query

Workflow di interrogazione del wiki anja del progetto corrente. Sola lettura + opzionale scrittura di una analysis page.

## Pre-condizioni

- `.anjawiki/meta.yaml` deve esistere → verifica con `Read` o `Bash test -f`
- Lo schema `.anjawiki/CLAUDE.md` deve essere stato letto in context

## Step-by-step

### 1. Leggi `wiki/index.md` per primo

**Regola d'oro**: il primo `Read` è sempre `.anjawiki/wiki/index.md`. È il catalogo navigabile e ti dà la mappa di cosa esiste prima di andare a leggere pagine specifiche.

### 2. Identifica pagine candidate

Tre fonti di candidati:

1. **Dall'index**: cerca link a pagine i cui titoli/one-liner combaciano con la domanda
2. **Dai tag**: se conosci tag rilevanti (es. domanda su "auth" → cerca pagine con tag `auth`, `security`, `authentication`)
3. **Grep su termini chiave**:
   ```bash
   grep -rli "<termine>" .anjawiki/wiki/ 2>/dev/null
   ```
   Per termini multipli: prendi l'intersezione (file che matchano tutti) o l'unione (file che matchano almeno uno) a seconda di quanto è specifica la domanda.

Filtra falsi positivi: scarta `wiki/log.md` (contesto storico, non contenuto), e generalmente pagine `transient: true` (vecchi lint report).

### 3. Leggi candidate in parallelo

Usa **batch reads** dove puoi (multiple `Read` in una sola tool batch). Niente sequenziale se non c'è dipendenza tra le letture.

Limite di prudenza: se identifichi più di **15 pagine candidate**, ferma e chiedi all'utente di restringere lo scope con `AskUserQuestion`. Esempio:

> "Ho identificato 22 pagine potenzialmente rilevanti. Vuoi che mi concentri su:
> - Solo entity (16 pagine)
> - Solo concept (4 pagine)
> - Solo source recenti (ultimi 30 giorni)
> - Tutte"

### 4. Sintetizza la risposta

Una risposta anja ben fatta ha 4 caratteristiche:

- **Citata**: ogni claim importante ha `[[wikilink]]` alla pagina di origine
- **Risale alle source**: se un'entity/concept page traccia la sua info a una source, cita anche `[[source-X]]`
- **Onesta sui gap**: se l'evidenza è scarsa o assente, dillo esplicitamente
- **Onesta sulle contraddizioni**: se pagine in disaccordo, mostra entrambe le posizioni

Anti-pattern:
- Inventare claim non supportati dalle pagine ("hallucination")
- Citare pagine che non hai effettivamente letto
- Mascherare gap con generiche ("in genere si dice che...")

Se la domanda è larga e il wiki ha poco materiale: rispondi con quello che hai + suggerisci `/anja-ingest <fonte>` per arricchire.

### 5. Decidi se filare la risposta come pagina

Default: sì (via `AskUserQuestion`). **Skip se passato `--no-file`**.

**NON filare** se la query è:
- **Triviale** — "quante source ci sono?" (rispondi e stop)
- **Di navigazione** — "dove sta la pagina su X?" (rispondi e stop)
- **Di conferma** — "ho capito bene che X è Y?" (rispondi e stop)
- **Meta sul wiki** — "qual è l'ultima entry del log?" (rispondi e stop)

**Fila se la query è**:
- **Sintetica/comparativa** — "tradeoff tra X e Y", "cosa hanno in comune A, B, C"
- **Concettuale** — "perché abbiamo scelto X?"
- **Esplorativa** — "cosa sappiamo di Y nel progetto?" con risposta non banale
- Una risposta che vorresti rileggere tra 6 mesi

### 6. Se fila: scrivi la analysis page

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/slugify.py" "<tema della query>"
```

Crea `.anjawiki/wiki/analysis/<slug>.md` seguendo il template Analysis di `.anjawiki/CLAUDE.md`:

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

Aggiorna `.anjawiki/wiki/index.md` aggiungendo entry sotto **Analysis**:
```
- [[<slug>]] — <one-liner del tema>
```

### 7. Append log entry

In `.anjawiki/wiki/log.md`:

```
## [YYYY-MM-DD] query | <domanda riassunta in poche parole>
```

Se hai filato, segnala anche il path:

```
## [YYYY-MM-DD] query | <domanda> → analysis/<slug>.md
```

## Edge case

| Caso | Cosa fare |
|---|---|
| Wiki vuoto (`index.md` ha solo overview placeholder) | Risposta: "Wiki vuoto, niente fonti ingerite. Lancia `/anja-ingest` per popolare." |
| Nessuna pagina rilevante trovata | Risposta onesta: "Non trovo pagine sul tema. Possibili azioni: (a) raffinare la domanda; (b) ingerire una fonte rilevante." |
| Tutte le candidate dicono cose diverse | Sintetizza segnalando le posizioni con `[[link]]`, NON sceglierne una arbitraria. Suggerisci all'utente di risolvere la contraddizione. |
| Query in lingua diversa dal wiki | Rispondi nella lingua della query; il contenuto del wiki resta come scritto. |
| Query che richiede dati attuali (live, non in wiki) | Rispondi con ciò che è in wiki + dillo: "questi dati potrebbero essere stale; per info attuali serve `/anja-refresh` o ingest aggiornato". |

## Quando delegare al subagent

In v1 query NON delega a subagent: il workflow è leggero (sola lettura + 1 scrittura opzionale). Se in futuro emergono query molto larghe (>20 pagine candidate, sintesi complessa), introduciamo un agente `wiki-explorer` dedicato. Per ora: chiedi all'utente di restringere scope.

## Output finale

```
<risposta sintetica con [[wikilinks]]>

---
Pagine consultate: [[page-1]], [[page-2]], ...
Fonti citate:      [[source-X]], [[source-Y]]
Filata come:       wiki/analysis/<slug>.md  (se filata)
Log entry:         aggiunta
```

Per query triviali, ometti la sezione meta e restituisci solo la risposta breve.
