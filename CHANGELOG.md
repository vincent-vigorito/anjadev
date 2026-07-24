# Changelog

All notable changes to the `anja` plugin.

## v0.19.8 — 2026-07-24

**Auto-routing della delega: `agent.delegate` sceglie da solo lo specialista giusto.**

### Added

- `target` è ora **opzionale**: senza, l'agent viene scelto dalle sue
  `auto_route_keywords` matchate sul prompt (word-boundary, nessuna chiamata LLM →
  deterministico e ripetibile). La risposta include `routing` con l'agent scelto,
  le keyword che hanno fatto match e i runner-up — decision-trail leggibile.
- Nuovo parametro `workspace` per restringere il routing a un brand. Se non passato
  viene **inferito dal prompt**; a parità di punteggio fra workspace diversi il
  routing **si rifiuta e chiede disambiguazione** invece di tirare a sorte (gli
  specialisti hanno nomi uguali nei pod: sbagliare workspace = pubblicare sul brand
  sbagliato).
- A parità di keyword vince chi ha `delegate_tools`, cioè chi può davvero eseguire
  il task invece di limitarsi a descriverlo.

## v0.19.7 — 2026-07-24

**Delega non distruttiva + tool di produzione dichiarabili.**

### Fixed

- `agent.delegate`: al **timeout** il lavoro non va più perso — il testo prodotto
  fin lì viene restituito (`partial: true`, `timed_out: true`) e **loggato nella
  sessione** dell'agent (`timed_out: true` nel frontmatter). Anche gli errori
  inattesi riportano il `partial_response` quando c'è. Prima il parziale viveva
  dentro la coroutine cancellata e spariva senza lasciare traccia.

### Added

- `agent.delegate` legge `delegate_tools` dalla config dell'agent: la lista dei
  **tool nativi** concessi in delega, filtrata su whitelist (`Read/Write/Edit/
  MultiEdit/Bash/Grep/Glob/LS/TodoWrite/WebFetch/WebSearch/NotebookEdit/Task`).
  Default invariato e read-only (`Read/Grep/Glob`): serve per gli agent che devono
  **produrre** (generare kit, eseguire script) e non solo consultare — vanno
  abbinati a `bypass_permissions: true`, altrimenti in headless le scritture
  restano bloccate dal permission system. La risposta espone `native_tools` e, in
  caso di timeout, un `hint` che spiega come sbloccare.

## v0.19.6 — 2026-07-24

**Roster e delega cross-workspace: gli agent dei workspace sono visibili e delegabili dalle sessioni hub.**

### Fixed

- `agent.list`: elenca il roster COMPLETO — agent hub-level + i team di tutti i
  workspace (`<hub>/workspaces/*/.anjawiki/agents/`), con campo `workspace` per
  distinguerli. Workspace diversi possono avere agent omonimi (pod `dev`/`analyst`/…):
  sono agent diversi e compaiono tutti; il mirror-sessioni hub senza config non
  oscura più l'agent vero. Prima le sessioni hub (es. Telegram) vedevano solo gli
  hub-level e concludevano che i responsabili di workspace "non esistono".
- `agent.delegate`: risoluzione del target con lo stesso lookup cross-workspace
  della webapp (`_resolve_hub_agent_dir`) — delegare al responsabile di un
  workspace ora funziona dalle sessioni hub.

## v0.19.5 — 2026-07-23

**Sandbox workspace dual-layout: i tool `workspace.*` vedono il layout post-hoist di AnjaHub.**

### Fixed

- `_validate_workspace_path`: per gli scope workspace, `files/`, `data/`, `scripts/` e i
  file root (`CLAUDE.md`, `log.md`, `meta.yaml`) si risolvono ora alla **radice del
  workspace** (layout AnjaHub post-hoist, es. `<ws>/data/PIANO.md`) con **fallback
  automatico su `.anjawiki/`** per i workspace legacy pre-hoist. Prima la sandbox
  puntava solo a `.anjawiki/` e gli agenti delegati non vedevano i dati operativi
  del workspace (PIANO.md, catalogo, media) pur essendo in whitelist.
- `wiki/**` resta sempre risolto in `.anjawiki/wiki` e lo scope `hub` è invariato;
  path-traversal e whitelist immutati (8 casi di regressione verificati).

## v0.19.4 — 2026-07-12

