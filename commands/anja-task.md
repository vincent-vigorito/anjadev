---
description: Gestione task del wiki anja (roadmap.md). Sub-command: add / list / done / triage
argument-hint: <add "<title>" [--priority P0|P1|P2|P3] [--est <effort>] [--owner <name>] | list [--status open|in_progress|done|blocked] [--priority P0..P3] [--owner <name>] | done <id> [--took <effort>] | triage>
allowed-tools: mcp__anja_memory__roadmap_list, mcp__anja_memory__roadmap_add, mcp__anja_memory__roadmap_update, mcp__anja_memory__roadmap_complete, mcp__anja_memory__roadmap_block, mcp__anja_memory__wiki_log_append
---

# /anja-task — Gestione task strategici cross-sessione

Pattern: `roadmap.md` come 4° file speciale del wiki, accanto a `index.md`/`log.md`/`overview.md`. Mantiene continuity tra sessioni — chiunque (utente o agent) legge lo stesso registro task.

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica che `.anjawiki/meta.yaml` esista nella cwd:
- Se no: errore "Wiki non inizializzato. Lancia `/anja-init` prima." e termina.

## Sub-command routing

Parsa il primo token di `$ARGUMENTS`:

### `add "<title>"` [--priority P0|P1|P2|P3] [--est <effort>] [--owner <name>]

Aggiungi nuovo task in stato `open`.

1. Chiama `mcp__anja_memory__roadmap_add` con args:
   - `title`: stringa quotata
   - `priority` (opt): default nessuno se utente non specifica
   - `est` (opt): es. "15min", "2h"
   - `owner` (opt): es. "anja", "vincent"
2. Conferma con: `✓ Task added: [<priority>] <title> (id: <slug>)`
3. **Niente log entry** — l'add è già visibile in `roadmap.md`

### `list` [--status <status>] [--priority <prio>] [--owner <name>]

Lista task. Default mostra tutti, ordine: open prima, P0 prima.

1. Chiama `mcp__anja_memory__roadmap_list` con filters opzionali
2. Formatta output:

```
roadmap — <count> task(s), summary: open=N in_progress=M done=K blocked=L

OPEN
  [P0] <id> — <title> (est: ..., owner: ...)
  [P1] <id> — <title>
  ...

IN PROGRESS
  [P0] <id> — <title> (started: <date>)
  ...

BLOCKED
  [P2] <id> — <title> (blocker: <reason>)

DONE (last 30 days)
  <id> — <title> (done: <date>, took: ...)
```

Mostra max 10 done. Se più: `... e N altri done, lancia /anja-task archive per pulire.`

### `done <id>` [--took <effort>]

Completion shortcut.

1. Chiama `mcp__anja_memory__roadmap_complete` con `id` + `took` opzionale
2. Conferma: `✓ Done: <id> (took: <took>)`
3. **Log entry**: `mcp__anja_memory__wiki_log_append(type="roadmap", description="completed <id>: <title>")`

### `triage`

Review periodica. Workflow semantico (Claude analizza):

1. Chiama `mcp__anja_memory__roadmap_list` (no filter)
2. **Analizza** restituendo:
   - **Stale**: task `open` con `added` > 14 giorni senza `started`
   - **Missing estimates**: task P0/P1 senza `est`
   - **Long in-progress**: task `in_progress` con `started` > 7 giorni
   - **Blocked aging**: task `blocked` da > 7 giorni
   - **Suggerimenti**: priorità da rivedere, owner mancanti su P0
3. Per ogni issue: propone azione concreta (es. "task X aperto da 21 giorni, considera cancel o priority bump").
4. **NON** modifica nulla automaticamente — chiede conferma per fix singolo.
5. **Log entry**: `mcp__anja_memory__wiki_log_append(type="lint", description="task triage: N stale, M missing est, K long-in-progress")`

## Output style

- **Niente preamboli verbosi**, vai diretto al risultato
- **Cita gli id** come `<id>` (no markdown, plain), così `done <id>` è copy-paste friendly
- **Una riga summary** sopra le sezioni per dare il colpo d'occhio
- **Niente emoji** se non per status (📋 al massimo)

## Edge cases

- `$ARGUMENTS` vuoto → equivale a `list` (default safe)
- Sub-command non riconosciuto → mostra l'help inline (`add | list | done | triage`)
- `roadmap.md` mancante → la prima `add` lo crea via tool; `list` mostra "_(nessun task)_"
- `done` con id che non esiste → errore con suggestion: "Esegui `/anja-task list` per vedere gli id disponibili"
