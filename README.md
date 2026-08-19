# anja — plugin Claude Code

> Trasforma qualunque progetto software in una **knowledge base self-maintained + memoria identitaria + ricerca semantica del codice**, gestita end-to-end dall'agent dentro Claude Code.

**Stato**: v0.21.0 — usable in production. Plugin CLI standalone (nessuna dipendenza da AnjaHub). License MIT. Storia completa in [`CHANGELOG.md`](./CHANGELOG.md).

## Cosa fa, in 7 punti

1. **Wiki strutturato per progetto** in `.anjawiki/wiki/` (entities, concepts, sources, analysis, sessions) mantenuto dall'agent via tool MCP CRUD + lint + rename + backlinks.
2. **Memoria identitaria** in 4 layer: wiki semantico + user profile + soul agent + sessions journal.
3. **Ricerca semantica del codice** (`code.search`): hybrid 3-livelli (ripgrep → LLM rerank → vector embedding sqlite-vec). Provider pluggable (OpenRouter default, Voyage AI, OpenAI, local sentence-transformers). Description con trigger prescrittivi USE/SKIP così l'agent sceglie autonomamente vs `Grep` in base alla natura della query (semantica/concettuale → code.search, nome esatto → Grep).
4. **Roadmap task come 4° file speciale**: `roadmap.md` con priority/owner/est, 6 tool MCP, slash command `/anja-task`, focus top-5 P0/P1 al SessionStart per continuity multi-agent.
5. **Auto-summary di sessione** in background allo SessionEnd (subprocess detached, non blocca `/exit`).
6. **Skill management 3-livelli** (v0.8.0): SKILL.md con frontmatter strutturato in `.anjawiki/skills/<slug>/`, discovery multi-source (project + user-global + plugin), progressive disclosure (`skill.list` → `skill.load` → `skill.read_file`), e write-side agent-managed (`skill.save / patch / edit / delete / write_file / remove_file`) per memoria procedurale persistente. Catalog Level 0 auto-iniettato al SessionStart.
7. **Knowledge graph wiki ↔ codice** (v0.9.0): embedding condiviso tra wiki pages e code chunks → k-NN cross-kind (`graph.semantic_neighbors`) scopre "questa entity copre quale file?" e duplicati semantici. `graph.report` produce `GRAPH_REPORT.md` con god nodes + cluster + surprise edges (alta similarity, niente `[[wikilink]]`) + auto-mapping wiki→code per token reduction agent. `graph.html` genera visualizer Cytoscape standalone con sidebar search. Re-embed automatico: inline nei `wiki.upsert_*` + PostToolUse hook su Write/Edit + SessionEnd consistency check.

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

Se un progetto ha un wiki scaffoldato con una versione precedente del plugin, dopo l'update lancia `/anja-upgrade` nel progetto per portarlo al layout corrente (non-distruttivo).

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

## Cross-harness setup (Codex · Grok · Gemini · OpenCode)

Il core (`mcp_memory_server.py` + `mcp_code_server.py`) è **JSON-RPC 2.0 over stdio**
standard, stdlib pure, zero dipendenze → gira su **qualunque host MCP**, non solo Claude
Code. Stessi 3 env ovunque: `ANJA_SCOPE` (`project`|`hub`|`agent`), `ANJA_ROOT` (path del
root), `ANJA_TOOL_GROUPS` (filtro opzionale, default tutti i gruppi).

Verificato con handshake `initialize` + `tools/list` su stdio puro: `anja_memory` espone
27 tool (con `memory,wiki,roadmap`), `anja_code` 1 tool (`execute_python`). Nessuna
modifica al plugin: cambia solo *dove* dichiari il server. `<ANJADEV>` = path del plugin
installato (`~/.claude/plugins/marketplaces/anjadev`) o di un clone locale del repo.

**Claude Code** — `.mcp.json` del progetto (formato di riferimento, scritto da `/anja-init`):

```json
{
  "mcpServers": {
    "anja_memory": {
      "command": "python3",
      "args": ["<ANJADEV>/scripts/mcp_memory_server.py"],
      "env": { "ANJA_SCOPE": "project", "ANJA_ROOT": "/abs/project", "ANJA_TOOL_GROUPS": "memory,wiki,roadmap,code" }
    }
  }
}
```

**OpenAI Codex** — `~/.codex/config.toml` (o `.codex/config.toml` project-scoped):

```toml
[mcp_servers.anja_memory]
command = "python3"
args = ["<ANJADEV>/scripts/mcp_memory_server.py"]

[mcp_servers.anja_memory.env]
ANJA_SCOPE = "project"
ANJA_ROOT = "/abs/project"
ANJA_TOOL_GROUPS = "memory,wiki,roadmap,code"
```