**Codex lifecycle hook installabile e compatibile con il runtime reale.**

### Fixed

- Il manifest Codex non dichiara piu hook non supportati: Codex li scopre esclusivamente dalla configurazione del progetto e non dal plugin.
- Il journal Codex usa l'evento supportato `Stop` al posto di `SessionEnd`; se il payload non espone il session ID, l'adapter trova il rollout piu recente della stessa workspace.
- I journal Codex sono identificati come `cli-codex`; Claude Code conserva `cli-claude` come default invariato.

### Added

- **`scripts/install_codex_hooks.py`**: installazione idempotente per progetto di `SessionStart` e `Stop` in `.codex/hooks.json`, con preservazione degli hook esistenti e attivazione di `[features].hooks`.
- **`tests/test_install_codex_hooks.py`**: copre merge, preservazione e idempotenza dell'installer.

## v0.19.3 — 2026-07-12

**Codex lifecycle adapter**: journal e re-embed Codex ora passano da un adapter dedicato, senza modificare il percorso Claude Code.

### Added

- **`hooks/codex_adapter.py`**: normalizza i rollout JSONL locali di Codex nel contratto transcript consumato dal core Anja, salva il transcript convertito in `.anjawiki/transcripts/codex/` e genera il journal con prompt utente + tool stats. Il percorso Codex non avvia auto-summary o sync `cc_memory`, entrambi specifici di Claude Code.
- **`tests/test_codex_adapter.py`**: fixture Codex → journal che copre user prompts, function call e normalizzazione PostToolUse.

### Changed

- **`hooks/hooks.codex.json`**: `SessionEnd` e `PostToolUse` invocano l'adapter Codex; `SessionStart` e tutti gli hook Claude Code restano invariati.

## v0.19.2 — 2026-06-30

**Security**: YAML injection in `task.schedule_one_shot`.

### Fixed

- **`task.schedule_one_shot`** (`mcp_memory_server.py`): gli `output_actions` (il campo `type` e i valori) erano serializzati nel routine YAML con f-string non quotate → un valore contenente `"` o newline poteva iniettare chiavi YAML arbitrarie (es. forzare `enabled`/output non voluti). Ora i valori passano da `json.dumps` (scalare JSON valido anche come YAML, con escape corretto di quote/newline) e le chiavi sono validate come identificatori. Il `prompt` (blocco `|`) e il `name` (già validato kebab-case) non erano interessati.

## v0.19.1 — 2026-06-13

**OpenCode adapter validato sul campo** (OpenCode 1.17.4) + fix stdin.

### Fixed

- **`.opencode/plugin/anja.js`**: lo stdin verso gli hook Python passava da un metodo `.stdin()` inesistente sulla Bun shell → `TypeError` (journal e re-embed non partivano). Ora via `child_process.spawn` (stdin standard). **Validato e2e su OpenCode 1.17.4**: loading + hooks risolti, context injection (`chat.message`→`output.parts`, mutazione confermata), re-embed (`tool.execute.after`→`post_tool_use.py`), journal (`session.idle`→`session_end.py` con summary haiku). Campi runtime confermati: `event.properties.sessionID`, tool `write`/`edit` con `args.filePath`.

### Added

- Debug opt-in `ANJA_OC_DEBUG=1` → log diagnostico in `/tmp/anja-opencode.log`.

## v0.19.0 — 2026-06-13

**OpenCode full mode** (`F-OpenCodeAdapter`): plugin che aggancia i lifecycle di OpenCode agli hook Python anja, replicando gli automatismi senza riscriverne la logica.

### Added

- **`.opencode/plugin/anja.js`**: plugin OpenCode (Bun) — `event(session.idle)`→`session_end.py` (journal di sessione, debounced 60s), `tool.execute.after`→`post_tool_use.py` (re-embed del wiki dopo edit di `.anjawiki/wiki`), `chat.message`→`session_start.py` (context injection best-effort, 1×/sessione). Il plugin TRADUCE i messaggi OpenCode (via `client.session.messages`) nel JSONL stile Claude Code che `parse_transcript` già legge, poi invoca gli script Python **invariati** → zero modifiche al codice condiviso, zero regressioni su CC/Codex/Grok.
- **`tests/test_opencode_adapter.py`**: verifica il contratto di integrazione (transcript tradotto → `session_end.py` → journal con user prompts + tool stats, 3/3).

