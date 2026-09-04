# PIANO.md — piano d'azione anjadev

> Sintesi di due analisi indipendenti (Claude Fable, Codex) sul repo a v0.24.0, verificate sul codice il 2026-09-04.
> Ogni voce ha: file toccati, sforzo stimato, criterio di accettazione. Le caselle si spuntano man mano.
> Sforzo: **S** < 1h · **M** 1–4h · **L** 1–2 giorni.

## Obiettivo

Portare il plugin da "usable in production" a "verificabile in production": ogni numero nella documentazione è derivato dal codice, ogni tool del registry è testato almeno una volta sul wire, ogni release passa da una CI verde.

## Principi

1. **Il wire non cambia.** Nomi tool, gruppi, schema `.anjawiki/` (SCHEMA.md 1.1) restano identici. Ogni fase è invisibile all'agent che usa il plugin.
2. **Una fonte per ogni verità.** Versione, elenco tool, elenco comandi: un solo posto nel codice, il resto generato o validato.
3. **Fail loud dove non costa.** Gli hook restano best-effort, ma ogni `except Exception` che oggi tace deve almeno scrivere una riga su stderr.
4. **Refactor solo dietro test.** La divisione del monolite parte dopo che il registry ha un test di corrispondenza esatta.

---

## Stato verificato (fatti, non opinioni)

| # | Fatto | Dove |
|---|-------|------|
| F1 | README dichiara v0.23.0, plugin è 0.24.0 | `README.md:5` |
| F2 | Runtime: 56 tool in 9 gruppi. Documentazione: 27 / 81 / 82 tool, 15 gruppi | `scripts/mcp_memory_server.py:3819`, `README.md:229`, `README.md:307`, `.claude-plugin/marketplace.json` |
| F3 | Registry ha 57 definizioni: `wiki.find_duplicates` ha handler (`:1542`) e schema (`:4166`) ma **non è in nessun gruppo** → filtrato da `_allowed_tool_names`, irraggiungibile | `scripts/mcp_memory_server.py:3819-3860` |
| F4 | Gruppo `skills`: README dice 9, runtime 11 (`history`, `rollback`). Gruppo `wiki`: README dice 18, runtime 19 | `README.md:237,240` |
| F5 | Slash command: 12 reali, plugin.json dice 10, README dice 11 | `commands/`, `.claude-plugin/plugin.json`, `README.md:304` |
| F6 | Tre test non hanno funzioni `test_*`: pytest ne raccoglie 5 su 8 | `tests/test_codex_adapter.py`, `test_install_codex_hooks.py`, `test_opencode_adapter.py` |
| F7 | Quattro test preferiscono `/opt/homebrew/opt/python@3.12/bin/python3.12` hard-coded, PATH ristretto a path macOS | `tests/test_core_split.py:25,50`, `test_mcp_smoke.py:34,80`, `test_steward.py:28`, `test_compact_sessions.py:26` |
| F8 | Nessuna CI, nessun `pyproject.toml`, nessun linter, nessuna coverage | radice repo |
| F9 | 43 `except Exception` nel server, molti con `pass` | `scripts/mcp_memory_server.py` |
| F10 | `scripts/mcp_bybit_lite.py` (814 righe, trading Bybit) non è citato da nessun manifest, README o comando | `scripts/mcp_bybit_lite.py` |
| F11 | `_quick_loc_count` esegue `sh -c` con `project_root` interpolato **senza quoting**: un path con spazi o apici rompe il comando | `scripts/code_search.py:55-69` |
| F12 | `mcp_code_server`: `start_new_session=True` ma su timeout fa `proc.kill()` e non `os.killpg` → i figli del figlio sopravvivono | `scripts/mcp_code_server.py:162-169` |
| F13 | `mcp_code_server`: `proc.communicate()` legge tutto l'output in memoria e tronca **dopo** → il cap da 50KB non limita la RAM | `scripts/mcp_code_server.py:165-180` |
| F14 | `mcp_code_server`: workspace `mkdtemp(prefix="anja-code-")` mai rimosso, si cancella solo lo script | `scripts/mcp_code_server.py:73,200` |
| F15 | `anja_code` è montato di default per Codex (`.mcp.codex.json`), non per Claude Code (`init_project.py` registra solo `anja_memory`). Il livello di fiducia non è documentato | `.mcp.codex.json`, `scripts/init_project.py:124` |
| F16 | `bump.sh` allinea i 3 manifest ma non README né `SERVER_VERSION` (2.1.0 memory, 0.1.0 code) | `bump.sh`, `scripts/mcp_memory_server.py:58`, `scripts/mcp_code_server.py:27` |
| F17 | Tutti gli 8 test passano su Python 3.9.6 (grazie a `from __future__ import annotations`). README richiede 3.10+ | `README.md` prerequisiti |
| F18 | Smoke test copre 19 tool su 56 | `tests/test_mcp_smoke.py` |