Oppure via CLI:
`codex mcp add anja_memory --env ANJA_SCOPE=project --env ANJA_ROOT=/abs/project -- python3 <ANJADEV>/scripts/mcp_memory_server.py`

**Grok Build** (xAI) — **zero config**, verificato sul campo: ha una *Claude-compatibility*
nativa (`grok inspect` → "Harness Compatibility: claude", tutto on di default). Carica da solo:
i **plugin Claude Code** installati (skills + hooks di anja), il **`.mcp.json` di progetto**
formato CC, `AGENTS.md` e perfino le permissions da `.claude/settings.local.json`.

Unico passo richiesto: dare il **trust al progetto** dentro la TUI (`/hooks` → *trust this
project*, oppure dal pannello `/plugins` → tab Hooks) — senza trust, MCP e hook restano
bloccati per sicurezza e l'agente ripiega sull'accesso bash-native. Diagnostica:
`grok mcp doctor` e `grok inspect`.

(Il grok-cli open-source di superagent-ai è un tool diverso: lì serve `.grok/settings.json`
con `mcpServers` stile Claude.)

**OpenCode** — `opencode.json`, chiave `mcp`, tipo `local` (`command` è un array, env in `environment`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "anja_memory": {
      "type": "local",
      "command": ["python3", "<ANJADEV>/scripts/mcp_memory_server.py"],
      "enabled": true,
      "environment": { "ANJA_SCOPE": "project", "ANJA_ROOT": "/abs/project", "ANJA_TOOL_GROUPS": "memory,wiki,roadmap,code" }
    }
  }
}
```

**Gemini CLI** — `~/.gemini/settings.json` (o `.gemini/settings.json` project-scoped), chiave `mcpServers`:

```json
{
  "mcpServers": {
    "anja_memory": {
      "command": "python3",
      "args": ["<ANJADEV>/scripts/mcp_memory_server.py"],
      "env": { "ANJA_SCOPE": "project", "ANJA_ROOT": "/abs/project", "ANJA_TOOL_GROUPS": "memory,wiki,roadmap,code" }
    }
  }
}
```

Gemini legge `GEMINI.md` (non `AGENTS.md`): `compose_claude_md.py` lo genera come **symlink → `AGENTS.md`**. Alternativa pulita per distribuzione: una Gemini *extension* (`contextFileName: AGENTS.md` + `mcpServers`), installabile via path/GitHub.

### Context file generati (da `AGENTS.src.md`)

Il compose produce, oltre ad `AGENTS.md` (composed, letto nativo da Codex/Grok):
- `CLAUDE.md` = `@AGENTS.md`  (Claude Code)
- `GEMINI.md` = symlink → `AGENTS.md`  (Gemini CLI)

Non editare i generati: il context vive in `AGENTS.src.md`.

> **Automatismi (hook).** Claude Code **e Grok CLI** hanno hook compatibili (`SessionStart/End`,
> `PreToolUse/PostToolUse`, … — JSON su stdin/stdout): gli automatismi anja (context injection,
> journal, re-embed) reggono su entrambi. **Codex e Gemini** non hanno hook equivalenti → "modo
> manuale": il context statico è nel file, ma il pull dinamico (roadmap/sessioni) e il journal si
> fanno via tool MCP o bash-native — vedi la sezione *Bootstrap* nel context composto.
>
> **OpenCode (full mode)**: oltre alla config MCP `opencode.json` sopra, il plugin
> `.opencode/plugin/anja.js` aggancia i lifecycle OpenCode agli hook Python anja —
> `event(session.idle)`→journal, `tool.execute.after`→re-embed wiki, `chat.message`→context
> injection. Install: symlink/copia il plugin in `.opencode/plugin/` del progetto (o in
> `~/.config/opencode/plugin/`); imposta `ANJADEV_DIR` se vive fuori dal repo. Il plugin
> traduce i dati OpenCode nel formato JSONL che gli script CC già parsano → zero modifiche
> al Python condiviso. **Validato e2e su OpenCode 1.17.4** (loading · context injection ·
> re-embed · journal con summary). Debug opt-in: `ANJA_OC_DEBUG=1` → `/tmp/anja-opencode.log`.
>
> Nota Codex: alcune versioni hanno avuto bug nel leggere `mcp_servers` da `config.toml`
> ([openai/codex#3441](https://github.com/openai/codex/issues/3441)) — verifica con la tua release.

### Install come plugin Codex (esperienza piena)

Oltre alla config MCP manuale, anjadev è un **plugin Codex completo** — stesso core di CC
(`.codex-plugin/plugin.json` ↔ `.claude-plugin/plugin.json`, stesso `skills/` + `hooks.json`):
skills + MCP + **lifecycle hooks** (context injection a `SessionStart`, wiki re-embed a `PostToolUse`).

```bash
codex plugin marketplace add vincent-vigorito/anjadev
# poi nel plugin browser della CLI: installa "anja"
```

Dà gli automatismi come su CC. Nota: il journal a `SessionEnd` parsea il transcript nel formato
CC → su Codex può servire un adattamento del parser (gli altri hook funzionano a prescindere).

> ⚠️ Verificato su codex-cli 0.137: i plugin caricano le **skills**, ma gli **MCP server del
> manifest non vengono ancora avviati** dalla CLI → registra gli MCP via `config.toml`
> (project-scoped `.codex/config.toml` o `~/.codex/config.toml`, vedi sezione sopra) o
> `codex mcp add`. Il `.mcp.codex.json` del plugin resta per quando Codex li supporterà.

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
| `/anja-upgrade` | Migra progetto/hub con wiki di versione precedente al layout corrente (triade + composed + MCP + schema-version) |
| `/anja-evolve-skills` | Review auto-improvement delle skill (pattern Hermes): legge inbox PostToolUse, propone patch SKILL.md, applica dopo conferma |

## MCP tools (81 totali via `mcp_memory_server`)

Esposti via stdio, filtrabili via env `ANJA_TOOL_GROUPS` (15 gruppi).

### Gruppo `wiki` (18 tool)
`wiki.search`, `wiki.read`, `wiki.upsert_entity`, `wiki.upsert_concept`, `wiki.upsert_source`, `wiki.upsert_analysis`, `wiki.update_overview`, `wiki.index_update`, `wiki.log_append`, `wiki.backlinks`, `wiki.lint`, `wiki.rename`, `wiki.replace_links`, `wiki.delete`, `wiki.tree`, `wiki.stats`, `wiki.export`, `wiki.attach_image`

### Gruppo `skills` (9 tool) — v0.8.0
**Read-side (Level 0/1/2)**: `skill.list`, `skill.load`, `skill.read_file`
**Write-side (agent-managed)**: `skill.save`, `skill.patch` (find/replace mirato), `skill.edit`, `skill.delete`, `skill.write_file`, `skill.remove_file`

### Gruppo `graph` (7 tool) — v0.9.0 + v0.9.1
**Embedding pipeline**: `wiki.embed` (incremental, dirty-check, multi-trigger inline+hook+session-end).
**Query by ID (cross-kind)**: `graph.semantic_neighbors` (k-NN dato source slug o file path, filter per kind).
**Query by text** (v0.9.1): `graph.search_text` (embedda query libera → k-NN cross-kind), `wiki.search_semantic` (sugar wiki-only), `sessions.search_semantic` (sugar session journals).
**Report agent-friendly**: `graph.report` (scrive `GRAPH_REPORT.md` con god nodes + cluster + surprise edges + wiki↔code anchors + orphans).
**Visualizer standalone**: `graph.html` (Cytoscape single-file Obsidian-style, file-aggregated, hover-focus mode, sidebar search/filtri, apri nel browser).

### Gruppo `roadmap` (6 tool)
`roadmap.list`, `roadmap.add`, `roadmap.update`, `roadmap.complete`, `roadmap.block`, `roadmap.archive`

### Gruppo `code` (3 tool)
`code.search` (hybrid 3-livelli), `code.reindex` (build/refresh vector index), `code.status` (stats index)

### Gruppo `memory` (3 tool)
`memory.recall`, `memory.write`, `memory.timeline`

### Gruppo `sessions` (3 tool)
`sessions.list`, `sessions.read`, `sessions.summarize` (claude CLI haiku subprocess)

### Altri gruppi
`soul` (2), `user` (2), `roadmap` (6), `graph` (7, opt-in: vuole l'index)

> Dal **v0.21** questo server espone SOLO i gruppi core del plugin CLI. I tool
> hub-only (`agents`, `tasks`, `workspace`, `kanban`, `goals`, `pp`) sono stati
> spostati in AnjaHub (`anja_hub_runtime`): se un `.mcp.json` vecchio li elenca
> in `ANJA_TOOL_GROUPS`, il server parte comunque e stampa un warning su stderr.

## Architettura

```
anja/
├── .claude-plugin/plugin.json   # manifest plugin (versione — allineata da bump.sh)
├── .codex-plugin/plugin.json    # manifest plugin Codex
├── bump.sh                      # release: allinea le versioni nei 3 manifest in un colpo
├── CHANGELOG.md                 # storia release
├── commands/                    # 11 slash command (.md)
├── hooks/
│   ├── session_start.py         # carica focus roadmap + ultime 5 log
│   └── session_end.py           # write session file + spawn auto-summary bg
├── agents/                      # subagent (wiki-maintainer)
├── scripts/
│   ├── mcp_memory_server.py     # MCP server stdio (81 tool, 15 gruppi)
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
git clone git@github.com:vincent-vigorito/anjadev.git ~/Documents/anjadev
cd ~/Documents/anjadev

