# anja — plugin Claude Code

> Trasforma qualunque progetto software in una **knowledge base self-maintained + memoria identitaria + ricerca semantica del codice**, gestita end-to-end dall'agent dentro Claude Code.

**Stato**: v0.8.0 — usable in production. Estratto da AnjaHub monorepo. License MIT.

## Cosa fa, in 6 punti

1. **Wiki strutturato per progetto** in `.anjawiki/wiki/` (entities, concepts, sources, analysis, sessions) mantenuto dall'agent via tool MCP CRUD + lint + rename + backlinks.
2. **Memoria identitaria** in 4 layer: wiki semantico + user profile + soul agent + sessions journal.
3. **Ricerca semantica del codice** (`code.search`): hybrid 3-livelli (ripgrep → LLM rerank → vector embedding sqlite-vec). Provider pluggable (OpenRouter default, Voyage AI, OpenAI, local sentence-transformers). Description con trigger prescrittivi USE/SKIP così l'agent sceglie autonomamente vs `Grep` in base alla natura della query (semantica/concettuale → code.search, nome esatto → Grep).
4. **Roadmap task come 4° file speciale**: `roadmap.md` con priority/owner/est, 6 tool MCP, slash command `/anja-task`, focus top-5 P0/P1 al SessionStart per continuity multi-agent.
5. **Auto-summary di sessione** in background allo SessionEnd (subprocess detached, non blocca `/exit`).
6. **Skill management 3-livelli** (v0.8.0): SKILL.md con frontmatter strutturato in `.anjawiki/skills/<slug>/`, discovery multi-source (project + user-global + plugin), progressive disclosure (`skill.list` → `skill.load` → `skill.read_file`), e write-side agent-managed (`skill.save / patch / edit / delete / write_file / remove_file`) per memoria procedurale persistente. Catalog Level 0 auto-iniettato al SessionStart.

## Install

### Prerequisiti

- Claude Code CLI
- Python 3.10+ (3.12 raccomandato — `brew install python@3.12` su macOS)
- (Opzionale per code search) `pip install sqlite-vec httpx`

### Install via marketplace

Dentro Claude Code in un progetto qualunque:

```
/plugin marketplace add https://github.com/vincent-vigorito/anjadev.git
/plugin install anja@anjadev
```

CC clona automaticamente il repo in `~/.claude/plugins/marketplaces/anjadev/`. Aggiornamento successivo:

```
/plugin update anja@anjadev
```

Per dev locale del plugin (contributor only): clone manuale in `~/Documents/anjadev/` e `marketplace add /Users/$(whoami)/Documents/anjadev` su path locale.

### Setup primo progetto

```bash
cd ~/Documents/my-project
claude
```

Dentro Claude Code:

```
/anja-init                # scaffolda .anjawiki/ (wiki + meta + config + triade AGENTS/SOUL/TOOLS)
/anja-config              # AskUserQuestion: scegli provider + model embedding
/anja-index-code          # build vector index del codebase
```

Poi nella chat usa naturalmente: *"cosa è X?"*, *"trova il code che gestisce auth"*, *"aggiungi task per refactor Y"* — l'agent richiama i tool MCP appropriati.

### Setup API key embedding

`.anjawiki/.secrets.env` (gitignored automaticamente):

```bash
echo "OPENROUTER_API_KEY=sk-or-..." >> .anjawiki/.secrets.env
# o VOYAGE_API_KEY / OPENAI_API_KEY a seconda del provider scelto
```

Il server MCP `anja_memory` **auto-loada** all'avvio — niente shell setup. Restart CC dopo il primo setup.

## Slash command

| Command | Descrizione |
|---|---|
| `/anja-init` | Scaffolda `.anjawiki/` (cold) o analizza codebase (analyze mode) |
| `/anja-ingest <path\|url>` | Ingerisci fonte nel wiki strutturato |
| `/anja-query <question>` | Interroga wiki, opzionale filing come analysis page |
| `/anja-refresh` | Reconcile wiki ↔ codebase: diff vs last snapshot + update entity toccate |
| `/anja-lint` | Health check: orfani, broken links, frontmatter, stale |
| `/anja-status` | Riepilogo identità + counts + ultimo log |
| `/anja-task add\|list\|done\|triage` | Gestione roadmap.md |
| `/anja-config` | AskUserQuestion: provider + model embed (scrive in `.mcp.json`) |
| `/anja-index-code` | Build/refresh vector index del codebase |

