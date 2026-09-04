# SECURITY.md — modello di fiducia di anja

> Cosa il plugin garantisce, cosa assume, cosa **non** garantisce. Ogni nuovo tool si confronta
> con questa lista (PIANO.md 3.5). Segnalazioni: apri una issue privata o scrivi all'autore nel
> manifest.

## Perimetro

- **Host fidato.** Il plugin gira sulla macchina dell'utente, con i suoi permessi, dentro un
  harness (Claude Code, Codex, OpenCode, Grok) che è a sua volta fidato. Non c'è multi-tenancy.
- **Ingresso non fidato.** Gli argomenti dei tool MCP arrivano dal modello: possono essere
  sbagliati o manipolati da contenuti che il modello ha letto (prompt injection). Le difese sotto
  esistono per questo.
- **Rete.** Solo i provider embedding (`openrouter`, `voyage`, `openai`) e i CLI del harness per
  summary/steward escono in rete. Il core MCP è stdio puro.

## Garanzie (verificate da test)

| Garanzia | Dove | Test |
|----------|------|------|
| Scritture e letture del wiki confinate a `.anjawiki/` (path traversal in `sessions.read`, `wiki.export`, `wiki.attach_image`, `memory.write`, skill files) | `mcp_memory_server.py` (`relative_to` dopo `resolve()`) | `test_mcp_smoke` (path con spazio), fix v0.18.1–0.18.2 |
| Un tool nascosto da `ANJA_TOOL_GROUPS` non è chiamabile, né col nome canonico né flat | `_allowed_tool_names` applicato a `tools/list` **e** `tools/call` | `test_registry` §5 |
| Registry coerente o il server non parte (nessun tool "fantasma" senza handler) | `_build_registry()` | `test_registry` §6 |
| `.anjawiki/.secrets.env` viene caricato nell'env del server e mai scritto in output | `secrets_loader.py` | — (revisione manuale) |
| Summary/steward: il testo delle sessioni è incapsulato come dato nel prompt (injection-wrap), output JSON validato; JSON rotto → zero patch | `summarize_session_bg.py`, `steward.py` | `test_steward`, `test_journal_policy` |
| Steward fail-closed: pagine esistenti solo in append, mai delete/rename/SOUL, max 3 patch, lock 30 min | `steward.py` | `test_steward` |
| `execute_python` (`anja_code`) è **opt-in**: senza `ANJA_CODE_EXEC=1` il server non espone né esegue nulla | `mcp_code_server.py` `_exec_enabled` | `test_code_server` §0 |
| `execute_python`: env ripulito da API key/token/secret, timeout con kill dell'intero process group, output limitato in streaming (RAM del server bounded), rlimit memoria, recursion guard, workspace `strict` temporaneo rimosso | `mcp_code_server.py` | `test_code_server` §2–§6 |

## Assunzioni

- `.anjawiki/.secrets.env` è gitignored da `/anja-init`: se lo committi, la chiave è pubblica.
- I CLI usati per summary/steward (`claude -p`, `codex exec`, `grok -p`) hanno le proprie
  policy: anja passa il testo delle sessioni a quel CLI, che può inviarlo al suo provider.
- Il provider embedding riceve il testo delle pagine wiki e dei chunk di codice indicizzati.
  Con `local` o `mock` nulla esce dalla macchina.

## Cosa NON è garantito

- **`execute_python` non è un confine di sicurezza.** Esegue Python con i permessi dell'utente:
  può leggere qualunque file leggibile dall'utente, aprire la rete, scrivere fuori dal progetto.
  Le misure (env scrub, timeout, cap output, rlimit) limitano incidenti e consumo di risorse,
  non un attaccante. Abilitalo solo in ambienti dove daresti al modello una shell.
- Nessuna cifratura a riposo del wiki: è markdown in chiaro nel repo del progetto.
- Il lock dello steward è un file con TTL: due macchine sullo stesso wiki condiviso possono
  collidere.

## Storico fix di sicurezza

- v0.18.1 — path traversal in `sessions.read` / `wiki.export` / `attach_image` / `memory.write`.
- v0.18.2 — injection-wrap nel summarize; delegate least-privilege (ora in AnjaHub).
- v0.19.2 — escape YAML in `task.schedule_one_shot` (ora in AnjaHub).
- v0.20.3 — la sessione delegata non eredita gli MCP user-level dell'host (ora in AnjaHub).
- v0.25.0 — `anja_code` opt-in, killpg su timeout, cap output in streaming, cleanup workspace;
  `code.reindex --force` non cancella più le pagine wiki dall'index; `_quick_loc_count` senza shell.