Punti forti confermati (da preservare durante il refactor): contratto `.anjawiki/` versionato; filtro tool applicato sia a `tools/list` sia a `tools/call`; controlli path-traversal su sessions/wiki/skill; steward append-only con dry-run/propose e cap patch; journal policy testata; core stdlib con degradazione controllata; git ordinato con un commit per release.

---

## Fase 0 — Igiene immediata → release v0.24.1 ✅ (2026-09-04)

Tutto meccanico, nessun rischio, un pomeriggio. Sblocca le fasi successive.

- [x] **0.1** Allinea README: stato v0.24.0, 56 tool / 9 gruppi, gruppo wiki 19, gruppo skills 11, 12 slash command. Stessi numeri in `plugin.json` e `marketplace.json`. — **S** — `README.md`, `.claude-plugin/*.json`
  *Accettazione:* `grep -nE "8[12] tool|15 gruppi|v0\.23" README.md .claude-plugin/*.json` restituisce zero righe.
- [x] **0.2** Ripristina `wiki.find_duplicates` aggiungendolo a `TOOL_GROUPS["wiki"]` → **fatto nel gruppo `graph`** (dipende dall'index embedding come gli altri; 57 tool / 9 gruppi). Vedi D1. — **S** — `scripts/mcp_memory_server.py:3831`
  *Accettazione:* `tools/list` senza filtro restituisce esattamente le chiavi del registry handler.
- [x] **0.3** Aggiungi `def test_<nome>(): main()` ai tre test adapter. — **S** — `tests/test_codex_adapter.py`, `test_install_codex_hooks.py`, `test_opencode_adapter.py`
  *Accettazione:* `pytest tests -q` raccoglie 8 test.
- [x] **0.4** Rimuovi il path Homebrew hard-coded: usa `sys.executable` sempre, PATH di test = PATH corrente. Se serve un interprete diverso, leggi `ANJA_TEST_PYTHON`. — **S** — i 4 file di F7
  *Accettazione:* i test passano su Linux senza `/opt/homebrew`.
- [x] **0.5** Quota il path in `_quick_loc_count`: sostituisci `sh -c` con `subprocess.run([...find args...])` + conteggio righe in Python, oppure `shlex.quote(project_root)`. Preferita la prima (zero shell). — **S** — `scripts/code_search.py:55-69`
  *Accettazione:* test con un project root contenente spazio e apice restituisce LOC > 0.
- [x] **0.6** Sposta `mcp_bybit_lite.py` fuori dal repo (repo separato o `~/Documents/bybit-mcp-trading`). → **rimosso** (D2). — **S**
  *Accettazione:* `scripts/` contiene solo file citati da README, manifest, hook o import.
- [x] **0.7** `bump.sh`: aggiorna anche la riga "**Stato**" del README e `SERVER_VERSION` in entrambi i server (o rendi `SERVER_VERSION` = versione plugin letta da `plugin.json` a runtime). — **S** — `bump.sh`
  *Accettazione:* `./bump.sh 0.24.1` seguito da `grep -rn "0.24.1"` mostra README + 3 manifest + 2 server.
- [x] **0.8** CHANGELOG v0.24.1, commit, tag.

## Fase 1 — Registry verificabile → release v0.25.0 ✅ (2026-09-04)

Chiude la causa strutturale della deriva (F2–F5, F18). Prerequisito della Fase 4.

- [x] **1.1** Unica struttura `TOOLS: list[ToolSpec]` con `name, group, schema, handler`. `TOOL_GROUPS`, `TOOL_DEFS` e `HANDLERS` diventano derivati da essa (comprehension), non più tre liste scritte a mano. — **M** — `scripts/mcp_memory_server.py:3819-4860`
  *Accettazione:* diff del wire (`tools/list` prima/dopo, nomi flat e canonici) vuoto. Il test 1.2 passa.
- [x] **1.2** Test di corrispondenza: ogni spec ha handler callable, ogni handler è in una spec, ogni nome appartiene a un solo gruppo, il nome flat (`wiki_read`) è biiettivo col canonico (`wiki.read`), `tools/call` accetta entrambi. — **S** — `tests/test_registry.py` (nuovo)
- [x] **1.3** Smoke test parametrico: per ogni tool del registry, una chiamata con argomenti minimi validi su un wiki temporaneo → risposta JSON-RPC senza `error` di protocollo (un `error` applicativo controllato è accettato). — **M** — `tests/test_mcp_smoke.py`
  *Accettazione:* 56 (o 57) tool esercitati, non 19.
- [x] **1.4** Generatore `scripts/gen_tools_doc.py`: produce la sezione "MCP tools" del README dal registry, con conteggio per gruppo. Marker `<!-- tools:start -->` / `<!-- tools:end -->` nel README. — **M**
  *Accettazione:* `gen_tools_doc.py --check` esce 1 se il README è diverso dal generato (usato in CI, Fase 2).
- [x] **1.5** Check release `scripts/release_check.py`: versione uguale in README/manifest/server, conteggio tool e gruppi coerente, conteggio slash command = file in `commands/`, CHANGELOG ha la voce della versione corrente. `bump.sh` lo chiama alla fine. — **S**

## Fase 2 — Test e CI → dentro v0.25.0 ✅ (2026-09-04)

- [x] **2.1** `pyproject.toml` minimale: metadata, `[tool.pytest.ini_options] testpaths = ["tests"]`, `[tool.ruff]` con regole `E,F,I,B` e `line-length = 110`. Nessuna dipendenza runtime aggiunta. — **S**
- [x] **2.2** Ruff: prima corsa con `--fix` solo per import inutilizzati e ordinamento; le altre violazioni vanno in `extend-ignore` con un TODO datato, così la CI parte verde. — **M**
- [x] **2.3** GitHub Actions `.github/workflows/ci.yml`: matrice Python 3.9 / 3.10 / 3.12 × ubuntu / macos. Job: `ruff check`, `pytest -q`, `gen_tools_doc.py --check`, `release_check.py`. — **M**
  *Accettazione:* badge verde su `main`. Se 3.9 passa in matrice, il README abbassa il requisito a 3.9; altrimenti resta 3.10 e il codice smette di fingere.
- [x] **2.4** Coverage con `coverage.py` (stdlib-adiacente, nessuna dipendenza runtime): soglia iniziale = valore misurato meno 2 punti, alzata a ogni release. — **S**
- [x] **2.5** Test mancanti, in ordine di rischio: (a) `code_search` livello 1 ripgrep su un repo fixture; (b) `embed_providers` con provider mock che restituisce vettori deterministici → `wiki.embed`, `graph.semantic_neighbors`, `code.search` livello 3; (c) skill write-side (`save/patch/rollback/delete`) con verifica history; (d) `mcp_code_server` timeout, cap output, env scrubbing. — **L** — `tests/test_code_search.py`, `test_embed_mock.py`, `test_skills_write.py`, `test_code_server.py`

## Fase 3 — Sandbox `anja_code` → dentro v0.25.0 ✅ (2026-09-04)

Il tool `execute_python` è l'unico che esegue codice arbitrario. Ha già timeout, cap output, limite memoria e env scrubbing; mancano quattro cose.

- [x] **3.1** Kill dell'intero process group su timeout: `os.killpg(proc.pid, SIGKILL)` (già `start_new_session=True`), fallback `proc.kill()` su Windows. — **S** — `scripts/mcp_code_server.py:166-169`
  *Accettazione:* script che spawna `sleep 1000` in background → dopo timeout nessun processo orfano.
- [x] **3.2** Lettura output a streaming con cap: thread che legge stdout/stderr a chunk e si ferma al limite, poi kill. Rimpiazza `communicate()`. — **M** — `scripts/mcp_code_server.py:165`
  *Accettazione:* script che stampa 1GB non fa crescere la RSS del server oltre il cap + margine.
- [x] **3.3** Cleanup workspace: `shutil.rmtree(tmp, ignore_errors=True)` nel `finally` quando il cwd è stato creato da `mkdtemp`. — **S** — `scripts/mcp_code_server.py:73,200`
- [x] **3.4** Opt-in esplicito: `anja_code` esce da `.mcp.codex.json` di default e si abilita con `ANJA_CODE_EXEC=1` (il server rifiuta `tools/call` senza l'env, `tools/list` lo annota). Documentare in README il modello di fiducia: "esegue Python locale con i permessi dell'utente, sandbox best-effort, non è un confine di sicurezza". **Decidere** (D3). — **S** — `.mcp.codex.json`, `README.md`
- [x] **3.5** `SECURITY.md`: garanzie (path-traversal confinato a `.anjawiki/`, secrets mai in output, summarize con prompt-injection guard, delegate least-privilege), assunzioni (host fidato, `.secrets.env` gitignored), cosa NON è garantito (`anja_code`). Una pagina, così ogni nuovo tool si confronta con una lista. — **M**

## Fase 4 — Manutenibilità → release v0.26.0

Solo dopo Fase 1 e 2: il refactor deve avere test di regressione del wire già verdi.

- [ ] **4.1** Layout: `scripts/anja/` package con `server.py` (JSON-RPC, dispatch, filtro gruppi, ~300 righe), `registry.py` (ToolSpec + aggregazione), `tools/{memory,sessions,soul,user,skills,wiki,roadmap,code,graph}.py`. `scripts/mcp_memory_server.py` resta come entry point sottile per non rompere i `.mcp.json` esistenti. — **L**
  *Accettazione:* nessun file > 1000 righe; `tools/list` identico byte per byte; test 1.2 e 1.3 verdi; smoke test cross-harness (`test_core_split`) verde.
- [ ] **4.2** Logging diagnostico opt-in: `ANJA_LOG=debug` → ogni `except Exception` scrive `[anja_memory] <tool> <ExcType>: <msg>` su stderr (stderr è sicuro nel protocollo stdio). Default: solo errori non recuperabili. Nessun `pass` muto nel server; negli hook ammesso ma con commento che spiega perché. — **M** — tutti i 43 punti di F9
- [ ] **4.3** Classificazione componenti in README: **core** (server memory, hook, comandi, steward), **adapter** (codex, opencode, grok config), **sperimentale** (`anja_code`, `graph.html`), **legacy** (script migrazione CC memory). Ogni script in `scripts/` appartiene a una classe. — **S**
- [ ] **4.4** Steward osservabile: ogni run scrive `.anjawiki/.steward/runs/<timestamp>.json` con cluster, patch proposte/accettate/rifiutate e motivo. `/anja-steward --history` le mostra. — **M** — `scripts/steward.py`, `commands/anja-steward.md`

## Fase 5 — Distribuzione (opzionale, dopo v0.26)

- [ ] **5.1** `pyproject.toml` con entry point `anja-memory-server` e `anja-code-server`, pubblicabile su PyPI o installabile con `pipx install git+...`. Gli host non-Claude smettono di dipendere da symlink e `PLUGIN_ROOT`. Il plugin Claude Code resta un wrapper che punta al pacchetto. — **L**
  *Prerequisito:* Fase 4.1 (il package esiste già).

---

## Decisioni (prese il 2026-09-04)

| ID | Domanda | Opzioni | Blocca |
|----|---------|---------|--------|
| D1 | `wiki.find_duplicates`: ripristinare o rimuovere? | ✅ **Ripristinato nel gruppo `graph`.** È un dedup batch di tutto il wiki (coppie sopra soglia), non un doppione di `graph.semantic_neighbors` che è un k-NN per singola query. Non era mai stato in un gruppo dalla v0.15.0 | 0.2 |
| D2 | `mcp_bybit_lite.py`: repo separato o `experimental/`? | ✅ **Eliminato** (resta nella storia git a `5311913` se servisse) | 0.6 |
| D3 | `anja_code` opt-in con env, o resta montato per Codex? | ✅ **Opt-in** via env, con SECURITY.md | 3.4 |
| D4 | Requisito Python: dichiarare 3.9 (verificato) o tenere 3.10? | ✅ **3.9+** dichiarato: suite verde su 3.9 locale, matrice CI 3.9/3.10/3.12 la sorveglia | 2.3 |
| D5 | Layout Fase 4: package `scripts/anja/` o cartella piatta `scripts/tools_*.py`? | ✅ **Package** `scripts/anja/` | 4.1 |

## Mappa release

| Release | Fasi | Contenuto visibile all'utente |
|---------|------|-------------------------------|
| v0.24.1 ✅ | 0 | Doc corretta, `find_duplicates` risolto, test tutti raccolti, fix quoting LOC |
| v0.25.0 ✅ | 1 + 2 + 3 | Registry unico, README tools generato, CI, ruff, smoke su tutti i tool, sandbox `anja_code` chiusa, SECURITY.md, opt-in |
| v0.26.0 | 4 | Server diviso per dominio, logging diagnostico, steward con audit |
| v0.27.0 | 5 | Pacchetto installabile |

## Cosa NON fare

- Non cambiare i nomi tool o i gruppi durante il refactor: gli `.mcp.json` dei progetti utente e AnjaHub li assumono.
- Non aggiungere dipendenze runtime obbligatorie: il core stdlib è il motivo per cui gira su 4 host.
- Non far scrivere il wiki allo steward in automatico allo SessionStart: la scelta "propose, apply umano" è corretta e va difesa.
- Non fondere Fase 4 con Fase 1: refactor e cambio di registry insieme rendono impossibile capire chi ha rotto il wire.

## Note di esecuzione (2026-09-04)

- Coverage misurata localmente con sottoprocessi: la soglia in CI è la baseline meno 2 punti.
  La CI la misura su ubuntu/py3.12 con sqlite-vec installato, quindi include `test_embed_mock`.
- Scoperti e chiusi durante le fasi 2–3, non previsti dal piano: `anja_code` rifiutava lo scope
  `project` (F-split v0.21 mai adattato); `code.reindex --force` cancellava le pagine wiki
  dall'index condiviso.
- I test hard-coded sull'interprete Homebrew (F7) avevano una ragione: il Python di sistema macOS
  non carica estensioni sqlite. Ora è esplicito (`ANJA_TEST_PYTHON`) e il test salta con diagnosi.
- Fase 4 (divisione del monolite, logging opt-in, classificazione componenti, audit steward) e
  Fase 5 (pacchetto) restano da fare.