## MCP tools (74 totali via `mcp_memory_server`)

Esposti via stdio, filtrabili via env `ANJA_TOOL_GROUPS` (14 gruppi).

### Gruppo `wiki` (18 tool)
`wiki.search`, `wiki.read`, `wiki.upsert_entity`, `wiki.upsert_concept`, `wiki.upsert_source`, `wiki.upsert_analysis`, `wiki.update_overview`, `wiki.index_update`, `wiki.log_append`, `wiki.backlinks`, `wiki.lint`, `wiki.rename`, `wiki.replace_links`, `wiki.delete`, `wiki.tree`, `wiki.stats`, `wiki.export`, `wiki.attach_image`

### Gruppo `skills` (9 tool) — v0.8.0
**Read-side (Level 0/1/2)**: `skill.list`, `skill.load`, `skill.read_file`
**Write-side (agent-managed)**: `skill.save`, `skill.patch` (find/replace mirato), `skill.edit`, `skill.delete`, `skill.write_file`, `skill.remove_file`

### Gruppo `roadmap` (6 tool)
`roadmap.list`, `roadmap.add`, `roadmap.update`, `roadmap.complete`, `roadmap.block`, `roadmap.archive`

### Gruppo `code` (3 tool)
`code.search` (hybrid 3-livelli), `code.reindex` (build/refresh vector index), `code.status` (stats index)

### Gruppo `memory` (3 tool)
`memory.recall`, `memory.write`, `memory.timeline`

### Gruppo `sessions` (3 tool)
`sessions.list`, `sessions.read`, `sessions.summarize` (claude CLI haiku subprocess)

### Altri gruppi
`soul` (2), `user` (2), `agents` (2), `tasks` (3), `workspace` (5), `kanban` (8), `goals` (7), `pp` (3)

## Architettura

```
anja/
├── .claude-plugin/plugin.json   # manifest plugin
├── commands/                    # 9 slash command (.md)
├── hooks/
│   ├── session_start.py         # carica focus roadmap + ultime 5 log
│   └── session_end.py           # write session file + spawn auto-summary bg
├── agents/                      # subagent (wiki-maintainer)
├── scripts/
│   ├── mcp_memory_server.py     # MCP server stdio (v1.7.0, 28 tool)
│   ├── code_db.py + code_index.py + code_search.py + embed_providers.py
│   ├── roadmap_io.py
│   ├── summarize_session_bg.py  # detached process per auto-summary
│   ├── init_project.py          # scaffolding /anja-init
│   └── ... (lint_checks, slugify, compose_claude_md, status, ecc.)
├── templates/
│   ├── project-skeleton/        # struttura .anjawiki/ scaffoldata da /anja-init
│   ├── soul-baselines/          # personality presets per type (dev/research/...)
│   └── triade-skeleton/         # AGENTS/SOUL/TOOLS scaffolding
├── skills/                      # skill descrittive workflow (ingest, query, lint, refresh, init-analyze)
├── SCHEMA.md                    # wire format pubblico .anjawiki/
└── README.md                    # questo file
```

### Wire format pubblico

Il layout `.anjawiki/` è un **contratto pubblico** descritto in [`SCHEMA.md`](./SCHEMA.md). Consumatori esterni (hub AnjaHub, IDE plugin, tool di sync) possono assumere il layout, frontmatter required, formato log e wikilinks come stabili entro la stessa MAJOR version. Vedi anche `.anjawiki/.schema-version` scritto da `/anja-init`.

## Env vars

| Var | Default | Descrizione |
|---|---|---|
| `ANJA_SCOPE` | `project` | `project` \| `hub` \| `agent` — determina path resolution |
| `ANJA_ROOT` | — | Path del root scope (set da `.mcp.json` per ogni progetto) |
| `ANJA_TOOL_GROUPS` | tutti | CSV: `memory,sessions,soul,user,skills,wiki,roadmap,code` — filtra tool MCP |
| `ANJA_EMBED_PROVIDER` | `openrouter` | `openrouter` \| `voyage` \| `openai` \| `local` |
| `ANJA_EMBED_MODEL` | provider-default | es. `qwen/qwen3-embedding-8b` per openrouter |
| `ANJA_AUTO_SUMMARY` | `1` | `0` per disabilitare auto-summary background |
| `ANJA_HUB` | — | Override path hub (per scope=project che vuole user-global) |

