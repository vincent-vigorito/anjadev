---
description: Migra un progetto/hub anja esistente al layout corrente del plugin (triade, AGENTS.md composed, MCP, schema-version)
argument-hint: [--target <path>] [--type dev|research|business|personal|automation|hub] [--memory]
allowed-tools: Bash, Read, AskUserQuestion
---

# /anja-upgrade

Porta un progetto (o hub) con wiki anja di una versione precedente al layout corrente del plugin. Non-distruttivo: aggiunge solo ciò che manca, non tocca il wiki esistente. Wrapper di `${CLAUDE_PLUGIN_ROOT}/scripts/upgrade_triade.py`.

Argomenti passati dall'utente: `$ARGUMENTS`

## Workflow

### Step 1: Parse argomenti

- **`--target <path>`** — root del progetto/hub da migrare. Default: cwd (`.`).
- **`--type <type>`** — override della detection automatica (project legge `type:` da `.anjawiki/meta.yaml`, hub viene riconosciuto da `config/projects.json`).
- **`--memory`** — al termine, migra anche la Claude Code memory (`~/.claude/projects/<encoded>/memory/`) dentro `SOUL.md` via `migrate_cc_memory.py`.

### Step 2: Diagnosi stato corrente

Esegui via Bash (singolo comando, sostituisci `<TARGET>`):

```bash
T="<TARGET>"; \
test -f "$T/.anjawiki/meta.yaml" && echo "kind=project" || (test -f "$T/config/projects.json" && echo "kind=hub" || echo "kind=unknown"); \
test -f "$T/.anjawiki/.schema-version" && echo "schema=$(cat "$T/.anjawiki/.schema-version")" || echo "schema=MISSING"; \
for f in AGENTS.src.md AGENTS.md SOUL.md TOOLS.md CLAUDE.md .mcp.json; do test -e "$T/$f" && echo "$f=ok" || echo "$f=MISSING"; done
```

- Se `kind=unknown`: termina con "Nessun wiki anja trovato in `<TARGET>` (manca `.anjawiki/meta.yaml`). Per un progetto nuovo usa `/anja-init`."
- Se **niente è MISSING** e `schema` è valorizzato: termina con "Progetto già al layout corrente (schema `<ver>`), niente da migrare."
- Altrimenti mostra all'utente una sintesi compatta di cosa manca e prosegui.

> Nota hub: l'upgrade di un hub richiede gli script di `anja-hub/` accanto al plugin (dev env AnjaHub). Se `kind=hub` e lo script fallisce con import error, spiega che l'upgrade hub va lanciato dal dev env, non dal plugin installato via marketplace.

### Step 3: Dry-run

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/upgrade_triade.py" --target "<TARGET>" --dry-run ${TYPE_FLAG}
```

dove `${TYPE_FLAG}` è `--type <type>` se l'utente l'ha passato, altrimenti vuoto. Mostra l'output all'utente.

### Step 4: Conferma

Usa **AskUserQuestion**:

- **Procedi** — applica la migrazione (file mancanti aggiunti, esistenti preservati)
- **Annulla** — non fare nulla

Se "Annulla": termina con "Upgrade annullato, nessun file toccato."

### Step 5: Esegui

Stesso comando dello Step 3 **senza** `--dry-run`. Mostra l'output. Se exit code ≠ 0, riporta l'errore e fermati.

### Step 6: Migrazione memoria (solo se `--memory`)

Lo script chiede conferma via `input()` → in Bash non-interattivo va orchestrato in due passi:

1. Dry-run e mostra il diff all'utente:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_cc_memory.py" --target "<TARGET>" --dry-run
   ```
2. **AskUserQuestion** (Applica / Salta). Se "Applica":
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_cc_memory.py" --target "<TARGET>" --yes
   ```

Se la CC memory dir non esiste, salta con nota "Nessuna CC memory da migrare."

### Step 7: Output finale

```
✓ Upgrade completato per <TARGET>
  Triade:         <creata|già presente>
  AGENTS.md:      composed rigenerato
  .mcp.json:      anja_memory registrato (env aggiornato)
  schema-version: <ver>

⚠ Riavvia la sessione Claude Code per caricare il nuovo .mcp.json e il context composto.
```

Adatta le righe a ciò che è effettivamente successo (l'output dello script dice cosa ha scritto e cosa ha skippato).
