---
description: Build o refresh del vector index del codebase per ricerca semantica via code.search level 2
argument-hint: [--force] [--limit N]
allowed-tools: mcp__anja_memory__code_reindex, mcp__anja_memory__code_status, mcp__anja_memory__wiki_log_append
---

# /anja-index-code — Build vector index del codebase

Costruisce o aggiorna `.anjawiki/code-index.db` (sqlite-vec) per abilitare ricerca semantica via `code.search(query, smart_level=2)`. Incremental di default: indicizza solo i file modificati dall'ultimo `last_indexed_sha` git. `--force` per full rebuild.

Argomenti: `$ARGUMENTS`

## Pre-flight

1. Verifica `.anjawiki/meta.yaml` esiste nella cwd. Se no: "Wiki non inizializzato. Lancia `/anja-init` prima."
2. Verifica che esista una chiave API per il provider embedding (default `OPENROUTER_API_KEY`):
   - Check env `ANJA_EMBED_PROVIDER` (default `openrouter`)
   - Per `openrouter` → `OPENROUTER_API_KEY` required
   - Per `voyage` → `VOYAGE_API_KEY`
   - Per `openai` → `OPENAI_API_KEY`
   - Per `local` → `pip install sentence-transformers` (no API key)
   - Se mancante: spiega come settare nel `.secrets.env` del progetto e termina

## Workflow

### Step 1 — Status preliminare

Chiama `mcp__anja_memory__code_status` per vedere stato attuale:
- Se `indexed=false`: prosegui con full index
- Se `indexed=true`: mostra "Index esistente: N chunks, provider=X, last_indexed_sha=Y", chiedi conferma se l'utente vuole proseguire (oppure procedi se `--force`)

### Step 2 — Reindex

Chiama `mcp__anja_memory__code_reindex` con args:
- `force`: true se `--force` in arguments, altrimenti false
- `limit`: int se `--limit N` in arguments

Operazione può richiedere:
- Full index 50k LOC: ~30-60s (provider API) o ~5-10min (local)
- Incremental: pochi sec

### Step 3 — Output

Restituisci all'utente:
```
✓ Index updated
  Provider: <name> / <model> (dim <N>)
  Files indexed: <N>
  Chunks indexed: <N>
  Git SHA: <short>
  Mode: <full|incremental>
```

### Step 4 — Log

Chiama `mcp__anja_memory__wiki_log_append`:
- `type`: "refresh"
- `description`: "code-index built: <N> chunks via <provider>/<model>, sha <short>"

## Note

- L'index file `.anjawiki/code-index.db` può crescere (1-2 MB per 10k chunks). **Aggiungilo a `.gitignore`** se non vuoi committarlo. È rebuildable in ogni momento.
- Se cambi provider (es. da openrouter a voyage), serve `--force` perché la dim degli embedding cambia.
- Auto-rebuild su commit: setta env `ANJA_AUTO_INDEX=1` + aggiungi git hook (TODO: comando dedicato per setup hook).

## Edge cases

- API key mancante → errore esplicito con istruzioni setup
- Codebase enorme (>100k file) → suggerisci `--limit 5000` per primo test, poi full
- `httpx` o `sqlite-vec` non installati → errore con istruzione `pip install sqlite-vec httpx`
- Git non disponibile → full re-index ad ogni run (incremental skip)
