---
description: Riepilogo dello stato del wiki anja (identità, conteggi, ultimo log, ultime fonti)
argument-hint: [--log-tail N]
allowed-tools: Bash
---

# /anja-status

Mostra riepilogo dello stato del wiki: identità, conteggi pagine, ultime entry log, ultima fonte ingerita, ultimo codebase-snapshot, ultimo lint.

Comando di **sola lettura**, niente scrittura, nessuna domanda all'utente.

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica che `.anjawiki/meta.yaml` esista nella cwd:
- Se no: errore "Wiki non inizializzato. Lancia `/anja-init` prima." e termina.

## Workflow

Esegui:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" \
  --target .anjawiki \
  --log-tail <N>      # default 5 se non specificato
```

Lo script restituisce JSON con tutti i dati. **Formattalo per l'utente** in modo leggibile (output sotto).

## Formato output

```
anja status — <name> (<id>)
─────────────────────────────────────────
Type:    <type>
Mode:    <init_mode>
Created: <created>

Pagine:    <total>
  entities:   <n>
  concepts:   <n>
  sources:    <n>
  analysis:   <n>
  sessions:   <n>

Ultime entry log:
  [<date>] <type> | <description>
  ...

Ultima fonte:    [[<slug>]] — <title>
Ultimo snapshot: [[<slug>]] (sha: <git_sha-short>)
Ultimo lint:     [[<slug>]] (E errors, W warnings, S suggestions)
```

**Regole di formattazione:**

- Se `latest_source` è `null`: ometti la riga "Ultima fonte" (o mostra "—")
- Se `latest_snapshot` è `null`: ometti la riga "Ultimo snapshot" (o mostra "—")
- Se `latest_lint` è `null`: ometti la riga "Ultimo lint" (o mostra "—")
- `git_sha-short`: prime 8 caratteri del SHA
- Se non ci sono log entries: mostra "(nessuna entry)"

Niente log entry generato da questo comando — `/anja-status` è read-only.