## Filosofia

- **Stdlib first**: nessuna dipendenza esterna obbligatoria per il core (sqlite-vec + httpx opzionali per code search).
- **MCP-first**: ogni capability via tool stdio, token-controlled via `ANJA_TOOL_GROUPS`.
- **Edit minimali**: tre righe simili > astrazione prematura.
- **Niente commenti ovvi**: solo "perché" non ovvi.
- **Wiki self-maintained**: l'agent è responsabile dell'igiene (lint, rename, dedup) come prima cittadina.

## Dev setup (per contributor)

```bash
git clone git@github.com:vincent-vigorito/anja.git ~/Documents/anja-platform
cd ~/Documents/anja-platform

# Il plugin vive in anja/. Editing diretto sui file. Nessun build step.
# Per testare in un progetto reale:
cd ~/Documents/my-project
/plugin marketplace add ~/Documents/anja-platform
/plugin install anja@anja-marketplace
/anja-init --type dev
```

### Workflow dev tipico

| Modifica | Come ricaricare |
|---|---|
| MCP server (`scripts/mcp_*.py`) | Nuova chat in CC (subprocess MCP rispawna) |
| Slash command (`commands/*.md`) | Nuova chat |
| Hook (`hooks/*.py`) | Nuova chat (hook caricato a `SessionStart`) |
| Template (`templates/`) | Nessun reload; effetto su prossimo `/anja-init` |

### Smoke test

```bash
python3 -m pytest anja/tests/ -v
# oppure:
python3 anja/tests/test_mcp_smoke.py
```

### Convenzioni codice

- Python 3.10+ (typing moderno: `X | None`, `list[T]`, ecc.)
- Solo stdlib nel core. Eccezioni motivate: `sqlite-vec`, `httpx` (opt-in per code search)
- File <500 LOC per pezzo, eccetto `mcp_memory_server.py` (dispatcher centrale, motivato)
- Tool MCP: handler `def tool_<group>_<name>(args: dict) -> dict`, return JSON-serializable, errors come `{"error": "msg", "hint": "..."}`

## Changelog

- **0.7.0** (2026-05-19) — 5 nuove feature roadmap-complete:
  - Onboarding nudge in `session_start.py`: suggerimento `/anja-init` in progetti senza `.anjawiki/` (idempotente via marker `~/.anja-nudged/`)
  - Validation soft in `wiki.upsert_*`: `_warnings` array per sezioni canoniche mancanti (entity: Sintesi/Dettagli/Apparizioni/Connessioni, concept: Definizione/Perché conta/Esempi/Riferimenti, ecc.)
  - `/anja-refresh` workflow completo: slash command + skill + diff vs last codebase-snapshot
  - `wiki.export` MCP tool: format md (zip)/json (dump strutturato)/html (static site con wikilinks risolti)
  - `wiki.attach_image` MCP tool: copia/scarica immagine in raw/ + append markdown link nella page
  - mcp_memory_server 1.8.0 → 1.9.0 (30 tool totali: +export +attach_image)
  - Smoke test 16/16 verde
- **0.6.2** (2026-05-19) — `code.search` description prescrittiva (USE/SKIP trigger pattern) per autoselect vs Grep
- **0.6.1** (2026-05-19) — fix `session_end` hook: skip SessionEnd con `reason=other` (compact/resume CC interni); fix README install URL HTTPS
- **0.6.0** (2026-05-18) — Initial commit: estrazione plugin da AnjaHub monorepo (MIT). 9 slash command + 28 MCP tool

## Rapporto con AnjaHub

`anjadev` è stato estratto come repo pubblico standalone (MIT). La piattaforma `AnjaHub` (webapp Mission Control + Telegram bot + routines daemon + workspace + goals) resta privata e usa questo plugin via marketplace + schema condiviso `.anjawiki/` (wire format pubblico, vedi [`SCHEMA.md`](./SCHEMA.md)).

L'hub legge i wiki dei progetti dev via filesystem usando lo schema documentato qui; opzionalmente può chiamare il server MCP via subprocess per scritture. Nessuna dipendenza di codice tra i due repo.

## Licenza

[MIT](./LICENSE) © 2026 Vincent Vigorito
