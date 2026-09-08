# Audit tecnico di `vincent-vigorito/anjadev`

**Repository analizzato:** `vincent-vigorito/anjadev`
**Branch:** `main`
**Commit esaminato:** `e5604f1`
**Versione:** `0.30.0`
**Data analisi e riverifica locale:** 6 settembre 2026
**Stato:** audit della baseline `e5604f1`; fix Q0/Q1–Q5 applicati successivamente, vedi aggiornamento qui sotto. Piano operativo: [PIANO-QUALITA.md](PIANO-QUALITA.md).

## Aggiornamento Q6 — confronto decisioni completato sulle fixture, 8 settembre 2026

Nuovo `code.compare_decision`: confronto di decisione e sorgenti con commit Git completo esplicito, hash/diff/righe per versione. Nessuna ricostruzione inventata dello storico, nessun giudizio automatico di conformità.3/3 workflow ricerca/ispezione/test/refresh/confronto riusciti su repository temporanei.

**178 test passati, zero skip**, smoke59/59 e controlli statici/registry/release verdi. Q6.4 completato nel perimetro fixture; il gate astensione rimane aperto nonostante il completamento degli strumenti. [Report e limiti](benchmarks/retrieval/DECISION-REPORT.md). Nessun commit nel progetto o modifica all'indice reale.

## Aggiornamento Q6 — evidenze strutturali, 8 settembre 2026

Nuovo tool `code.inspect`: ispezione Python con AST, revisioni attese, righe e fonte, operazioni e dichiarazioni imports/implemented_by con provenienza. Relazioni dichiarate e asserzioni non sono presentate come prove di comportamento o test eseguiti.36 file e7 casi mirati verificati offline,zero riferimenti invalidi.

**163 test passati, zero skip**, smoke58/58 tool e controlli statici/registry/release verdi. Nessuna chiamata remota o modifica all'indice utente. Il gate di astensione rimane aperto; Q6.6 implementato, Q6.4 ancora parziale. [Report e limiti](benchmarks/retrieval/STRUCTURE-REPORT.md).

## Aggiornamento Q6 — cutoff semantico respinto, 8 settembre 2026

Calibrazione solo sulle45 query di sviluppo, soglia congelata prima di24 domande nuove. Il cutoff0,3329 lascia7 falsi positivi sui12 no-answer e scarta3 delle12 domande rispondibili. Recall@5 dopo filtro0,708, sotto soglia. Non applicato al plugin: serve verificare le affermazioni nel contenuto, oltre alla similarità.

147 test passati, zero skip;73 HTTP completate per l'esperimento, costo API0,00035474 USD. [Risultati e protocollo](benchmarks/retrieval/calibration/REPORT.md). Nessun indice utente modificato.

## Aggiornamento Q6 — prova reale completata, 8 settembre 2026

Gemini embedding tramite OpenRouter, sullo stesso corpus v2 e codice del mock: Recall@5 **1,000**, MRR **0,972** sulle18 domande rispondibili della valutazione, identici nei tre passaggi. Zero riferimenti invalidi.220 richieste HTTP completate; costo API dichiarato0,0005501 USD.

Gate Q6 ancora aperto: candidati restituiti in tutti i9 casi senza risposta; l'astensione letterale non è calibrata semanticamente. Il ranking migliora nettamente rispetto al mock, senza modifiche al codice o tuning sui dati. [Report completo e limiti](benchmarks/retrieval/v2/REPORT-GEMINI.md).

## Aggiornamento Q6 — corpus ampliato, 8 settembre 2026

Benchmark v2 con 72 query, 36 file e 27 casi di valutazione separati, inclusi no-answer. Mock vettoriale Recall@5 0,639 e MRR 0,349: il gate qualità rimane aperto. Zero riferimenti invalidi. Nessun tuning o confronto prestazionale diretto con v1, che conserva un corpus più semplice.

