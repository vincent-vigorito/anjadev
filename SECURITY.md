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
| Lettura/verifica e writer wiki controllano i path risolti; symlink esterni esclusi dalle scansioni. Allegati locali possono essere importati da fuori, ma la destinazione resta nel raw; export confinato al root del progetto | `scripts/anja/common.py`, `wiki.py`, `wiki_maint.py`, `wiki_io.py` | `tests/test_hardening.py` |
| La ricerca MCP usa il root configurato; il reranker non eredita stdin del server | `scripts/anja/code.py`, `scripts/code_search.py` | `tests/test_hardening.py` |
| Envelope MCP errato non termina i server; notifiche valide senza risposta; `anja_code` resta opt-in | `scripts/anja/rpc.py`, entrambi i server | `tests/test_hardening.py`, `tests/test_code_server.py` |
| Un tool nascosto da `ANJA_TOOL_GROUPS` non è chiamabile, né col nome canonico né flat | `_allowed_tool_names` applicato a `tools/list` **e** `tools/call` | `test_registry` §5 |
| Registry coerente o il server non parte (nessun tool "fantasma" senza handler) | `_build_registry()` | `test_registry` §6 |
| `.anjawiki/.secrets.env` viene caricato nell'env del server e mai scritto in output | `secrets_loader.py` | — (revisione manuale) |
| Summary/steward: il testo delle sessioni è incapsulato come dato nel prompt (injection-wrap), output JSON validato; JSON rotto → zero patch | `summarize_session_bg.py`, `steward.py` | `test_steward`, `test_journal_policy` |
| Steward fail-closed: pagine esistenti solo in append, mai delete/rename/SOUL, max 3 patch, lock 30 min | `steward.py` | `test_steward` |
| `execute_python` (`anja_code`) è **opt-in**: senza `ANJA_CODE_EXEC=1` il server non espone né esegue nulla | `mcp_code_server.py` `_exec_enabled` | `test_code_server` §0 |
| `execute_python`: env ripulito da API key/token/secret, timeout con kill dell'intero process group, output limitato in streaming (RAM del server bounded), rlimit memoria, recursion guard, workspace `strict` temporaneo rimosso | `mcp_code_server.py` | `test_code_server` §2–§6 |

Le scritture Markdown di wiki, roadmap e steward usano controllo revisione e pubblicazione atomica per file (`scripts/anja/persistence.py`, regressioni `tests/test_persistence.py`). Per proteggere una modifica basata su una precedente lettura del client serve `expected_revision`; senza il parametro viene protetto soltanto il ciclo interno del tool. Lock e fsync sono verificati sul filesystem locale macOS; la matrice prevista è Linux/macOS. Operazioni su più file non hanno rollback globale in caso di crash.

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

I controlli di canonicalizzazione assumono un filesystem locale fidato: non impediscono race su symlink modificate da un altro processo ostile fra controllo e I/O. Non costituiscono una sandbox del sistema operativo.


Le verifiche wiki sono legate all'hash del contenuto attuale. Il tool MCP non può attribuire conferme `human:*`; l'operatore dispone della CLI locale `verify_page.py` con revisione attesa. Si tratta di attestazioni locali, non di firme o di un confine di sicurezza contro chi può scrivere i file del progetto. I job embedding verificano hash e fingerprint prima di pubblicare, con worker serializzato, timeout e retry finiti.

La policy Q5 (`.anjawiki/index-policy.json`) applica default, `.gitignore` nativo, `.anjaignore` e restrizioni esplicite agli invii del codice/wiki. Provider e modello remoto devono essere autorizzati; il reranker ha un'abilitazione separata. Preview e diagnostica non inizializzano provider, anche quando un modello sconosciuto richiederebbe un probe HTTP. I filtri sui nomi non riconoscono tutti i possibili segreti nel contenuto: la policy del progetto deve escludere i dati riservati pertinenti.

I backup verificano l'integrità dei file e il restore richiede una destinazione nuova; non sono firme né un backup completo di codice, raw e fonti esterne. L'archivio integrale dei transcript è opt-in e separato dal wiki. La retention è esplicita e non cancella i journal o le fonti dell'harness. In assenza del transcript, la diagnostica non dichiara recuperabile una conversazione integrale dal solo journal.
