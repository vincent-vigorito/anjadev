---
description: Ingerisci una fonte (file o URL) nel wiki anja
argument-hint: <path|url> [--no-discuss]
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, AskUserQuestion, Task
---

# /anja-ingest

Ingerisci una nuova fonte nel wiki di progetto. Aggiorna entity/concept toccati, append log.

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica che `.anjawiki/meta.yaml` esista nella cwd:
- Se no: errore "Wiki non inizializzato. Lancia `/anja-init` prima." e termina.

Leggi `.anjawiki/CLAUDE.md` per ricordarti lo schema/convenzioni del progetto (frontmatter, log format, template di pagina, anti-pattern).

## Workflow

Esegui il workflow ingest definito nella skill `ingest` di questo plugin: `${CLAUDE_PLUGIN_ROOT}/skills/ingest/SKILL.md`. Sintesi degli step:

1. **Identifica fonte**
   - Path locale → `Read` del file
   - URL → `WebFetch` + salva copia in `.anjawiki/raw/<topic>/<slug>.<ext>`. Se topic non ovvio: `AskUserQuestion`.
   - Binario non leggibile → prova estrazione, altrimenti `AskUserQuestion`.

2. **Estrai** TL;DR (3-5 righe) + entità/concetti menzionati.

3. **Discuti con l'utente** (default: sì, salta solo se `--no-discuss` passato):
   - Mostra TL;DR + proposed updates ("aggiornerò [[X]] e [[Y]], creerò [[Z]]")
   - Conferma cosa enfatizzare/ignorare via `AskUserQuestion`.

4. **Genera slug**:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/slugify.py" "<titolo>"
   ```
   Source page: `.anjawiki/wiki/sources/<YYYY-MM-DD>-<slug>.md`

5. **Scrivi source page** seguendo il template Source di `.anjawiki/CLAUDE.md`.

6. **Aggiorna entity/concept**:
   - `Grep -rl <termine> .anjawiki/wiki/entities/ .anjawiki/wiki/concepts/` per trovare esistenti
   - Esistente → leggi, estendi (no sovrascrittura silenziosa di contraddizioni)
   - Non esistente → crea con template
   - Cross-reference bidirezionali: source ↔ entity ↔ concept

7. **Aggiorna `.anjawiki/wiki/index.md`**: entry sotto Sources, + nuove Entities/Concepts.

8. **Aggiorna `.anjawiki/wiki/overview.md`** SOLO se la fonte cambia significativamente la tesi corrente. Altrimenti skip.

9. **Append log**: `## [YYYY-MM-DD] ingest | <titolo della fonte>`

## Quando delegare al subagent `wiki-maintainer`

Se identifichi **>5 pagine** entity/concept da toccare in modo non-banale, delega via Task tool con `subagent_type: wiki-maintainer`. Il subagent ha tool ridotti e contesto dedicato → protegge il main context. Vedi `${CLAUDE_PLUGIN_ROOT}/agents/wiki-maintainer.md`.

## Output finale

```
✓ Ingerito: <titolo>
  Source:     wiki/sources/<slug>.md
  Aggiornate: <lista [[pagine]]>
  Create:     <lista [[pagine]]>
  Log:        entry aggiunta in wiki/log.md
```

Se in `--no-discuss`, scrivi tutto senza chiedere conferma (utile per batch ingest).