### Note

- I campi esatti dell'API OpenCode (event payload, struttura `Part`, args dei tool) sono accedibili in modo difensivo (più fallback) — da validare sul campo quando OpenCode entra nel workflow. Il contratto verso il Python è testato.

## v0.18.2 — 2026-06-12

**Security** (round 2): prompt-injection hardening sul subprocess di summarize + least-privilege sull'agent delegato.

### Fixed

- **`sessions.summarize`** (`summarize_session_bg.py`): il session content (prompt utente + materiale ingerito, potenzialmente non fidato) era interpolato nel prompt di `claude -p` con delimitatore `---` → prompt injection nel subprocess. Ora racchiuso in tag XML `<session_file>` con istruzione esplicita "dati, non istruzioni" + neutralizzazione del tag-breakout.
- **`agent.delegate`** (`mcp_memory_server.py`): `tool_agent_delegate` hardcodava `permission_mode: bypassPermissions` per ogni agent delegato → con prompt injection l'agente girava senza guardrail. Ora least-privilege di default: `allowed_tools` SEMPRE ristretta (Read/Grep/Glob + i soli MCP dell'agent), `bypassPermissions` solo con opt-in `bypass_permissions: true` nella config dell'agent.

## v0.18.1 — 2026-06-11

**Security**: hardening dei tool MCP che costruivano path da argomenti del caller senza confinamento (un LLM influenzato da contenuto esterno poteva indurre lettura/scrittura di file arbitrari).

### Fixed

- **`sessions.read`** (`mcp_memory_server.py`): il branch `path` ora è confinato a `sessions_root` (era `ROOT / path_arg` → `../../.secrets.env` o un path assoluto leggevano file arbitrari e ne restituivano il content). Era l'unico read-tool che saltava il pattern `relative_to` già usato da skill/workspace.
- **`wiki.export`**: `output_path` confinato a `ROOT` (esportava il wiki — prompt utente, SOUL, frontmatter — ovunque sul disco).
- **`wiki.attach_image`**: `topic` sanitizzato + confinato a `raw/`, `filename` via basename, cap 25MB sul download da URL.
- **`memory.write`**: `category` sanitizzata `[a-z0-9_-]` + confinata a `raw/`.

### Added

