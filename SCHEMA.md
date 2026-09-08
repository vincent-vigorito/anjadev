# `.anjawiki/` schema — wire format pubblico

> Specifica del layout e del formato dei file dentro `.anjawiki/`. Questo documento è il **contratto pubblico** che consumatori esterni (hub AnjaHub, tool di sync, IDE plugin, script di terze parti) possono assumere quando leggono o scrivono in un wiki anja.
>
> Per il **manuale operativo** (workflow ingest/query/refresh/lint pensato per LLM agent dentro CC), vedi `.anjawiki/CLAUDE.md` scaffoldato in ogni progetto.

## Versioning

- File `.anjawiki/.schema-version` contiene una stringa semver-like (es. `1.0`).
- **MAJOR** bump = rottura layout/frontmatter required/log format → consumatori devono fare migration.
- **MINOR** bump = aggiunte non-breaking (nuove sotto-cartelle ignorabili, nuovi frontmatter opzionali).
- Current: **1.2** (1.1: famiglia trust/lifecycle opzionale; 1.2: `config.json["sessions"]`,
  `meta.yaml` `last_compact`, ritenzione dell'archivio sessioni — tutto opzionale).

## Layout cartelle

```
<project-root>/
├── .anjawiki/
│   ├── .schema-version         ← versione schema (text, semver)
│   ├── .secrets.env            ← API keys (gitignored, mai committare)
│   ├── config.json             ← config plugin (memory budget, ecc.)
│   ├── meta.yaml               ← identità del progetto (token, name, type)
│   ├── CLAUDE.md               ← manuale operativo del wiki per LLM agent
│   ├── code-index.db           ← sqlite-vec store opzionale (gitignored)
│   ├── .steward.lock / .steward-last / .steward-pending.json   ← stato dello steward (ignorabili)
│   ├── .steward/runs/*.json    ← audit dei run dello steward (ignorabili, ritenzione 200)
│   ├── raw/                    ← fonti immutabili (mai modificate da agent)
│   │   └── <topic>/...
│   └── wiki/                   ← contenuto generato (owned by agent)
│       ├── index.md            ← SPECIAL: catalogo semantico
│       ├── log.md              ← SPECIAL: append-only eventi
│       ├── overview.md         ← SPECIAL: sintesi corrente
│       ├── roadmap.md          ← SPECIAL: task strutturati
│       ├── entities/<slug>.md
│       ├── concepts/<slug>.md
│       ├── sources/<slug>.md
│       ├── analysis/<slug>.md
│       └── sessions/YYYY-MM-DD/<HHMMSS-cli-claude-XXXX>.md
└── (SOUL.md, AGENTS.md, TOOLS.md, .mcp.json — fuori da .anjawiki/, triade CC)
```

## meta.yaml

YAML, identità del progetto. **Single source of truth** per token e tipo.

```yaml
token: anja_<uuid7-canonical>     # RFC 9562 UUIDv7, time-sortable
name: <project-name>
type: dev | personal | research | business | automation
created: YYYY-MM-DD
tags: [tag1, tag2]
```

`last_compact: "<ISO 8601>"` (opzionale, 1.2): scritto da `compact_sessions.py --apply`
(chiave top-level, anche nel layout annidato `project:`/`anja:`). `/anja-status` lo espone.

## config.json

JSON, config del plugin. Sezioni note: `memory` (budget del context) e, dalla 1.2,
`sessions` — ritenzione dei journal (giorni; assenti → default):

```json
"sessions": {
  "archive_short_after_days": 14,      // < 3 msg o < 5 min → archive
  "archive_distilled_after_days": 14,  // distilled dallo steward → archive
  "archive_worth_after_days": 30,      // worth mai distillata → archive (summary conservato)
  "purge_archive_after_days": 180,     // stub archiviati SENZA summary → cancellati
  "archive_max": 500                   // cap soft: oltre, via i più vecchi senza summary
}
```

Ciclo di vita di una sessione: `sessions/<data>/<id>.md` (attiva) → `sessions/archive/<data>/<id>.md`
(stub: frontmatter + `archived: true` + Summary + `transcript_path`) → cancellata solo se senza
summary e oltre `purge_archive_after_days` o oltre `archive_max`. Uno stub con summary non viene
mai cancellato dal plugin. Il compact gira a SessionStart ogni 24 h (budget 20 azioni, stato in
`.anjawiki/.compact-last`) e dentro lo steward.

## Frontmatter pagine wiki

YAML frontmatter delimitato da `---`. Campi **required** per ogni pagina:

```yaml
---
title: <stringa leggibile>
type: entity | concept | source | analysis | session | overview | index | log | roadmap
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

Campi **opzionali**:
- `sources: [slug-1, slug-2]` (pagine source di provenienza)
- `tags: [tag1, tag2]`
- `source_path: ../../raw/<topic>/<file>` (solo `type: source`)
- `subtype: codebase-snapshot` (solo source di code refresh)
- `git_sha: <sha>` + `analyzed_at: YYYY-MM-DDTHH:MM:SSZ` (snapshot)
- `transient: true` (analysis cancellabili, es. lint report)
- `question: "..."` (analysis che nasce da query)

### Trust e revisione dei contenuti

`generated` indica chi ha prodotto l'ultima modifica; `verified` conserva gli eventi di verifica. I nuovi eventi sono JSON inline nel frontmatter, con `by`, `at`, `content_revision` e `origin`. Le attestazioni legacy senza hash sono conservate come tali, senza attribuire retroattivamente una revisione.

`content_revision` è SHA-256 del corpo e del frontmatter, esclusi soltanto `verified`, `generated` e `updated` (campi di audit). Titolo, fonti, tag, tipo, stato, `stale_after` e gli altri metadati fanno parte del contenuto verificato. Anche modifiche di formattazione significative possono invalidare la verifica. Il path non entra nell'hash: un rename che conserva il testo conserva le verifiche; le pagine con backlink riscritti diventano stale.

`wiki.read`, `wiki.verify` e i due lint condividono il calcolo del trust corrente:

- `unverified`: nessun evento;
- `legacy`: soltanto eventi senza revisione;
- `stale`: eventi revisionati presenti, nessuno per il contenuto attuale;
- `machine-confirmed`: verifica automatica per il contenuto attuale;
- `human-reviewed`: conferma locale dell'operatore per il contenuto attuale.

Lo storico non viene cancellato dopo upsert, edit diretto o intervento dello steward. Il trust viene derivato dal file letto, anche quando manca un hook. `wiki.read` restituisce sia `revision` (intero file, per controllo di concorrenza) sia `content_revision` (contenuto verificato); entrambi sono calcolati prima del troncamento della risposta.

`wiki.verify` usa `process:anja` per default e rifiuta `human:*`. Per una review umana effettivamente svolta, l'operatore usa localmente:

```sh
python3 scripts/verify_page.py <pagina.md> --human <id> --expected-revision <revision-di-wiki.read>
```

La CLI richiede la revisione attesa e registra `origin: local-cli`. Non è un tool MCP e non autentica crittograficamente l'identità dichiarata: chi può modificare liberamente il Markdown resta nel confine di fiducia locale. Gli agenti non devono usarla per inventare conferme umane.

`status` (`draft`, `stable`, `deprecated`) e `stale_after` restano metadati del ciclo di vita. Il superamento di `stale_after` è un warning separato dalla validità della verifica rispetto alla revisione.

## Wikilinks

```
[[slug]]
[[slug|label custom]]
[[slug#section]]
[[slug#section|label custom]]
```

Regex parser: `\[\[([^\]|#\s]+)(#[^\]|]+)?(\|[^\]]+)?\]\]`. Slug = nome file senza `.md`, in kebab-case.

## Slug naming

| Tipo | Pattern | Esempio |
|---|---|---|
| entity | kebab-case nome | `auth-service` |
| concept | kebab-case nome | `event-driven-architecture` |
| source | `YYYY-MM-DD-<slug>` o `codebase-snapshot-YYYY-MM-DD` | `2026-05-18-paper-x` |
| analysis | kebab-case tema | `auth-comparison` |
| session | `HHMMSS-cli-claude-XXXX` dentro `sessions/YYYY-MM-DD/` | `194849-cli-claude-d9e6` |
| roadmap | (special, file unico) | `roadmap.md` |

## Log format (strict)

`wiki/log.md` è append-only. Parser regex: `^## \[(\d{4}-\d{2}-\d{2})\] ([\w-]+) \| (.+)$`.

```
## [YYYY-MM-DD] tipo | descrizione breve in una riga
```

Tipi convenzionali: `init`, `init-analyze`, `ingest`, `query`, `refresh`, `lint`, `session`, `decision`, `milestone`, `note`. Custom kebab-case ammessi.

## Roadmap format

`wiki/roadmap.md` mantiene le sezioni `## Open`, `## Done`, `## Blocked`. Il sotto-formato è versionato nel frontmatter con `roadmap_schema: 2`, indipendente dalla versione del layout `.anjawiki/`. Gli ID sono salvati in ogni riga:

```markdown
---
title: Roadmap
roadmap_schema: 2
---
## Open
- [ ] (P1) Fix login | id: task-0123456789abcdef0123456789abcdef | owner: vincent | added: 2026-09-06
## Done
## Blocked
```

Parser canonico: `scripts/roadmap_io.py`. Le chiavi riconosciute per separare titolo e metadata sono `id`, `est`, `owner`, `added`, `started`, `done`, `took`, `blocker`; altre chiavi presenti dopo il separatore sono conservate. Il titolo può contenere `|` se non introduce una chiave metadata. I writer rifiutano newline nei campi e delimitatori metadata nel titolo. Gli ID persistenti non dipendono dal titolo e sopravvivono a spostamento, rinomina e archivio. ID duplicati nel documento impediscono la scrittura.

### Migrazione e compatibilità

La lettura dei file legacy non modifica nulla. Espone ID opachi provvisori deterministici sullo snapshot: se il file legacy viene modificato esternamente prima della migrazione, bisogna rileggerlo. Il primo aggiornamento persiste gli ID di tutti i task e `roadmap_schema: 2` e crea un backup esatto accanto alla roadmap: `.roadmap.md.pre-v2-<sha256>.bak`. La migrazione è ripetibile; un backup esistente deve coincidere col contenuto originale.

`legacy_task_ids` nel frontmatter conserva una mappa JSON degli alias precedenti. Un alias univoco continua a funzionare; uno ambiguo restituisce `ambiguous_task_id` con gli ID candidati. La mappa conserva anche i riferimenti ai task archiviati, così un alias non viene assegnato a un nuovo task. I nuovi task richiedono il proprio ID persistente, restituito da `roadmap.add`.

Un writer precedente a questo formato potrebbe eliminare gli ID: non deve modificare roadmap v2. Per tornare al formato precedente, recuperare il backup su una copia e riconciliare gli aggiornamenti successivi; il backup è lo snapshot pre-migrazione, non contiene le modifiche posteriori. I lettori Markdown restano utilizzabili.

La migrazione da v0.30.0 non è una transazione globale: `upgrade_triade.py` aggiorna
il layout e i file di integrazione; il primo aggiornamento roadmap persiste gli ID;
il primo refresh completo ricostruisce l'indice legacy con il fingerprint corrente,
includendo codice e wiki. Le verifiche wiki senza revisione restano `legacy`, anche
se dichiaravano un verificatore umano. L'upgrade da solo non certifica l'indice
come aggiornato e non rinnova le verifiche dei contenuti.

Regressione `tests/test_release_migration.py`: fixture con layout/schema SQLite di
v0.30.0 (`e5604f1`), titoli task duplicati, verifica YAML legacy e vettori esistenti.
Interruzioni di processo durante upgrade, prima/dopo la pubblicazione roadmap,
durante la transazione dell'indice e durante restore; riesecuzione completa e
conservazione degli ID controllate. Il restore verso una nuova directory conserva
i file inclusi byte per byte; una destinazione parziale non viene sovrascritta.

Il downgrade operativo richiede una copia dello snapshot **pre-migrazione**, la
reintegrazione separata del codice e degli altri dati esclusi dal backup, la
rigenerazione dei file host con la versione precedente e la ricostruzione
dell'indice con quella versione. Non è supportato alternare writer v0.30.0 e nuovi
writer sul progetto migrato o riutilizzare l'indice migrato con il vecchio plugin.

### Revisioni e scritture concorrenti

`wiki.read` e `roadmap.list` restituiscono `revision` (`sha256:<hash>` dell’intero testo UTF-8, incluse le terminazioni di riga). La revisione di `wiki.read` si riferisce al documento completo anche quando `content` è troncato. I writer di pagine e roadmap accettano `expected_revision`; `missing` richiede che il documento non esista. Una revisione superata produce `code: revision_conflict` con revisione attesa e corrente, senza sovrascrivere il contenuto concorrente.

Le chiamate senza `expected_revision` restano compatibili: il server protegge il proprio ciclo lettura/modifica/salvataggio, ma non può rilevare che il client aveva letto una revisione precedente prima della chiamata. Per proteggere quel caso, inviare la revisione ricevuta. `wiki.replace_links` espone revisioni nella preview e accetta una mappa `expected_revisions` per tutte le pagine modificate.

I writer condividono lockfile laterali `.nome-file.lock` (da non cancellare durante l’uso) e pubblicazione con file temporaneo, fsync e replace. I lock sono rilasciati dal sistema operativo anche dopo il crash del processo. La creazione delle note è esclusiva e non rende visibile un file parzialmente scritto.

L’atomicità è per file, non una transazione filesystem globale. Le operazioni su più pagine controllano tutte le revisioni sotto lock prima di scrivere. Un’interruzione I/O può lasciare risultati parziali: durante un rename possono restare entrambi i nomi, da verificare prima del recupero; nell’archiviazione si pubblica prima la copia in archivio e il retry deduplica per ID. I lock coordinano i writer che usano questi helper, non editor esterni o filesystem remoti con semantiche diverse.

## File speciali al root di wiki/

Esenti da check di "orphan" (sono entry-point, non linkati da altre pagine):

- `index.md` — catalogo navigabile per umani
- `log.md` — episodi cronologici append-only
- `overview.md` — sintesi tesi corrente del progetto
- `roadmap.md` — task strutturati

## Garanzie per consumatori esterni

Un consumatore esterno (es. AnjaHub) può assumere quanto segue per schema-version `1.0`:

1. **Path layout immutabile**: `wiki/{entities,concepts,sources,analysis,sessions}/` esistono o sono creabili.
2. **Frontmatter required** sempre presente sulle pagine generate dal plugin.
3. **Wikilinks `[[slug]]`** sono path-relative-free: lo slug è univoco a livello di wiki, no namespace.
4. **Log parsing** via regex sopra è stabile.
5. **Slug convention** vale per tutte le pagine generate dal plugin (utenti potrebbero violarla).
6. **Encoding**: UTF-8, line ending `\n`.
7. **Time zone**: date in formato ISO 8601 local (`YYYY-MM-DD`); timestamp full opzionali in ISO 8601 con `Z` per UTC.

## Cambiamenti dalla versione precedente

- **1.0** (2026-05-18): prima versione formalizzata. Estrazione dal manuale operativo `.anjawiki/CLAUDE.md`.


### Pubblicazione dell'indice codice/wiki (pipeline 3)

`code-index.db` conserva `indexed_files(kind, file_path, snapshot)` e `index_runs(id, kind, status, started, finished, pid, error, details)`. Lo snapshot contiene hash, dimensione, mtime e motivo di esclusione. Chunk, vettori, manifesto e metadati di completamento sono pubblicati nella stessa transazione; i tentativi falliti restano diagnosticabili senza cancellare l'ultimo indice valido. Lo staging `.index-stage-*` è temporaneo e ricostruibile.

La pipeline 3 corregge i riferimenti oltre EOF per file terminati da newline. Il passaggio dalla pipeline 2 richiede rebuild completo; fino al rebuild la ricerca vettoriale usa il fallback dichiarato.

`index_fingerprint` è JSON con provider, modello, dimensione, metrica e versione pipeline. Fingerprint assente o diverso richiede rebuild completo coordinato codice/wiki; una query non può accettare vettori di un altro modello soltanto perché hanno la stessa dimensione. Un rebuild fallito conserva i dati precedenti. I lettori del plugin precedente non applicano questo contratto: evitare writer di versioni diverse sullo stesso indice.

`code.status` restituisce `scopes.code` e `scopes.wiki`, ciascuno con stato, `last_success`, `changed_files` e `last_attempt`. Stati operativi: `ready`, `stale`, `building`, `partial`, `failed`; diagnostica aggiuntiva `missing`, `incompatible`, `provider_unavailable`. La scansione verifica i contenuti attuali. Un job limitato/single non avanza il checkpoint globale di completamento; una migrazione non ammette `limit`. `code.search` dichiara il fallback lessicale con `_fallback_reason` quando la ricerca vettoriale non è utilizzabile.


### Coda degli embedding wiki

`.anjawiki/wiki-jobs.db` conserva path, hash dello snapshot, fingerprint embedding, stato, numero di tentativi, scadenza lease, prossimo tentativo, ultimo errore e timestamp. Stati: `pending`, `running`, `retry`, `done`, `failed`, `superseded`. Lo stesso snapshot/fingerprint già accodato o concluso non viene duplicato; il ritorno A→B→A genera un nuovo job. Nuove revisioni rendono superseded i job precedenti non ancora avviati.

Un flock per progetto limita a uno il worker attivo e viene ereditato dal processo che esegue il job. Il figlio completa lo stato e risveglia la coda anche se muore il coordinatore. Ogni tentativo ha un limite di 300 secondi, imposto anche nel figlio; massimo tre tentativi, backoff di 2 e 4 secondi. Dopo un crash, un nuovo worker recupera i job running soltanto possedendo il lock. Non si ruba un lease a un processo ancora attivo. Il server MCP riavvia la coda esistente all'avvio; non serve un servizio esterno.

I writer wiki e gli hook Write/Edit/session-end alimentano la stessa coda. `ANJA_WIKI_EMBED=0` continua a disabilitare l'automatismo, compreso l'opt-out dello steward. Refresh espliciti codice/wiki restano sincroni e protetti dal protocollo di pubblicazione Q3. Snapshot e fingerprint del job vengono verificati prima dell'embedding e lo snapshot viene ricontrollato prima del commit; un risultato vecchio non sostituisce quello nuovo.

`code.status.wiki_jobs` espone conteggi e gli ultimi 20 job. Per diagnosi, ripresa manuale o un nuovo ciclo di retry dopo aver risolto la causa:

```sh
python3 scripts/wiki_jobs.py <progetto> --status
python3 scripts/wiki_jobs.py <progetto> --worker
python3 scripts/wiki_jobs.py <progetto> --retry-failed
```

Un retry esplicito crea un nuovo job e conserva il fallimento precedente. Le credenziali restano nella configurazione del provider; la coda conserva fingerprint e hash, non copie del contenuto. La retention della coda e dello staging è prevista in Q5.

### Policy dei dati e anteprima

La policy facoltativa è `.anjawiki/index-policy.json`. Sono ammessi soltanto `include`, `exclude`, `remote` e `rerank_model`; errori di sintassi, chiavi sconosciute e path che escono dal progetto bloccano l'indicizzazione.

Ordine delle restrizioni (nessun livello può riabilitare un file escluso da un altro):

1. Default: directory nascoste/build/dependency, repository annidati e symlink esclusi; estensioni supportate dal chunker; nomi di credenziali/segreti, chiavi, database, file `*.local.*` e transcript grezzi esclusi. Per il wiki la directory `.anjawiki/wiki` è il punto di partenza; le sessioni richiedono opt-in.
2. `exclude` nega path letterali relativi alla root, file o directory; `include`, se non vuoto, restringe a quei path. Non sono pattern glob e non accettano `..` o path assoluti.
3. `.gitignore` usa Git nativo, incluse regole annidate e file già tracciati (`check-ignore --no-index`). In un repository valgono anche le esclusioni native info/globali. Fuori da un repository viene usato un contesto Git temporaneo per le regole locali.
4. `.anjaignore` usa sintassi Git nativa, incluse negazioni, come un secondo insieme indipendente di esclusioni relativo alla root. Le negazioni operano dentro il proprio insieme; non superano i divieti degli altri livelli.

Senza Git, la presenza di ignore da applicare produce un errore, non un invio più permissivo. I filtri sui nomi non sono uno scanner universale dei segreti nel contenuto: usare regole esplicite per i dati riservati del progetto.

Esempio di restrizione locale (non abilita invii):

```json
{"exclude": ["private/", "src/internal_credentials.py"]}
```

Per autorizzare un provider remoto già scelto dall'utente, `remote` deve contenere esattamente il provider e il modello configurati, ad esempio `{"provider":"openai","model":"text-embedding-3-small"}`. La sola presenza di una API key non abilita gli invii. Il reranker CLI ha un'autorizzazione indipendente, `rerank_model`, corrispondente al modello configurato; i suoi candidati rispettano le stesse esclusioni. Se manca l'autorizzazione, la ricerca degrada al livello lessicale con motivo esplicito. Non vengono modificate automaticamente le autorizzazioni dei progetti esistenti.

`code.reindex(dry_run=true)`, `wiki.embed(dry_run=true)` e `/anja-index-code --dry-run` restituiscono file eleggibili, dimensioni sorgente, hash, esclusioni con motivo, destinazione locale/remota e autorizzazione. Le directory escluse sono aggregate e hanno dimensione null. La lista è di eleggibilità: gli invariati possono non richiedere invio. Una migrazione di fingerprint include anche l'altro scope, codice/wiki. La preview non costruisce provider, non esegue probe di dimensione e non scrive l'indice; anche `code.status` evita costruttori con effetti di rete.

La policy viene ricontrollata prima dei batch. File diventati esclusi vengono rimossi dall'indice al successivo refresh riuscito. Restano i limiti di concorrenza del filesystem locale: non si può revocare un payload già inviato prima di una modifica della policy.

### Diagnostica e recupero

`code.status` include root, versione plugin, schema locale, versione pipeline, configurazione del provider senza credenziali, disponibilità delle capacità opzionali, compatibilità/età dell'indice, job e disponibilità dei transcript. Dimensione sconosciuta resta sconosciuta: la diagnostica non la risolve tramite rete. I nuovi errori hanno codici come `index_policy_error`, `provider_unavailable`, `invalid_index_options`, `unsafe_project_state`, `index_database_error`, `diagnostics_failed`, `recovery_failed` e `maintenance_failed`.

Prima dei rebuild per migrazione embedding e delle migrazioni roadmap/schema progetto viene creato uno snapshot in `.anjawiki/backups/`. Include wiki, roadmap, configurazione/policy locale, regole ignore pertinenti, sorgenti della triade presenti e manifesti/metadati dell'indice. I vettori sono ricostruibili. I file vengono copiati con checksum; un cambiamento durante il backup impedisce di pubblicare lo snapshot incompleto.

```sh
python3 scripts/project_recovery.py backup <progetto>
python3 scripts/project_recovery.py verify <backup>
python3 scripts/project_recovery.py restore <backup> <nuova-directory>
```

Il restore verifica prima tutti i checksum e richiede una destinazione inesistente, con parent già presente. Conserva i contenuti e gli ID; salva `restored-index-manifest.json` e richiede la ricostruzione dell'indice. Un'interruzione durante la pubblicazione del restore lascia la nuova destinazione parziale per ispezione, senza sovrascrivere il progetto originale. I checksum rilevano corruzione, non autenticano un backup ostile.

Lo snapshot non è un backup dell'intero progetto: codice applicativo, raw/allegati, credenziali, transcript esterni e copie integrali dei transcript restano fuori. I puntatori possono quindi risultare mancanti dopo il ripristino; la diagnostica lo segnala. I backup del wiki non sono rimossi dalla manutenzione automatica degli artefatti ricostruibili.

### Journal e archivio integrale opzionale

Restano invariate le regole `journal_policy` (sessioni umane/programmatiche, worth e summary). Il default conserva il journal e il riferimento alla fonte, senza copiare il transcript integrale. `sessions.read`, `sessions.list` e `code.status` distinguono `journal_only`, `external`, `archived`, `missing` e `archive_corrupt`; `recoverable` indica la disponibilità del transcript, non la presenza del journal.

`ANJA_ARCHIVE_TRANSCRIPTS=1` abilita, per i progetti, una copia integrale al SessionEnd in `.anjawiki/transcripts/<sha256>.jsonl`, fino a 100 MiB. Copia pubblicata atomicamente, checksum nel frontmatter e revisione attesa sul journal; una fonte mancante, troppo grande o cambiata durante la lettura viene segnalata senza dichiarare riuscita l'archiviazione. Il transcript rimane separato dai contenuti indicizzabili.

La retention è esplicita, con preview di default e finestra predefinita di 30 giorni:

```sh
python3 scripts/project_maintenance.py <progetto> --days 30
python3 scripts/project_maintenance.py <progetto> --days 30 --apply
```

Rimuove copie integrali con nome hash di proprietà dell'archivio, job terminali vecchi e staging abbandonati. Non elimina journal, job pending/running/retry, backup wiki o staging quando risulta un build attivo. I riferimenti storici ai transcript eliminati restano nel journal e diventano esplicitamente missing se non è più disponibile la fonte esterna. Non è una cancellazione della fonte gestita dall'harness, né una garanzia di recovery lossless.

### Evidenze restituite da code.search (Q6)

`code.search` mantiene i tre livelli e l'ordine dei candidati, verificando i riferimenti prima di rispondere. Ogni risultato contiene `evidence.source_revision` (SHA256 dei byte della sorgente), `revision_algorithm`, `spans` con righe 1-based inclusive, `freshness=verified_at_read`, `match=literal|candidate` e `preview_truncated`. Le righe e il testo lessicale devono coincidere con il file letto; per i vettori devono coincidere anche hash del manifesto e contenuto del chunk. Il manifesto viene letto nella stessa transazione SQLite dei vettori. File mancanti, symlink, riferimenti esterni, intervalli invalidi, contenuti cambiati e sorgenti oltre 500 KB vengono esclusi con motivo in `evidence.rejected` della risposta.

La verifica riguarda gli snapshot delle sorgenti restituite, non uno snapshot atomico del repository. Una modifica successiva alla lettura può rendere obsoleto l'hash: verificare la revisione prima di applicare modifiche. Un fallback lessicale può avere riferimenti correnti mentre `index_status=stale` continua a descrivere correttamente l'indice.

Il riepilogo `evidence.status` distingue `literal_evidence`, `candidates_only`, `no_evidence`. `evidence.abstain=true` significa che nessun intervallo restituito contiene la query letterale (confronto case-insensitive); non è una classificazione semantica calibrata. I candidati restano visibili. `abstain=false` attesta solo un'occorrenza testuale: non prova comportamento, copertura test, dipendenze o correttezza di una risposta. Parafrasi e query regex possono richiedere astensione anche quando esistono risultati utili. Le distanze vettoriali non diventano percentuali di confidenza.

`limit` accetta interi 1–50 (default 10). `max_preview_chars` accetta interi 0–100000 (default 12000) e limita la somma dei caratteri del testo delle anteprime, non metadati, serializzazione JSON o token. L'ordine è preservato: il budget viene consumato dai primi risultati; gli altri mantengono riferimenti anche con testo vuoto. Un riscontro letterale può trovarsi nell'intervallo completo ma fuori dalla preview troncata: leggere la sorgente alle righe indicate. Opzioni fuori intervallo falliscono prima della ricerca con `invalid_search_options`.

### Ispezione sorgente e relazioni strutturali: code.inspect

`code.inspect(path, symbol?, expected_revision?, max_chars=6000, max_items=100)` legge un file ammesso dalla policy codice del root del server. Python (`.py`, `.pyi`) è analizzato con AST dell'interprete corrente; per Markdown sono riconosciute solo righe esplicite `Implemented by: percorso.py:simbolo`. Non costruisce provider, non esegue il codice, non modifica indice o sorgenti. Directory nascoste, symlink e file esclusi dalla discovery codice restano esclusi; per leggere il wiki nascosto usare i tool wiki esistenti.

La risposta contiene path, SHA256 dei byte, `freshness=verified_at_read`, `scope_span`, estratto `source`, simboli, operazioni sintattiche e relazioni. Il campo `symbol` seleziona funzioni/classi tramite nome qualificato (es. `Client.send`) oppure assegnazioni semplici a nomi di modulo (es. `WORKER_TIMEOUT`). Simboli assenti o duplicati danno `symbol_not_unique`; il tool non sceglie arbitrariamente una ridefinizione. Le operazioni delle funzioni annidate hanno proprietario lessicale distinto. Decorator e codice non eseguito restano fatti sintattici, non eventi runtime.

`expected_revision` può essere copiato da `code.search.results[].evidence.source_revision`: se il file è cambiato, l'ispezione restituisce `revision_conflict`. Gli intervalli sono righe fisiche 1-based inclusive; per un file vuoto `scope_span=null`. Syntax error o sintassi non supportata dall'interprete restituiscono `parse_error`, senza inventare fatti parziali.

`relations` distingue:

- `imports`, provenienza `python_ast`: nome importato, modulo, alias, livello relativo, proprietario lessicale e righe. `resolution=not_resolved`: nessun percorso di modulo, uso effettivo o dipendenza runtime viene inferito.
- `implemented_by`, provenienza `explicit_markdown_declaration`: dichiarazione e righe del documento, path e simbolo dichiarati. Se il target ammesso esiste ed è univoco, vengono forniti hash e righe del target (`resolution=symbol_exists`). `behavior_verified=false` rimane sempre: l'esistenza del simbolo non prova che realizzi la decisione. Target esclusi/mancanti o ambigui sono segnalati senza leggere percorsi esterni.

`operations` può mostrare assegnazioni, return, confronti, operatori, chiamate sintattiche, assert, raise, await e yield. Una `Call` non è un arco di call graph risolto; un `Assert` non è un test passato. `behavioral_claims=not_assessed` e `tests_executed=false` sono espliciti. Il consumatore deve leggere le fonti e verificare le specifiche affermazioni; assenza di una categoria AST non dimostra assenza di comportamento dinamico.

`max_chars` limita soltanto l'estratto sorgente (0–100000 caratteri); `max_items` limita complessivamente simboli, operazioni e relazioni, in quest'ordine (1–500). `source_truncated` e `facts_truncated` segnalano tagli. Non si promette che l'intero JSON rientri nel budget dell'estratto. I riferimenti ereditano path/hash della sorgente dalla risposta; target dichiarati hanno revisione distinta. Le letture di file diversi non formano uno snapshot atomico del repository. Le relazioni sono restituite su richiesta e non mescolate ai vicini di similarità del grafo.

### Confronto con decisioni storiche: code.compare_decision

`code.compare_decision(decision_path, base_commit, paths, expected_decision_revision?, max_chars=12000)` confronta una decisione Markdown e 1–20 file sorgente con uno snapshot Git scelto dal chiamante. `base_commit` deve essere l'ID completo di un commit (40 o 64 caratteri esadecimali), non HEAD, un branch, una data o un hash dell'indice. Non si deduce che quel commit sia la data di creazione della decisione: `baseline_relationship=caller_selected_not_inferred` è esplicito.

La decisione corrente deve essere ammessa dalla policy codice oppure dalla discovery wiki (sessioni escluse di default). I file sorgente devono essere presenti e ammessi dalla policy codice corrente prima che venga letto lo storico. Directory nascoste escluse, symlink, percorsi esterni e policy di esclusione restano applicati. Il file decisione deve esistere anche nel commit richiesto; altrimenti viene restituito `decision_missing_at_baseline`, senza inventare uno storico dai timestamp o dagli hash. Il tool funziona anche quando il root del progetto è una sottodirectory del repository.

Git legge oggetti senza checkout, filtri textconv o esecuzione del contenuto. Replace objects e lazy fetch sono disabilitati per i comandi di lettura. Accetta nello storico solo blob regolari, non symlink o submodule, con limite 500 KB per sorgente; i path Git sono literal pathspec. Un oggetto blob passato come base_commit viene rifiutato (`not_a_commit`); oggetti/cronologia mancanti sono errori espliciti. Il tool non scrive commit, branch o indici e non inizializza provider embedding.

Risposta: commit base, confronto della decisione e dei file, SHA256 dei byte correnti/storici, blob OID storico, numero di righe per versione e unified diff. Gli hunk si riferiscono alle rispettive versioni, non a numeri di riga intercambiabili. Stato per file `unchanged`, `modified`, `added` o `unavailable`; stato globale `partial` se almeno un sorgente è indisponibile. File correnti mancanti/esclusi danno `current_missing_or_excluded`: non vengono dedotte cancellazioni o rinomine. `added` significa solo che quel path corrente non esiste nello snapshot selezionato.

La decisione può cambiare indipendentemente dal codice. `expected_decision_revision` verifica l'hash corrente della decisione e restituisce `revision_conflict` se diverso; gli hash dei sorgenti correnti sono restituiti per successivi controlli. `freshness=verified_at_read` riguarda singole letture, non uno snapshot atomico del working tree. `decision_fulfillment=not_assessed`: una differenza testuale non certifica conformità, violazione o copertura test.

`max_chars` (0–100000) è condiviso dal testo dei diff, prima decisione e poi file nell'ordine richiesto; hash e metadati sono fuori budget. `diff_truncated` segnala tagli, anche nel mezzo di un hunk. Il confronto degli hash precede la normalizzazione delle righe: un cambiamento del solo newline finale può avere `status=modified`, `normalized_text_equal=true` e diff vuoto. Il testo è decodificato UTF-8 con sostituzione dei byte invalidi; anche la normalizzazione degli strumenti Git può produrre differenze di byte senza modifiche comportamentali.
