# Changelog

All notable changes to the `anja` plugin.

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