- **`bump.sh`**: script di release single-source-of-truth — aggiorna in un colpo le 4 occorrenze di `version` nei 3 manifest (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`), che prima andavano in deriva (causa del "già aggiornato" dopo un fix senza bump).

## v0.18.0 — 2026-06-11

**Feature**: `/anja-upgrade` — migrazione guidata di progetti/hub con wiki di versione precedente.

### Added

- **`/anja-upgrade`** (`commands/anja-upgrade.md`): slash command che wrappa `upgrade_triade.py` con workflow guidato — diagnosi stato (kind, schema-version, triade/composed/MCP mancanti), dry-run, conferma AskUserQuestion, esecuzione, opzionale `--memory` per migrare la CC memory in SOUL.md via `migrate_cc_memory.py` (orchestrato `--dry-run` + conferma + `--yes`, lo script da solo chiederebbe `input()` interattivo). Non-distruttivo: aggiunge solo ciò che manca.

### Fixed

- `upgrade_triade.py` ora backfilla anche `.anjawiki/.schema-version` nei progetti migrati (prima lo scriveva solo `init_project.py` → i progetti upgradati restavano senza version per il gate migration).

### Changed

- `/anja-config` edge case `.mcp.json` mancante: la suggestion ora punta a `/anja-upgrade` per wiki esistenti (prima suggeriva lo script raw).
- README: riga `/anja-upgrade` nella tabella comandi + nota post-`/plugin update`.

## v0.17.1 — 2026-06-06

### Fixed

- **Collisione `.mcp.json` CC ↔ Codex**: il file MCP del plugin Codex (con `${PLUGIN_ROOT}`) veniva auto-scoperto anche da Claude Code (che usa `${CLAUDE_PLUGIN_ROOT}`) → errori "Missing environment variables: PLUGIN_ROOT" al reload. Rinominato `.mcp.json` → `.mcp.codex.json` (referenziato esplicitamente dal manifest Codex; CC non lo auto-scopre più — su CC gli MCP restano scaffoldati nei progetti, come sempre).

## v0.17.0 — 2026-06-06

**Codex plugin**: anjadev distribuibile come plugin Codex nativo (esperienza piena, non solo MCP).

### Added

- `.codex-plugin/plugin.json` — manifest plugin Codex (punta a `./skills/`, `./.mcp.json`, `./hooks/hooks.codex.json`).
- `.mcp.json` — definizione MCP server (`anja_memory`/`anja_code`) per il plugin (`${PLUGIN_ROOT}`).
- `hooks/hooks.codex.json` — hook lifecycle (`SessionStart`/`SessionEnd`/`PostToolUse`) nel formato Codex (`${PLUGIN_ROOT}`); riusano gli stessi script Python di CC.
- `.agents/plugins/marketplace.json` — entry marketplace per `codex plugin marketplace add`.
- README — install come plugin Codex.

La struttura del plugin Codex combacia con quella di CC (`.codex-plugin/plugin.json` ↔ `.claude-plugin/plugin.json`, stesso `skills/` + `hooks.json`): riuso ~totale. Nota: il parser del journal (`session_end`) è tarato sul transcript CC e potrebbe richiedere un adattamento al formato Codex (degrada gracefully; gli altri hook funzionano a prescindere).

## v0.16.1 — 2026-06-06

**Cross-harness**: supporto Gemini CLI.

### Added

- `compose_claude_md.py` genera anche `GEMINI.md` (symlink → `AGENTS.md`) per Gemini CLI (che legge `GEMINI.md`, non `AGENTS.md`).
- README — sezione cross-harness estesa con **Gemini** (`~/.gemini/settings.json` mcpServers + `GEMINI.md` + opzione *extension*). Nota hook aggiornata dopo verifica: **Grok CLI ha hook CC-compatibili** (gli automatismi anja reggono), Codex/Gemini "modo manuale", OpenCode parcheggiato.

## v0.16.0 — 2026-06-06

**Cross-harness**: portabilità verso Codex / Grok / OpenCode (F-MCP-CrossHarness + F-NonCC-ManualMode, lato plugin).

### Changed

- **Modello context unificato**: `compose_claude_md.py` ora genera `AGENTS.md` (composed *inline*, standard cross-tool letto nativo da Codex/Grok/OpenCode) + `CLAUDE.md` = wrapper `@AGENTS.md` (per Claude Code). Il source editabile è `AGENTS.src.md`. **Migrazione idempotente** automatica (`AGENTS.md`→`AGENTS.src.md` al primo run). Rimossi i residui `@SOUL.md`/`@TOOLS.md` dal composed (gli harness non-CC non espandono `@import`).
- Scaffold allineato a `AGENTS.src.md`: `init_project.py`, `upgrade_triade.py`, template `triade-skeleton/`.

### Added

- **README — sezione "Cross-harness setup"**: config MCP pronte per Codex (`config.toml` / `codex mcp add`), Grok (`.grok/settings.json`), OpenCode (`opencode.json`). I server core (`mcp_memory_server` / `mcp_code_server`) sono MCP stdio standard → girano su qualunque host MCP (verificato via handshake `initialize` + `tools/list`).
- **Sezione Bootstrap** nel context composto: per harness senza hook anja, guida il pull di contesto manuale (`roadmap.list` / `memory.timeline`) + journal bash-native.

## v0.15.0 — 2026-06-01

**Feature batch**: Memory 2.0 — journal lossless + dedup semantico + accesso bash-native.

### Added

- **`wiki.find_duplicates`**: trova coppie di pagine wiki semanticamente troppo simili (candidati duplicati / da fondere o contraddittorie) via embeddings condivisi (`code-index.db`). Vede ciò che il match esatto non vede (es. `auth-service` vs `authentication`). Args: `threshold` (default 0.85), `types`, `limit`.
- **Session journal lossless** (`session_end.py`): il session file ora salva `transcript_path` nel frontmatter (recovery pointer ai turni originali, che CC conserva) + **TUTTI** i user prompt (prima troncati a primi-5 + ultimi-3) + sezione "Transcript (drill-down lossless)". Niente archivio separato → zero gonfiamento git, nessun rischio di committare chat sensibili.
- **Accesso bash-native** documentato (template `project-skeleton/CLAUDE.md` + `triade-skeleton/AGENTS.md`): il wiki è file `.md` → `grep`/`find`/`cat` come fallback universale per harness senza MCP. Portabilità "gratis" (spunto Mirage filesystem-as-interface).

## v0.14.0 — 2026-06-01

**Feature**: `wiki.search` ibrida (Memory 2.0 — hybrid retrieval).

### Added

- **`wiki.search` ora è IBRIDA di default**: fonde il canale keyword (grep+rank) e quello vettoriale (sqlite-vec, embedding condiviso wiki↔code) via **Reciprocal Rank Fusion** (RRF). Robusta sia su query esatte (nomi/comandi → keyword) sia concettuali (riformulazioni → vector): su query concettuali il keyword da solo restituiva risultati irrilevanti, l'hybrid premia i documenti trovati da entrambi i canali (consensus → top rank).
- Param nuovi su `wiki.search`: `mode` (`hybrid`|`keyword`|`vector`, default `hybrid`), `include_sessions` (default false).
- Helper `_rrf_fuse` (fusione per rango — ignora score non comparabili BM25/cosine) + `_wiki_vector_search` (embed query + k-NN `kind='wiki'`).
- Nuovo handler `wiki.search_keyword` per accesso esplicito al solo canale keyword.

### Changed

- `wiki.search` degrada con grazia a solo keyword se manca embed provider/index (campo `_note` nel risultato). `tool_wiki_search` (motore keyword) invariato → retro-compatibile.

## v0.13.5 — 2026-05-28

**Fix critico**: `session_end.py` spawnava `summarize_session_bg.py` detached, che invocava `claude -p ...` headless. La sub-sessione `claude -p` però è essa stessa una sessione Claude Code: al termine scattava di nuovo `SessionEnd` hook → nuovo session file → nuovo summary in background → loop infinito (~1 sessione fantasma ogni 10s, dir `.anjawiki/wiki/sessions/<date>/` allagata).

L'opt-out `ANJA_AUTO_SUMMARY=0` esisteva già (`session_end.py:377`) ma non veniva propagato al subprocess. Fix: `spawn_bg_summarize` ora passa `env=ANJA_AUTO_SUMMARY=0` + `ANJA_WIKI_EMBED=0` al child, così la sub-sessione `claude -p` non riarma il loop.

## v0.13.0 — 2026-05-23

**Feature**: F-SkillEvolution-B — Skill auto-improvement workflow (pattern Hermes "skills learn from usage").

### Added

- **PostToolUse hook** `hooks/skill_evolution.py`: traccia invocazioni di skill scripts via Bash. Append a `~/.anja/skill_evolution_inbox.jsonl` con dedup hash (60s window). Skip silenzioso se `ANJA_SKILL_EVOLUTION=0`.

- **Skill `evolve-skills`** + `scripts/evolve.py`: workflow review. Legge inbox, invoca Claude haiku per analizzare se ogni invocazione è memorabile (edge case, pattern, esempio utile) → propone patch SKILL.md → output in `~/.anja/skill_evolution_proposals.jsonl`. Marker incrementale per non re-processare.

- **Slash command `/anja-evolve-skills`**: triggera evolve workflow, mostra proposte memorable con diff, chiede conferma utente per ogni, applica via `skill.patch`. Modalità `--apply-all` per batch trusted.

- **2 nuovi tool MCP** in `mcp_memory_server`:
  - `skill.history(name)` — lista backup disponibili in `<skill>/.history/`
  - `skill.rollback(name, timestamp?)` — ripristina SKILL.md da backup (default: ultimo)

### Changed

- `skill.patch` ora crea automaticamente backup `<skill>/.history/<ts>.SKILL.md` prima della modifica (microsecond timestamp per evitare collisioni). Max 20 backup per skill (LRU). Backup recoverable via `skill.rollback`.

### Safety

- No auto-apply: ogni patch evolution richiede conferma utente esplicita
- Rollback reversibile: anche il rollback crea backup dello stato corrente
- Marker incrementale evita re-review delle stesse entry
- Tool group `skills` esteso con `skill.history` + `skill.rollback`

## v0.12.0 — 2026-05-23

**Refocus**: research skills migrate al plugin anja-hub (Personal AI Hub workflows). anjadev resta plugin puro "dev + memory + code search" per qualsiasi progetto.

### Removed (moved to anja-hub plugin)

- `skills/research-duckduckgo/` → ora in `anja-hub/skills/`
- `skills/research-serpapi/` → ora in `anja-hub/skills/`

**Razionale**: web research è un workflow user-facing del Personal AI Hub, non una capability "dev tooling" universale. Lo split coerente con la filosofia post-v0.10.0 (anjadev = strumenti dev/memory/code, anja-hub = Personal AI Hub UX).

Skills che restano in anjadev (wiki workflows generici): `ingest`, `init-analyze`, `lint`, `query`, `refresh`.

## v0.11.0 — 2026-05-23

**Feature**: Web research skills (Hermes-style — lazy load on-demand via `skill.load`, no MCP server resident, no token overhead).

### Added

- **`skills/research-duckduckgo/`** — Ricerca web tramite DuckDuckGo HTML scrape. Zero setup, no API key, privacy-friendly. Default per uso quotidiano. Output JSON `{query, count, results: [{title, url, snippet}]}`. Stdlib only (urllib + regex parser). Smoke verde su query reali.

- **`skills/research-serpapi/`** — Ricerca Google via SerpAPI ufficiale. Richiede `SERPAPI_KEY` env (free tier 100 req/mese). Drop-in compatible con DDG (stesso schema JSON). Errore esplicito se key mancante con istruzioni setup.

### Pattern

Le skill sono caricate on-demand via `skill.load(name)` quando l'agent rileva intent di ricerca web ("cerca info su X", "trova news", "google Y", ecc.). Output strutturato pronto da sintetizzare con citazioni `[title](url)` markdown.

Vantaggi del pattern skill vs MCP server dedicato:
- Token cost: ~500 token solo quando caricata, vs schema tool sempre nel context
- Niente subprocess permanente in memoria
- Distribuibile come markdown file in git, no setup utente
- Provider swap-able (DDG/SerpAPI/futuri arxiv/github) senza rebuild

### Integration (in AnjaHub plugin privato)

- Settings → Research tab: stato attivo skill, test button, preferred provider (ddg/serpapi/fallback)
- Anja hub system prompt: routing rules "cerca/trova/google" → invoca skill.load
- Endpoint `/api/settings/research` GET/POST + `/api/settings/research/test` per live verification

## v0.10.0 — 2026-05-23

**Breaking change**: focus del plugin ristretto a "advanced knowledge management + semantic code search per progetti dev/research". Tool MCP AnjaHub-specific e content-generation rimossi.

### Removed (migrated)

I 4 MCP server seguenti sono stati rimossi da anjadev. Vivono ora nel plugin privato `anja-hub` di AnjaHub:

- `mcp_office_ops.py` — 13 tool gestione hub (workspace.task, agent.update, script.lifecycle, routine.lifecycle, goal.assign_agent, ecc.). Rinominato `mcp_hub_ops.py` con prefix tool `office.*` → `hub.*`.
- `mcp_images_server.py` — image generation via OpenRouter/Sora
- `mcp_videos_server.py` — video generation async polling 13 modelli
- `mcp_office_server.py` — generate docx/xlsx/pptx via pandoc/libreoffice/marp

**Razionale**: questi tool sono workflow tipici di un Personal AI Assistant (gestione hub + content creation), non di un plugin general-purpose per progetti dev/research. Un dev che installa anjadev su un suo monorepo Go o React non vuole content gen né tool che assumono struttura AnjaHub. Filosofia coerente con il split OSS commerciale di 2026-05-18.

### Migration

Chi aveva installato anjadev <0.10.0 e usava i tool rimossi:

1. Installare anche il plugin `anja-hub` (privato, fa parte del repo AnjaHub)
2. Eseguire `python3 anja-hub/scripts/migrate_workspaces_mcp_paths.py <hub-path> --apply` per riscrivere i `.mcp.json` dei workspace esistenti dai path/key vecchi (`anjadev/scripts/mcp_office_ops.py` + `anja_office_ops`) ai nuovi (`anja-hub/scripts/mcp_hub_ops.py` + `anja_hub_ops`).

Vedi `anja-plan.md:F-PluginSplit` nel repo AnjaHub per spec completa della migrazione.

### Plugin size

- LOC: ~16k → ~9k (-45%)
- MCP server: 6 → 2 (`mcp_memory_server` + `mcp_code_server`)
- File: ~60 → ~30

## v0.9.x e precedenti

Vedi git log per dettagli pre-0.10. Versioni 0.x sono pre-release, breaking change possibili ai minor bump.
