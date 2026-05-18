---
description: Inizializza un wiki anja nella directory corrente
argument-hint: [--type personal|research|business|dev|automation] [--cold|--analyze] [--name <name>]
allowed-tools: Bash, Read, AskUserQuestion
---

# /anja-init

Inizializza la struttura `.anjawiki/` in un progetto. Vedi il template in `${CLAUDE_PLUGIN_ROOT}/templates/project-skeleton/` per la struttura risultante.

Argomenti passati dall'utente: `$ARGUMENTS`

## Workflow

### Step 1: Parse argomenti

Estrai dagli argomenti:
- **`--type <type>`** — uno tra `personal`, `research`, `business`, `dev`, `automation`. Default: `dev`.
- **`--cold`** o **`--analyze`** — modalità di init. Se non specificato, default basato su type:
  - `dev`, `automation` → `--analyze`
  - `personal`, `research`, `business` → `--cold`
- **`--name <name>`** — nome del progetto. Default: basename della cwd.

**Vincolo v1:** in questa fase è supportato solo `--type dev`. Se l'utente passa altro, output:
> "Tipo X non ancora supportato in v1. Riprova con `--type dev` (gli altri tipi arriveranno in Fase 4)."
e termina.

### Step 2: Verifica esistenza `.anjawiki/`

Esegui via Bash: `test -d .anjawiki && echo "exists" || echo "absent"`.

Se output è `exists`, usa **AskUserQuestion** con queste opzioni:

- **Sovrascrivi** — rimuovi `.anjawiki/` esistente e ricomincia da zero (perdi tutto il wiki)
- **Abort** — non fare nulla

(In v1 non supportiamo "aggiorna solo lo schema preservando il wiki". Arriverà in Fase 4.)

Se "Sovrascrivi": `rm -rf .anjawiki` via Bash, poi continua.
Se "Abort": termina con messaggio "Init annullato. Wiki esistente preservato."

### Step 3: Esegui scaffolding

Esegui via Bash:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py" \
  --type "<TYPE>" \
  --mode "<MODE>" \
  --target ".anjawiki" \
  ${NAME_FLAG}
```

dove:
- `<TYPE>` è il type determinato (in v1 sempre `dev`)
- `<MODE>` è `cold` o `analyze`
- `${NAME_FLAG}` è `--name "<NAME>"` se l'utente ha specificato un nome, altrimenti vuoto (lo script userà il basename della parent del target)

Lo script:
- Genera token `swk_<hex12>`
- Copia il template in `.anjawiki/`
- Sostituisce i placeholder nei file template
- Stampa conferma con token

Cattura l'output dello script per estrarre il token generato.

### Step 4: Output finale (modalità cold)

Se la modalità è `cold`, mostra all'utente:

```
✓ Wiki anja inizializzato in .anjawiki/
  Token: <token-generato>
  Type:  <type>
  Mode:  cold

Prossimi step:
  /anja-ingest <path|url>   per ingerire una fonte
  /anja-query <domanda>     per interrogare il wiki
```

### Step 5: Modalità analyze (skill `init-analyze`)

Se la modalità è `analyze`, dopo il successo dello scaffolding cold (Step 3):

1. Mostra all'utente: "Scaffolding completato. Avvio analisi del codebase con la skill `init-analyze`..."
2. **Attiva la skill `init-analyze`** definita in `${CLAUDE_PLUGIN_ROOT}/skills/init-analyze/SKILL.md`
3. Segui il workflow lì descritto: lettura memoria CC + Explore subagent + batch read fissi + git history + AskUserQuestion per scope + deep-dive mirato + scrittura wiki + log entry `init-analyze`

La skill produce in output:
- `wiki/overview.md` popolato con tesi corrente
- `wiki/entities/<slug>.md` per ogni sottosistema confermato
- `wiki/concepts/<slug>.md` per ogni pattern/concept ricorrente
- `wiki/sources/codebase-snapshot-<date>.md`
- `wiki/index.md` aggiornato con categorizzazione
- entry log `init-analyze`

### Step 6: Verifica finale

Verifica che `.anjawiki/` sia stato creato correttamente leggendo `.anjawiki/meta.yaml` con `Read`. Conferma che il token nel file corrisponde a quello stampato dallo script. Se c'è discrepanza, segnala l'errore all'utente.

## Note di esecuzione

- **Niente Write o Edit** in questo comando — tutta la creazione di file passa dallo script Python.
- **Niente sostituzioni manuali di placeholder** — lo script Python le fa tutte.
- **Errori dello script Python**: se exit code != 0, mostra stderr all'utente e termina.