**143 test passati, zero skip**, controlli statici/registry/release verdi. Pronto un runner con preview vincolata a file, query, provider/modello e limiti di invio, su copie temporanee. Prova reale non eseguita: mancano credenziali embedding o modello locale. [Report v2](benchmarks/retrieval/v2/REPORT.md). Nessun codice privato inviato e nessun indice reale modificato.

## Aggiornamento Q6 — evidenze verificabili, 8 settembre 2026

`code.search` aggiunge hash delle sorgenti, righe e contenuto verificati, budget delle anteprime e motivi di esclusione. Verificata la modifica concorrente della sorgente durante l'embedding: nessun risultato vecchio spacciato per corrente. Il segnale di astensione riguarda l'assenza di riscontro letterale, non certifica rilevanza semantica; candidati conservati.

Sul corpus mock: astensione su 3/3 no-answer e su 12/39 query rispondibili. Limite esplicito, nessun superamento dichiarato del gate semantico. **137 test passati, zero skip**, controlli statici/registry/release verdi e 84 risposte controllate separatamente. [Report aggiornato](benchmarks/retrieval/REPORT.md). Q6 rimane aperto per provider reale, decisioni storiche e relazioni strutturali.

## Aggiornamento Q6 in corso — 8 settembre 2026

Benchmark offline riproducibile: 42 query, 3 fixture, 12 query separate e 10 scenari deterministici. Scoperto e corretto un difetto aggiuntivo nei riferimenti oltre EOF dei chunk terminati da newline; pipeline fingerprint 3 richiede rebuild degli indici precedenti. Stesso corpus: riferimenti invalidi 38→0, scenari riusciti 4/10→10/10. **121 test passati, zero skip**; benchmark standalone e controlli statici/registry/release separati.

Q6 non è concluso: MRR mock vettoriale 0,519 e RRF 0,639 sul set separato; astensione vettoriale 0/3. Nessuna misura semantica con provider reale, nessuna promessa di programmazione autonoma. Metriche, metodi, limiti e lavoro residuo nel [report Q6](benchmarks/retrieval/REPORT.md). Nessun indice reale ricostruito.

## Aggiornamento dopo implementazione Q0/Q1 — 6 settembre 2026

Chiusi nel working tree i rilievi 1 (read/verify e altri percorsi wiki), 9 (envelope MCP su entrambi i server) e 10 (root della ricerca). Identificata e corretta anche la causa del fallimento smoke: stdin ereditato dal processo di reranking, che poteva consumare le richieste MCP successive. Regressioni in `tests/test_hardening.py:1`.