# Il repo È il plugin (root = plugin). Editing diretto sui file. Nessun build step.
# Per testare in un progetto reale:
cd ~/Documents/my-project
/plugin marketplace add ~/Documents/anjadev
/plugin install anja@anjadev
/anja-init --type dev
```

> **Release**: dopo ogni modifica da distribuire, `./bump.sh <major.minor.patch>` allinea
> la versione nei 3 manifest (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
> `.codex-plugin/plugin.json`), poi commit + `git tag vX.Y.Z`. Senza bump, CC vede "already at
> latest" e continua a caricare la cache pre-modifica (versiona per numero, non per git SHA).

### Workflow dev tipico

| Modifica | Come ricaricare |
|---|---|
| MCP server (`scripts/mcp_*.py`) | Nuova chat in CC (subprocess MCP rispawna) |
| Slash command (`commands/*.md`) | Nuova chat |
| Hook (`hooks/*.py`) | Nuova chat (hook caricato a `SessionStart`) |
| Template (`templates/`) | Nessun reload; effetto su prossimo `/anja-init` |

### Smoke test

```bash
python3 -m pytest tests/ -v
# oppure:
python3 tests/test_mcp_smoke.py
```

### Convenzioni codice

- Python 3.10+ (typing moderno: `X | None`, `list[T]`, ecc.)
- Solo stdlib nel core. Eccezioni motivate: `sqlite-vec`, `httpx` (opt-in per code search)
- File <500 LOC per pezzo, eccetto `mcp_memory_server.py` (dispatcher centrale, motivato)
- Tool MCP: handler `def tool_<group>_<name>(args: dict) -> dict`, return JSON-serializable, errors come `{"error": "msg", "hint": "..."}`

## Changelog

Storia completa e dettagliata in **[`CHANGELOG.md`](./CHANGELOG.md)**. Ultime release:

- **0.18.1** (2026-06-11) — **Security**: confinamento path traversal nei tool MCP `sessions.read` / `wiki.export` / `wiki.attach_image` / `memory.write` (un caller poteva indurre lettura/scrittura di file arbitrari). + `bump.sh` (single source of truth versione).
- **0.18.0** (2026-06-11) — `/anja-upgrade`: migrazione guidata di progetti/hub con wiki di versione precedente.
- **0.17.x** (2026-06-06) — plugin **Codex** nativo (skills + MCP + hooks) + **Grok Build** zero-config (compat CC nativa).
- **0.16.x** (2026-06-06) — cross-harness: `AGENTS.md` composed unificato + setup Codex/Grok/Gemini/OpenCode.
- **0.14.0–0.15.0** (2026-06-01) — Memory 2.0: retrieval ibrido BM25+vector+RRF (`wiki.search`), lossless journal, `wiki.find_duplicates`, accesso bash-native.

> Release: ogni cambiamento da distribuire richiede `./bump.sh <ver>` (allinea le versioni nei 3 manifest) + tag, altrimenti `/plugin update` non ricrea la cache e gira il codice vecchio.

## Rapporto con AnjaHub

`anjadev` è il **formato + il plugin CLI** (wiki, identità, code search) per qualunque harness. [Anja Hub](https://github.com/vincent-vigorito/anja-hub) (webapp Mission Control + Telegram bot + routines daemon + workspace + goals, MIT) è un **consumer**: lo monta via marketplace e condivide lo schema `.anjawiki/` (wire format pubblico, vedi [`SCHEMA.md`](./SCHEMA.md)).

La direzione di dipendenza è una sola: **AnjaHub usa anjadev, anjadev non sa che AnjaHub esiste**. Dal v0.21 il server MCP non importa nulla dalla webapp dell'hub; i tool di piano di lavoro degli agent (kanban, goals, delega, workspace, task one-shot, catalogo PP) vivono nell'hub, nel server `anja_hub_runtime`, con gli stessi nomi di prima.

## Licenza

[MIT](./LICENSE) © 2026 Vincent Vigorito
