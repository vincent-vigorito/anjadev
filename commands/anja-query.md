---
description: Interroga il wiki anja e opzionalmente fila la risposta come analysis page
argument-hint: <domanda> [--no-file]
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, AskUserQuestion
---

# /anja-query

Interroga il wiki di progetto. Risposta sintetica con citazioni `[[wikilinks]]`. Opzionalmente fila la risposta come `wiki/analysis/<slug>.md` per accumulare conoscenza.

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica che `.anjawiki/meta.yaml` esista nella cwd:
- Se no: errore "Wiki non inizializzato. Lancia `/anja-init` prima." e termina.

Leggi `.anjawiki/CLAUDE.md` per le convenzioni del progetto (frontmatter, link, log format).

## Workflow

Esegui il workflow query definito in `${CLAUDE_PLUGIN_ROOT}/skills/query/SKILL.md`. Sintesi degli step:

1. **Leggi `.anjawiki/wiki/index.md` per primo** — è il catalogo navigabile. Regola d'oro.
2. **Identifica pagine candidate** dall'index + tag rilevanti + Grep su termini chiave della domanda.
3. **Leggi candidate in parallelo** (batch reads quando puoi).
4. **Sintetizza la risposta**:
   - Cita con `[[wikilinks]]`
   - Cita anche le source originali `[[source-X]]` se importante
   - Segnala gap o contraddizioni
   - Se non trovi materiale rilevante: dillo, suggerisci `/anja-ingest`
5. **Decidi se filare** come `analysis/<slug>.md`:
   - Default: sì, via `AskUserQuestion`, eccetto domande triviali (vedi skill)
   - Skip se passato `--no-file`
6. **Se fila**:
   - Slug via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slugify.py "<tema della query>"`
   - Scrivi `.anjawiki/wiki/analysis/<slug>.md` con template Analysis di `.anjawiki/CLAUDE.md`
   - Aggiungi entry sotto Analysis in `.anjawiki/wiki/index.md`
7. **Append log**: `## [YYYY-MM-DD] query | <domanda riassunta>`

## Output finale

Mostra all'utente la risposta sintetica + meta info:

```
<risposta sintetica con [[wikilinks]]>

---
Pagine consultate: [[page-1]], [[page-2]], ...
Fonti citate:      [[source-X]], [[source-Y]]
Filata come:       wiki/analysis/<slug>.md  (se filata)
Log entry:         aggiunta
```

Se la query è triviale (count, navigazione, conferma), restituisci solo la risposta breve senza filare.