Suite finale: **35 test passati, zero skip**; Ruff, README/registry e release check verdi. Gli altri rilievi restano aperti. Le sezioni seguenti descrivono l’evidenza della baseline, non affermano che i difetti già corretti persistano nel working tree. Dettagli e limiti nel [piano](PIANO-QUALITA.md#esecuzione-q0q1--6-settembre-2026).

## Aggiornamento dopo implementazione Q2 — 6 settembre 2026

Chiusi nel working tree anche i rilievi 4 (ID task) e 7 (collisioni delle note). Introdotti ID persistenti con migrazione e backup, revisione attesa nei writer Markdown, salvataggi atomici e gestione dei conflitti nello steward. Archivio con retry deduplicato per ID; revisioni controllate prima delle operazioni su più file. Il contratto non promette rollback globale del filesystem.

Verifica aggiornata: **54 test passati, zero skip**, Ruff e controlli registry/release verdi. Le verifiche sul trust del contenuto (rilievo 6) restano distinte dal controllo di concorrenza appena aggiunto e saranno affrontate in Q4. A chiusura Q2 i problemi dell’indice erano ancora aperti; sono affrontati nell’aggiornamento Q3 seguente. Dettagli in [PIANO-QUALITA.md](PIANO-QUALITA.md).

## Aggiornamento dopo implementazione Q3 — 7 settembre 2026

Chiusi nel working tree i rilievi 2 (embedding/checkpoint/force), 3 (working tree/rename), 5 (fingerprint del modello) e 8 (copertura chunker). Codice/wiki condividono staging su disco e pubblicazione transazionale. Validati rollback, crash del processo, migrazioni a stessa/diversa dimensione, pubblicazioni concorrenti, file cambiati durante embedding, stato e fallback lessicale esplicito. Le 22 nuove regressioni usano provider deterministico e SQLite/vec reale. **Suite finale: 76 test passati in 16.15s, zero skip**; Ruff, README/registry, release check e diff check verdi.

Restano aperti trust legato alla revisione e controllo dei processi background (Q4), policy dati/recovery (Q5), benchmark di qualità e flussi reali (Q6), compatibilità/prodotto (Q7). Nessuna misura di qualità semantica con provider reale è implicita nei test passati. Dettagli e limiti nel [piano operativo](PIANO-QUALITA.md#esecuzione-q3--7-settembre-2026).

## Aggiornamento dopo implementazione Q4 — 7 settembre 2026

Chiuso il rilievo 6 (verifica legata al contenuto). Trust calcolato dalla revisione corrente, storico conservato e legacy esplicito; nessuna conferma umana implicita nel tool. Introdotta coda locale persistente per gli embedding background con dedup, worker unico, timeout, retry finiti, recupero dopo crash e diagnostica. Verificati ordine inverso dei completamenti e morte del coordinatore con figlio ancora attivo. **97 test passati in 19.31s, zero skip**; Ruff, README/registry, release check e diff check verdi.

Restano Q5 (policy dati/diagnostica/recovery), Q6 (qualità retrieval misurata e flussi reali) e Q7 (compatibilità/prodotto). Le prove locali non attestano qualità semantica su provider reali né l'esito della CI remota. Dettagli in [PIANO-QUALITA.md](PIANO-QUALITA.md#esecuzione-q4--7-settembre-2026).

## Aggiornamento dopo implementazione Q5 — 7 settembre 2026

Completate policy dati condivise, preview offline, autorizzazione esplicita dei modelli remoti/reranker, diagnostica senza inizializzazione del provider e codici macchina. Aggiunti snapshot verificati delle migrazioni e restore su copia nuova; archivio integrale dei transcript opt-in, disponibilità delle fonti e retention esplicita di copie/job/staging. Le regole dei journal restano invariate. **117 test passati in 23.83s, zero skip**; Ruff, README/registry, release check e diff check verdi.

Restano Q6 (qualità retrieval e flussi reali misurati) e Q7 (compatibilità/prodotto). I filtri sui nomi non sono uno scanner universale dei segreti, gli snapshot non coprono l'intero progetto e un journal senza transcript non consente recovery integrale. Dettagli e limiti in [PIANO-QUALITA.md](PIANO-QUALITA.md#esecuzione-q5--7-settembre-2026).

## Giudizio complessivo

Il progetto ha una buona impostazione e funzionalità interessanti, ma adesso investirei più nell’affidabilità che nell’aggiunta di altre funzioni.

Ho individuato problemi concreti, soprattutto nel confinamento dei file, nell’indicizzazione incrementale e nell’identità dei task. Non sono soltanto osservazioni sullo stile del codice.

Ho esaminato le parti principali del server MCP, wiki, roadmap, indicizzazione, embedding e conservazione delle sessioni, oltre a test e configurazione CI. Ho anche riprodotto alcuni meccanismi problematici su file fittizi isolati.

La riverifica ha eseguito l’intera suite locale: **10 passati e 2 falliti**. Il retry mirato ripete entrambi i fallimenti (`test_embed_mock`, `test_mcp_smoke`). Runner Python 3.9.6, pytest 8.0.0; sottoprocessi con Homebrew Python 3.12 tramite `ANJA_TEST_PYTHON`. Non sono state validate tutte le CLI end-to-end. Lo stato della CI remota non è stato riverificato: una precedente CI verde non sostituisce questa evidenza locale.

Durante la sola riverifica iniziale non era stato modificato codice applicativo. Questo documento è stato corretto su richiesta; il journal di progetto registra la verifica.

## Evidenze e limiti della riverifica

| Rilievo | Evidenza attuale | Riferimento |
|---|---|---|
| 1 — Path confinement | Riprodotte lettura e modifica di un Markdown esterno al wiki con `../outside`, in directory temporanea | `scripts/anja/wiki.py:360`, `scripts/anja/wiki_maint.py:114` |
| 2 — Checkpoint | Provider simulato in errore: zero chunk e `last_indexed_sha` comunque aggiornato | `scripts/code_index.py:370`, `scripts/code_index.py:414` |
| 3 — Incrementale | Verificato sul codice: diff fra commit e parser che usa solo il primo path | `scripts/code_index.py:227` |
| 4 — ID task | Riprodotto lo scambio di ID fra Alice e Bob dopo completamento e riscrittura | `scripts/roadmap_io.py:136`, `scripts/roadmap_io.py:193` |
| 5 — Modello | Verificato sul codice: apertura rifiuta nuova dimensione prima del rebuild; modello scritto senza invalidazione completa | `scripts/code_db.py:77`, `scripts/wiki_embed.py:160` |
| 6 — Verifica | Verificato sul codice: upsert conserva `verified`, aggiorna `generated`, senza hash della revisione | `scripts/anja/wiki.py:475`, `scripts/anja/wiki.py:523` |
| 7 — Collisioni | Verificato sul codice: fallback al secondo senza ulteriore controllo esclusivo | `scripts/anja/memory.py:92` |
| 8 — Chunker | Riprodotto: modulo Python con 100 costanti → zero chunk | `scripts/code_index.py:104` |
| 9 — MCP | Riprodotto: `null` e `[]` → processo termina con AttributeError; `{}` non termina il processo | `scripts/anja/server.py:131`, `scripts/anja/server.py:203` |
| 10 — Progetto di ricerca | Il test embedding cerca nel repository corrente anziché nella fixture; precedenza cwd confermata nel codice | `scripts/code_search.py:31` |

Il fallimento smoke è «roadmap.list: nessuna risposta», ma la chiamata isolata funziona: causa ancora da determinare, senza attribuirla al bug degli ID. Le prove temporanee erano diagnostiche e non sono ancora test di regressione versionati.

Concorrenza, queue, benchmark e grafo tipizzato nelle sezioni successive sono proposte di evoluzione: non tutti rappresentano difetti riprodotti né interventi urgenti.

---

# Cosa mi piace dell’impostazione

## Knowledge base in Markdown

La scelta di tenere la conoscenza in Markdown è valida. La memoria del progetto rimane leggibile, versionabile e ispezionabile, mentre l’indice vettoriale rimane un componente separato.

Anche l’unione fra conoscenza sul progetto e ricerca nel codice ha senso: Anja non è semplicemente un archivio delle conversazioni, ma uno strumento per ritrovare decisioni, concetti, implementazioni e relazioni tra documentazione e codice.

## Buona struttura del core MCP

Il registro dei tool controlla handler mancanti, nomi duplicati, collisioni dei nomi esposti e gruppi MCP inconsistenti.

Inoltre il filtro `ANJA_TOOL_GROUPS` viene applicato sia a `tools/list` sia a `tools/call`, quindi un tool nascosto non rimane richiamabile semplicemente conoscendone il nome.

## Test, CI e modello di sicurezza

Il progetto dispone già di CI Linux/macOS, test su più versioni Python, test del registry MCP, embedding, adapter, code server, journal, steward, Ruff e coverage.

Anche `SECURITY.md` è una buona base perché chiarisce il modello di fiducia e i limiti di `execute_python`.

---

# Problemi individuati

## 1. PRIORITÀ ALTA — `wiki.read` e `wiki.verify` possono uscire dal perimetro del wiki

### Dove

- `scripts/anja/wiki.py::tool_wiki_read`
- `scripts/anja/wiki_maint.py::tool_wiki_verify`

### Problema

Queste funzioni usano lo slug ricevuto dal tool dentro una ricerca simile a:

```python
wiki.rglob(f"{slug}.md")
```

Non viene necessariamente applicata prima una validazione forte dello slug, una risoluzione canonica del path e un controllo che il file finale rimanga dentro `.anjawiki/wiki`.

Uno slug contenente componenti come `../` può quindi portare la ricerca fuori dalla directory prevista.

Nel caso di `wiki.verify`, il problema è più serio perché il file individuato viene anche riscritto.

### Impatto

Non si tratta di un accesso remoto anonimo o di una RCE. Si tratta però di un tool MCP che può potenzialmente leggere o modificare Markdown esterni al wiki, violando il perimetro dichiarato.

### Miglioria

Creerei un unico helper centrale per la risoluzione sicura dei path che faccia:

1. validazione slug;
2. costruzione path;
3. `resolve()`;
4. controllo `relative_to(wiki_root.resolve())`;
5. gestione symlink;
6. rifiuto in caso di uscita dal perimetro.

**Questo è il primo intervento che farei.**

---

## 2. PRIORITÀ ALTA — Un errore di embedding può lasciare l’indice incompleto ma apparentemente aggiornato

### Dove

`scripts/code_index.py::index`

### Problema

Quando un batch di embedding fallisce, il codice intercetta l’eccezione e continua. Alla fine, però, può comunque aggiornare `last_indexed_sha` al commit corrente.

Il risultato può essere:

1. vengono indicizzati alcuni batch;
2. un batch fallisce;
3. il processo continua;
4. il commit viene marcato come indicizzato;
5. al prossimo aggiornamento Git non risultano differenze;
6. i file mancanti restano fuori dall’indice.

### Effetto

La ricerca può non trovare codice realmente esistente senza rendere chiaro il motivo.

### Problema aggiuntivo con `force=True`

Nel rebuild completo i vecchi chunk possono essere rimossi prima che sia garantito il completamento dei nuovi embedding. Un errore del provider può quindi trasformare un indice funzionante in un indice parziale o vuoto.

### Miglioria

Garantirei prima una pubblicazione transazionale. La prima scelta da valutare è preparare e validare gli embedding fuori dalla transazione, poi applicare dati e checkpoint in una sola transazione SQLite. Uno staging separato serve solo se dimensioni, memoria o migrazione dello schema lo richiedono:

```text
current_index
    ↓
build staging index
    ↓
validation
    ↓
atomic publish
```

Solo alla fine aggiornerei il checkpoint. In caso di errore manterrei il vecchio indice e registrerei uno stato `partial` o `failed`.

---

## 3. PRIORITÀ ALTA — L’indicizzazione incrementale non segue correttamente il lavoro non committato

### Dove

`scripts/code_index.py::get_changed_files_since`

### Problema

Il confronto utilizzato è sostanzialmente:

```bash
git diff --name-status <ultimo_sha> HEAD
```

Questo rappresenta le differenze tra commit, non necessariamente:

- modifiche non committate;
- file nuovi non tracciati;
- cambiamenti presenti nel working tree.

### Perché è importante

Anja viene usato proprio durante lo sviluppo. Il codice più importante da ritrovare è spesso quello appena scritto e non ancora committato.

### Rename

Git può restituire:

```text
R100    old.py    renamed.py
```

Il parser deve trattare esplicitamente i rename, altrimenti il nuovo file può non essere indicizzato e i vecchi chunk possono rimanere nel database.

### Miglioria

Userei un manifesto locale basato sul contenuto reale del filesystem:

```text
file_path
content_hash
mtime
size
indexed_revision
```

Git rimarrebbe utile per commit SHA, versioning e recency, ma non sarebbe l’unica fonte di verità.

---

## 4. PRIORITÀ ALTA — Gli ID dei task possono cambiare e riferirsi a un altro task

### Dove

- `scripts/roadmap_io.py`
- `scripts/anja/roadmap.py`

### Problema

Gli ID vengono derivati dal titolo del task e ricostruiti a ogni lettura. Non sono realmente persistiti come identità immutabile.

Con titoli uguali, l’identità dipende dall’ordine dei task.

### Esempio

| Stato | Alice | Bob |
|---|---|---|
| Entrambi Open, titolo “Fix login” | `fix-login` | `fix-login-2` |
| Alice completato | `fix-login-2` | `fix-login` |

Un agente che conserva il vecchio ID può quindi aggiornare il task sbagliato.

### Miglioria

Ogni task deve avere un ID persistente e immutabile. Il titolo può cambiare liberamente; lo slug può rimanere soltanto una rappresentazione leggibile.

---

## 5. PRIORITÀ MEDIA — Il cambio di modello embedding non è gestito in modo completamente coerente

### Dove

- `scripts/code_db.py`
- `scripts/code_index.py`
- `scripts/wiki_embed.py`

### Caso 1 — Dimensione diversa

Il database rileva una differenza di dimensione e suggerisce un reindex, ma bisogna garantire che la procedura `force=True` riesca davvero a ricostruire il DB invece di fallire durante l’apertura.

### Caso 2 — Modello diverso ma stessa dimensione

Due modelli possono avere la stessa dimensione ma spazi vettoriali differenti.

Esempio:

```text
model A → 1536 dimensioni
model B → 1536 dimensioni
```

Se i vecchi vettori rimangono mentre i metadati passano al modello B, il database diventa semanticamente incoerente.

Peggio ancora se codice e wiki vengono ricostruiti in momenti diversi.

### Miglioria

Salverei inizialmente un fingerprint con provider, modello, dimensione, metrica e versione della pipeline. Il seguente è un possibile schema esteso, da introdurre solo per parametri realmente variabili:

```text
provider
model
dimension
distance_metric
normalization
chunker_version
preprocessing_version
embedding_schema_version
```

Un cambio incompatibile deve forzare una migrazione coerente di codice e wiki.

---

## 6. PRIORITÀ MEDIA — La verifica umana dovrebbe essere legata alla versione del contenuto

La distinzione tra `generated` e `verified` è buona, ma la verifica dovrebbe riferirsi all’esatta revisione approvata.

### Scenario

1. Vincent verifica pagina A.
2. La pagina viene marcata come verificata.
3. Successivamente l’agente modifica pagina A.
4. La verifica precedente rimane.
5. La pagina può sembrare ancora verificata dall’umano.

### Miglioria

Le verifiche devono essere legate a un `content_hash` o a un `revision_id`.

Esempio:

```yaml
verified:
  by: human:vincent
  at: 2026-09-06T10:30:00+02:00
  revision: sha256:abc123...
```

Se il contenuto cambia, la verifica corrente deve diventare `stale`.

---

# Altri bug più circoscritti

## 7. Collisione in `memory.write`

Se il nome del file esiste, viene aggiunto un timestamp con precisione al secondo. Più scritture con lo stesso titolo nello stesso secondo possono quindi produrre collisioni.

### Miglioria

Usare UUID, ULID, timestamp con microsecondi o creazione esclusiva del file con retry.

---

## 8. Il chunker Python può non indicizzare codice di modulo lungo

Il chunker AST crea chunk per classi e funzioni. Il codice top-level non coperto viene aggiunto soltanto in determinate condizioni.

Un file con molte costanti o configurazioni può quindi avere copertura insufficiente.

### Miglioria

Tutto il file dovrebbe avere una copertura deterministica. Le parti non coperte dall’AST dovrebbero essere convertite in sliding chunks.

---

## 9. Payload MCP strutturalmente errati possono far terminare il server

Il loop principale protegge il parsing JSON, ma un JSON sintatticamente valido non è necessariamente una richiesta JSON-RPC valida.

Ad esempio:

```json
null
```

oppure:

```json
[]
```

possono passare il parsing e poi arrivare a codice che usa `req.get(...)`.

### Miglioria

Validare immediatamente la struttura della richiesta e restituire `invalid request` senza mai terminare il server.

---

## 10. PRIORITÀ ALTA — `code.search` può scegliere il progetto sbagliato

### Dove

`scripts/code_search.py:31` (`_project_root`), `scripts/anja/code.py:33`.

### Problema ed evidenza

La ricerca prova la directory corrente prima di `ANJA_ROOT`. Se entrambe identificano progetti Anja validi, `code.search` può cercare nel primo mentre `code.reindex` opera su `ROOT`. Nel retry di `test_embed_mock`, la ricerca ha restituito `tests/test_embed_mock.py` del repository anziché `auth.py` della fixture.

### Miglioria

Passare esplicitamente il root dal tool MCP al motore di ricerca. Per le CLI definire una precedenza unica: target esplicito, configurazione esplicita, discovery dalla cwd. Un target esplicito invalido deve produrre un errore, senza ripiegare silenziosamente su un altro progetto. Testare due progetti validi contemporaneamente.

---

# Come migliorerei strutturalmente Anja

## 1. Layer comune per la persistenza

Molti tool seguono il pattern:

```text
read
modify
write
```

Su file Markdown.

Con più agenti o più CLI questo può produrre race condition e lost update.

Creerei un piccolo persistence layer condiviso con:

- safe path resolution;
- revision hash;
- optimistic concurrency;
- atomic write;
- locking dove necessario.

Concettualmente:

```text
load(file) → content + revision
save(file, new_content, expected_revision)
```

Se la revisione non coincide:

```text
ConflictError
```

La scrittura atomica da sola evita file troncati, ma non evita i lost update.

---

## 2. Coda persistente per gli embedding

Le modifiche al wiki possono avviare processi background indipendenti. Con molte modifiche ravvicinate questo può creare:

- duplicazioni;
- race;
- embedding vecchi che finiscono dopo quelli nuovi;
- errori invisibili;
- troppi processi concorrenti.

Non userei subito Celery. Per Anja basterebbe SQLite.

Esempio:

```text
embedding_jobs

id
file_path
content_hash
status
attempts
created_at
started_at
finished_at
error
```

Con 1-2 worker e deduplicazione per `file_path + content_hash`.

---

## 3. Benchmark del retrieval

I test attuali dimostrano che la ricerca funziona, ma il livello successivo è misurare se trova realmente la risposta giusta.

Creerei un dataset interno di domande con risultati attesi e misurerei:

- Recall@1;
- Recall@3;
- Recall@5;
- MRR;
- latenza;
- costo embedding;
- token ritornati;
- freshness;
- precisione.

Questo permetterebbe di valutare realmente nuovi modelli, chunker, RRF, BM25 e weighting vettoriale.

---

## 4. Separare relazione semantica da relazione strutturale

Nel knowledge graph distinguerei chiaramente:

```text
similarity edge
```

da:

```text
explicit edge
```

Esempi:

```text
wiki page → implemented_by → code
code → imports → code
code → calls → function
concept → related_to → concept
embedding_similarity → code/wiki
```

Una similarità embedding non significa automaticamente una relazione architetturale.

---

## 5. Test sulle transizioni di stato

Aumentare semplicemente il coverage non sarebbe la priorità.

Preferirei test su:

### Indicizzazione

```text
index completo → modifica file → index incrementale
index completo → rename → index incrementale
index completo → delete → index incrementale
index completo → embedding batch fallisce → retry
model A → model B stessa dim
model A → model B diversa dim
```

### Roadmap

```text
duplicate titles
rename title
complete
reopen
archive
```

### Concorrenza

```text
agent A load
agent B load
agent A save
agent B save
```

B deve ricevere un conflitto invece di sovrascrivere A.

### MCP

Testare payload come:

```json
null
```

```json
[]
```

```json
{}
```

senza mai terminare il server.

---

## 6. Privacy e policy di indicizzazione

L’indicizzatore ha una propria lista di directory escluse, ma valuterei anche una policy esplicita con:

```text
.gitignore
.anjaignore
default excludes
user excludes
```

Aggiungerei un comando tipo:

```text
anja index --dry-run
```

che mostri quali file verranno indicizzati e, soprattutto, quali verranno inviati a provider remoti.

---

## 7. Recuperabilità delle sessioni

Le sessioni salvano un riferimento a `transcript_path`, ma un path non equivale automaticamente a un backup.

Se il transcript viene eliminato, spostato o pulito, il drill-down lossless non è più possibile.

Valuterei quindi una policy configurabile:

```text
journal only
```

oppure:

```text
journal + transcript archive
```

con retention, ad esempio:

```text
30 giorni transcript completi
1 anno summary
```

---

# Ordine con cui procederei

| Fase | Interventi | Obiettivo |
|---|---|---|
| **1. Sicurezza e integrità** | Path confinement, ID task persistenti, collisioni note | Non leggere o modificare l’oggetto sbagliato |
| **2. Indice affidabile** | Failure batch, checkpoint, working tree, rename, modello embedding, chunk coverage | Fare in modo che la ricerca rappresenti realmente il progetto |
| **3. Multi-agent** | Revision control, atomic writes, queue embedding, diagnostica | Evitare lost update e race |
| **4. Qualità conoscenza** | Revision-aware verification, retrieval benchmark, typed graph edges | Rendere la memoria affidabile e misurabile |

---

# Cosa NON farei

Non riscriverei Anja da zero.

Non trasformerei immediatamente il progetto in:

```text
PostgreSQL
Redis
Celery
Kafka
microservices
```

La natura locale e leggera del progetto è uno dei suoi punti di forza.

Manterrei:

- Markdown come source of truth;
- SQLite;
- MCP;
- Python stdlib-first;
- plugin locale;
- separazione AnjaDev / AnjaHub;
- adapter per i diversi harness.

Interverrei soprattutto su:

```text
consistenza
integrità
concorrenza
osservabilità
recovery
```

---

# Valutazione finale

La direzione del progetto è buona.

Anja ha già superato lo stadio di semplice plugin sperimentale: ha una vera architettura, un modello di memoria, integrazione multi-harness, ricerca semantica, knowledge graph, journal, skill management e una roadmap interna.

Il prossimo salto di qualità però non deriva necessariamente da altre feature.

Deriva dalla capacità di garantire che:

```text
ciò che Anja ricorda è realmente ciò che esiste
```

che:

```text
ciò che Anja considera verificato è realmente la versione verificata
```

e soprattutto che:

```text
un errore non produca uno stato silenziosamente incompleto
```

Per un sistema di memoria e retrieval, queste proprietà sono più importanti dell’aggiunta di un’altra funzionalità.

## Priorità finale sintetica

Le etichette indicano ordine operativo, non una classificazione formale di vulnerabilità.

### P0 — Integrità e baseline ripetibile

1. Diagnosticare i due fallimenti locali senza nasconderli con skip o fallback.
2. Confinamento dei path e scelta esplicita del progetto.
3. ID task persistenti e scritture senza collisioni.
4. Indice atomico, checkpoint corretto, retry recuperabile.
5. Robustezza del trasporto MCP ai payload malformati.

### P1 — Coerenza quotidiana

6. Working tree, rename, delete e copertura dei chunk.
7. Identità del modello e migrazione coordinata codice/wiki.
8. Verifica legata alla revisione e protezione dai lost update.
9. Embedding background ordinati, recuperabili e osservabili; queue SQLite se necessaria.
10. Policy di esclusione e anteprima dei file destinati al provider.

### P2 — Qualità misurata e maturità del prodotto

11. Benchmark retrieval e flussi di programmazione end-to-end.
12. Relazioni strutturali distinte dalla similarità, se migliorano i casi misurati.
13. Recovery dei transcript e verifiche di compatibilità/installazione.

Il [piano operativo](PIANO-QUALITA.md) specifica dipendenze, migrazioni, gate e criteri di accettazione. Markdown e SQLite restano la base; una riscrittura non è giustificata dai rilievi.
