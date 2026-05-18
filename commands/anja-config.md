---
description: Configura interattivamente embedding provider + model per code.search (richiede restart CC dopo)
argument-hint: [embed | show]
allowed-tools: Read, Edit, Bash, AskUserQuestion, mcp__anja_memory__wiki_log_append
---

# /anja-config — Configurazione interattiva del plugin

Workflow guidato per settare embed provider/model. Scrive direttamente nel `.mcp.json` del progetto (env block del server `anja_memory`). API keys restano nella shell env / `.secrets.env` (mai scritte nel file).

Argomenti: `$ARGUMENTS`

Sub-command:
- `embed` (default se vuoto) — config provider + model per ricerca semantica
- `show` — stampa la config corrente senza modifiche

## Pre-flight

Verifica che `.anjawiki/meta.yaml` esista nella cwd. Se no: "Wiki non inizializzato. Lancia `/anja-init` prima." e termina.

## Sub-command: `show`

1. Leggi `.mcp.json` dalla cwd
2. Estrai `mcpServers.anja_memory.env`
3. Stampa formato leggibile:
   ```
   anja embed config:
     Provider:  <ANJA_EMBED_PROVIDER o "openrouter (default)">
     Model:     <ANJA_EMBED_MODEL o "default per provider">
     API key:   <env var richiesta> = <"set" o "MISSING in shell env">
   ```
4. Per "set / MISSING" → controlla `Bash`: `[ -n "$VOYAGE_API_KEY" ] && echo set || echo MISSING` (sostituisci con la var corretta del provider scelto)
5. Niente log entry — read-only

## Sub-command: `embed` (default)

### Step 1 — Scelta provider

`AskUserQuestion`:
- header: "Provider"
- question: "Quale provider embedding vuoi usare per code.search?"
- options:
  - **OpenRouter (Recommended)** — 1 API key per accesso multi-provider (OpenAI/Cohere/...). Default raccomandato per setup veloce.
  - **Voyage AI** — `voyage-code-3` è SOTA su codice ($0.06/1M token). Massima qualità per code search.
  - **OpenAI** — diretto, modelli ben noti (`text-embedding-3-small/large`).
  - **Local (sentence-transformers)** — offline, privacy 100%, richiede `pip install sentence-transformers` (~600MB).
  - **None** — disabilita semantic search, solo ripgrep (level 0/1).

### Step 2 — Scelta model (in base a provider)

Solo se provider != none. Suggerimenti per options:

**OpenRouter** (options):
- `openai/text-embedding-3-small` (Recommended) — dim 1536, $0.02/1M
- `openai/text-embedding-3-large` — dim 3072, $0.13/1M
- (other) — input manuale slug OpenRouter

**Voyage** (options):
- `voyage-code-3` (Recommended) — dim 1024, ottimizzato codice, $0.06/1M
- `voyage-3` — dim 1024, general
- `voyage-3-lite` — dim 512, economico $0.02/1M

**OpenAI** (options):
- `text-embedding-3-small` (Recommended) — dim 1536, $0.02/1M
- `text-embedding-3-large` — dim 3072, $0.13/1M

**Local** (options):
- `BAAI/bge-small-en` (Recommended) — dim 384, 133 MB
- `BAAI/bge-base-en` — dim 768, 450 MB
- `BAAI/bge-large-en` — dim 1024, 1.3 GB

### Step 3 — Check API key

Solo se provider != local && provider != none. Determina la env var attesa:
- openrouter → `OPENROUTER_API_KEY`
- voyage → `VOYAGE_API_KEY`
- openai → `OPENAI_API_KEY`

Bash check: `[ -n "$<VAR>" ] && echo set || echo MISSING`

Se MISSING, dai istruzioni precise senza eseguire azioni:

```
⚠ API key '<VAR>' non trovata nella shell env.

Setta in una di queste posizioni:
  1. Shell profile (permanente):
     echo 'export <VAR>=<your-key>' >> ~/.zshrc && source ~/.zshrc

  2. Secrets file del progetto (gitignored):
     echo '<VAR>=<your-key>' >> .secrets.env
     # poi prima di lanciare claude:
     source .secrets.env

Dopo aver settato la key, restart CC perché il subprocess MCP rilegga l'env.
```

Se set: prosegui senza commenti, va bene.

### Step 4 — Update .mcp.json

Leggi `.mcp.json` con `Read`. Trova `mcpServers.anja_memory.env`. Aggiorna:
- `ANJA_EMBED_PROVIDER`: provider scelto (lowercase)
- `ANJA_EMBED_MODEL`: model scelto

Se provider != none && provider != local, aggiungi anche la variable substitution per la API key:
- openrouter → `"OPENROUTER_API_KEY": "${OPENROUTER_API_KEY}"`
- voyage → `"VOYAGE_API_KEY": "${VOYAGE_API_KEY}"`
- openai → `"OPENAI_API_KEY": "${OPENAI_API_KEY}"`

Usa `Edit` per modifica chirurgica del block `env`, preservando il resto.

Se la key esiste già con valore diverso: aggiornala. Se manca: aggiungila.

Aggiungi anche `code` al `ANJA_TOOL_GROUPS` se non già presente.

### Step 5 — Conferma

Output user-facing:
```
✓ Embed config aggiornato in .mcp.json:
  Provider: <provider>
  Model:    <model>
  Tool groups: <gruppi attivi, incluso 'code'>

⚠ Restart Claude Code per applicare (subprocess MCP non rilegge env senza respawn):
  - In CLI: /exit poi riapri
  - In webapp: chiudi e riapri la chat

Poi puoi:
  /anja-index-code        # build vector index del codebase
  Oppure direttamente:    # chat → "trova il code che gestisce X"
```

### Step 6 — Log

Chiama `mcp__anja_memory__wiki_log_append`:
- `type`: "decision"
- `description`: "embed config → provider=<provider>, model=<model>"

## Note

- `.mcp.json` resta commitable in git: contiene solo references (`${VAR}`), non secrets
- Cambiare provider con dim diversa richiede full re-index: `/anja-index-code --force`
- L'auto-detect di `code.search` continua a funzionare: se index esiste e provider matcha → level 2, altrimenti fallback

## Edge cases

- `.mcp.json` mancante → errore con suggestion "Lancia `/anja-init` o `python3 scripts/upgrade_triade.py`"
- Server `anja_memory` non in `mcpServers` → errore
- Provider/model "other" custom → accetta free-form input via AskUserQuestion
