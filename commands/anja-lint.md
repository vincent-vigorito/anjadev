---
description: Health check del wiki anja (orfani, link rotti, frontmatter, contraddizioni)
argument-hint: [--no-file] [--stale-days N]
allowed-tools: Bash, Read, Write, Grep, Glob, AskUserQuestion
---

# /anja-lint

Esegui health check del wiki di progetto. Combina **check meccanici** (via script Python) e **check semantici** (via Claude). Output: report `wiki/analysis/lint-<YYYY-MM-DD>.md` (transient, può essere cancellato).

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica che `.anjawiki/meta.yaml` esista nella cwd:
- Se no: errore "Wiki non inizializzato. Lancia `/anja-init` prima." e termina.

## Workflow

Esegui il workflow lint definito in `${CLAUDE_PLUGIN_ROOT}/skills/lint/SKILL.md`. Sintesi:

1. **Check meccanici** via Python:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lint_checks.py" \
     --wiki-root .anjawiki/wiki \
     --stale-days <N>      # default 90
   ```
   Lo script restituisce JSON con: broken-link (error), orphan (warning), missing-frontmatter (error), incomplete-frontmatter (warning), stale (suggestion).

2. **Check semantici** via Claude (lettura mirata):
   - Concetti citati ripetutamente in molte pagine senza pagina propria → suggestion
   - Possibili contraddizioni tra pagine (heuristica) → warning
   - `wiki/index.md` non allineato con il contenuto effettivo → warning
   - `wiki/overview.md` non allineato con la tesi corrente → warning

3. **Compila report** combinato in `.anjawiki/wiki/analysis/lint-<YYYY-MM-DD>.md`:
   - Frontmatter `type: analysis`, `transient: true`
   - Severity ordering: errors > warnings > suggestions
   - Per ogni issue: tipo, pagina, descrizione, azione suggerita

4. **Aggiorna `index.md`**: entry sotto Analysis con link al report.

5. **Append log entry**: `## [YYYY-MM-DD] lint | E errors, W warnings, S suggestions`

## Output finale

```
✓ Lint completato.
  Pagine controllate: <N>
  Issue totali:       <N>  (E errors, W warnings, S suggestions)
  Report:             wiki/analysis/lint-<YYYY-MM-DD>.md
  Log entry:          aggiunta
```

Se ci sono **errori** (severity error), evidenziali in cima all'output con istruzioni di fix immediato.
