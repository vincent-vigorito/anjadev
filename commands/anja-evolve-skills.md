---
description: Workflow di review per auto-improvement delle skill (pattern Hermes). Legge inbox PostToolUse hook, propone patch SKILL.md via LLM, applica dopo conferma utente.
argument-hint: [--batch N] [--marker-reset] [--apply-all]
allowed-tools: Bash, mcp__anja_memory__skill.patch, mcp__anja_memory__skill.load, mcp__anja_memory__skill.read_file, AskUserQuestion
---

# /anja-evolve-skills

Trigger del workflow `evolve-skills` (vedi skill omonima per dettagli). Workflow in 4 step:

1. **Genera proposals** — esegui `evolve.py` per analizzare last N skill invocations dall'inbox
2. **Show proposals** — mostra le proposte memorable=true all'utente in markdown con diff
3. **Confirm per proposta** — per ognuna: approve / skip / edit
4. **Apply** — invoca `skill.patch` MCP tool per ogni approved + audit log

Argomenti: `$ARGUMENTS`

## Workflow

### Step 1 — Run evolve.py

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/evolve-skills/scripts/evolve.py" --batch 5
```

(Default batch 5; override via `--batch N` in argomenti utente. Aggiungi `--marker-reset` se utente vuole rivedere dall'inizio.)

Output JSON status. Se `status: no_new_entries` → "Nessuna nuova skill invocation da analizzare. Niente da fare." e termina.

### Step 2 — Read proposals + filter memorable

Leggi `~/.anja/skill_evolution_proposals.jsonl` (le ultime N righe processate ora). Filtra:
- `memorable: true`
- `applied: false`
- nessun `review_error`

Se nessuna proposta memorable → "Analizzate {N} invocations, nessun pattern memorabile rilevato. La review ha già marcato le entry come processate." e termina.

### Step 3 — Per ogni proposta memorable, chiedi conferma

Mostra in markdown structured:

```markdown
🧠 Proposta evolution [1/N]

**Skill**: research-duckduckgo
**Triggered by**: invocation @ 2026-05-23T14:25:38Z (args: "test query" 5, exit 0)

**Rationale**: Edge case: query specifica con 0 risultati → tip per re-query.

**Patch proposta** (aggiunta sezione "Edge case" alla SKILL.md):

```markdown
## Edge case

- Se ricerca ritorna 0 risultati con query molto specifica, prova varianti più ampie...
```

**Procedo con apply?** (rispondi `sì` per applicare, `no` per skippare, `edit` per rifinire il testo)
```

Usa `AskUserQuestion` tool per gestire la conferma (3 options: approve, skip, edit).

Se `--apply-all` in argomenti → skip questo step, applica tutte automaticamente (modalità batch trusted).

### Step 4 — Apply patches

Per ogni approved:

1. Carica SKILL.md corrente: `skill.load(name)` → recupera body
2. Costruisci `new_text = old_text + "\n\n" + patch_proposal.section_to_append` (append in fondo se non specificata posizione)
3. Invoca `skill.patch(name=<skill>, old_text=<last 100 chars di body>, new_text=<same + patch>)`
4. Mark proposta come `applied: true` riscrivendo proposals.jsonl
5. Append a `~/.anja/skill_evolution_applied.jsonl`:
   ```json
   {"ts": "...", "skill": "...", "rationale": "...", "patch_summary": "added Edge case section"}
   ```

### Step 5 — Summary

```
✅ Skill evolution completata
- Reviewed: 5 invocations
- Memorable: 3
- Applied: 2 (1 skippata da utente)
- Backup salvati in <skill>/.history/

Skill aggiornate:
- research-duckduckgo (1.0.0 → 1.0.1)
- csv-to-markdown (1.0.0 → 1.0.1)
```

## Edge cases

- **claude CLI mancante**: evolve.py ritorna errore in review_error. Skippa quelle proposte, segnala all'utente.
- **inbox vuoto**: skip cleanly con messaggio
- **utente cancella mid-flow**: marker non viene scritto, proposals.jsonl resta con `applied: false` → next run riprocessa

## Anti-pattern

1. ❌ Apply automatico senza conferma — solo `--apply-all` esplicito
2. ❌ Re-processare entry già marcate — usa marker
3. ❌ Patch invasive (modifiche > 50% body skill) — evolve.py limita patch a sezioni "append only" di default
