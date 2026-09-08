# Anja — piano di affidabilità e qualità per lo sviluppo software

Data: 6 settembre 2026. Baseline: v0.30.0, commit `e5604f1`.
Stato: **Q0/Q1–Q5 implementati e verificati localmente; Q6: strumenti e workflow verificati sulle fixture, gate astensione aperto; Q7 avviato, gate CI verificato localmente**.
Fonti: [audit riverificato](anjadev-audit.md), [piano precedente e decisioni approvate](PIANO.md).

## Obiettivo e perimetro

Rendere Anja un supporto alla programmazione affidabile: ritrova il codice attuale del progetto corretto, conserva decisioni e identità, restituisce riferimenti verificabili e recupera dagli errori senza perdere dati. “Top level” significa risultati misurati sui flussi reali di sviluppo; non implica che il plugin sostituisca il modello, l’IDE o il controllo umano sulle modifiche.

Questo ciclo copre tutti i rilievi dell’audit, inclusa la selezione errata del root emersa nella riverifica. Aggiunge benchmark di programmazione, verifica della distribuzione e diagnostica utilizzabile dagli agenti. Non promette un primato rispetto ad altri prodotti senza un confronto sperimentale.

## Continuità con il piano precedente

Preservare le decisioni D1–D5 già approvate il 4 settembre: `find_duplicates` nel gruppo graph, rimozione del codice Bybit, esecuzione Python opt-in, compatibilità Python dichiarata e package `scripts/anja/`. La distribuzione resta plugin; non si reintroduce il packaging pip rimosso in v0.28.1.

Registry unico, documentazione derivata, CI e adapter sono la base esistente. Le caselle completate del vecchio piano sono storico, non prova che ogni scenario odierno passi. In particolare, il confinamento del wiki va completato e la suite locale va riportata verde.

Il precedente vincolo di schema invariato riguardava il refactor. Qui sono necessarie migrazioni additive per ID e revisioni: si mantengono nomi dei tool e chiamate compatibili dove possibile; ogni cambiamento incompatibile richiede schema versionato, migrazione e note esplicite.

## Baseline precedente ai fix (storico)

- Suite locale: 12 test raccolti, 10 passati e 2 falliti; retry mirato: stessi due fallimenti.
- Runner Python 3.9.6 / pytest 8.0.0; `ANJA_TEST_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12` per i sottoprocessi con sqlite-vec.
- `test_embed_mock`: ricerca nel repository corrente invece della fixture; precedenza cwd sul root configurato riscontrata in `scripts/code_search.py:31`.
- `test_mcp_smoke`: `roadmap.list` senza risposta nel flusso completo, ma funzionante isolatamente; causa poi identificata: il processo di reranking ereditava stdin e consumava richieste MCP successive.
- Riprodotti traversal read/write, scambio ID duplicati, checkpoint dopo embedding fallito, modulo Python non indicizzato, crash MCP su `null` e `[]`.
- Nessun benchmark di qualità del retrieval eseguito in questa verifica. CI remota e host reali non riverificati.

## Decisioni tecniche proposte

1. Un root esplicito prevale sulla discovery. I tool MCP passano il proprio root ai servizi.
2. Helper di path confinati per ciascuno scope; i path esterni legittimi, come una fonte da importare, hanno un contratto separato.
3. ID task opachi persistiti nel Markdown, indipendenti dal titolo. Gli ID legacy sono risolti solo quando non ambigui.
4. Embedding calcolati prima della transazione di pubblicazione. Una transazione SQLite è la prima soluzione; staging su disco solo se necessario per memoria o schema.
5. Manifesto di file e hash come riferimento per la freschezza; Git resta metadato e ottimizzazione.
6. Fingerprint minimo: provider, modello, dimensione, metrica e versione della pipeline. Nessun fingerprint ornamentale.
7. Hash di revisione del contenuto con normalizzazione documentata, escludendo gli eventi di verifica per evitare auto-invalidazione.
8. Scritture atomiche e controllo revisioni condivisi; locking circoscritto al read-modify-write. Le chiamate remote avvengono fuori dai lock.
9. Un coordinatore degli embedding per progetto. Queue SQLite con lease/retry solo se necessaria a garantire deduplicazione e recovery dei processi background.
10. Dipendenze opzionali e complessità aggiuntiva richiedono un beneficio verificato. Il core resta leggero.

Queste sono scelte proposte dal piano, non decisioni già approvate dall’utente in una sessione precedente.

## Sequenza e gate

| Fase | Priorità | Dipende da | Risultato necessario per chiuderla |
|---|---|---|---|
| Q0 | P0 | — | Diagnosi dei fallimenti, test affidabili e baseline registrata |
| Q1 | P0 | Q0 | Root/path corretti e server MCP resistente |
| Q2 | P0 | Q1 | Identità persistenti e nessuna perdita nelle scritture concorrenti |
| Q3 | P0/P1 | Q0, Q1 | Indice recuperabile, aggiornato e coerente col modello |
| Q4 | P1 | Q2, Q3 | Verifiche revisionate e background controllato |
| Q5 | P1/P2 | Q3, Q4 | Policy dati, diagnostica e recovery verificati |
| Q6 | P2 | Q1–Q5 | Miglioramento retrieval e flussi di sviluppo misurato |
| Q7 | rilascio | Q0–Q6 | Installazione, upgrade e compatibilità verificati |

Q2 e Q3 sono separabili a livello di implementazione; il piano non richiede agenti paralleli. Ogni fase si divide in patch reviewabili, senza accorpare migrazioni, refactor generali e nuove feature.

## Q0 — Ripristinare una baseline attendibile

- [x] **Q0.1** Salvare nei report di test interprete principale, interprete subprocess, cwd, root risolto, dipendenze opzionali e skip. Esaminare stdout/stderr e codici di uscita del flusso smoke completo per determinare perché manca la risposta finale. Non assumere che sia il bug degli ID.
- [x] **Q0.2** Rendere le fixture indipendenti dal repository dell’autore: casi da cwd neutra e da un secondo progetto Anja valido. Non spostare soltanto la cwd per mascherare il problema di selezione root.
- [x] **Q0.3** **Regressioni Q1–Q4 implementate e verificate.** Aggiungere regressioni riproducibili per i bug dell’audit, con provider deterministico e fault injection. Tenere i casi distinti, anziché un solo grande smoke opaco.
- [x] **Q0.4** Eseguire lint, controllo registry/documentazione e release check per stabilire la baseline completa.

File iniziali: `tests/test_embed_mock.py:103`, `tests/test_mcp_smoke.py:91`, `.github/workflows/ci.yml:1`, `pyproject.toml:4`.

Accettazione: causa dei due fallimenti documentata nel risultato di esecuzione; ciascuna regressione fallisce sul comportamento difettoso e passa con il relativo fix. Nessuno skip aggiunto per ottenere il verde. La suite diventa interamente verde quando Q1 e gli eventuali altri fix emersi sono chiusi.

## Q1 — Confinamento e protocollo

- [x] **Q1.1** Risolvere il root una sola volta nel contesto MCP; passarlo a ricerca, indice e status. Target esplicito invalido → errore strutturato, senza fallback a un altro progetto.
- [x] **Q1.2** Centralizzare la risoluzione wiki: slug valido, canonicalizzazione, controllo di appartenenza, policy symlink. Controllare anche i percorsi di scrittura, rename, delete, export e allegati, distinguendo sorgente autorizzata da destinazione confinata.
- [x] **Q1.3** Validare tipo della richiesta, metodo, params e arguments prima del dispatch. Gestire JSON non valido, oggetti malformati e batch non supportati senza terminare il processo; rispettare il comportamento delle notifiche.
- [x] **Q1.4** Verificare il comportamento equivalente dell’altro server MCP, mantenendo l’esecuzione opt-in.

File iniziali: `scripts/anja/wiki.py:346`, `scripts/anja/wiki_maint.py:82`, `scripts/anja/server.py:131`, `scripts/anja/code.py:33`, `scripts/code_search.py:31`; estendere l’inventario agli altri handler prima dei fix.

Accettazione: traversal, path assoluti, glob malevoli e symlink esterni non leggono né modificano destinazioni non autorizzate. Due progetti simultanei restituiscono risultati solo dal target. Dopo ogni payload errato, una richiesta valida successiva riceve risposta. Le garanzie sui symlink non vengono descritte come sandbox contro un processo locale ostile che modifica contemporaneamente il filesystem.

## Q2 — Identità e persistenza dei Markdown

- [x] **Q2.1** Persistire gli ID task e supportare lettura dei file legacy. Migrazione esplicita o al primo write, atomica, idempotente e con backup; la sola lettura non modifica file.
- [x] **Q2.2** Preservare ID in rename del titolo, complete, reopen, block e archive. Non assegnare silenziosamente un vecchio ID ambiguo a un task diverso; restituire candidati o errore esplicito.
- [x] **Q2.3** Eliminare le collisioni di `memory.write` con creazione esclusiva e retry; testare più scritture con stesso titolo e orologio congelato.
- [x] **Q2.4** Introdurre helper condivisi per write atomico e read-modify-write protetto. Per gli aggiornamenti derivati da una lettura precedente esporre revisione attesa e conflitto strutturato. Definire la compatibilità dei client che non inviano la revisione; non promettere loro optimistic concurrency completa.
- [x] **Q2.5** Adottare gli helper nei writer di wiki, roadmap, steward e memoria dove pertinenti, preservando frontmatter e sezioni non coinvolte.

File iniziali: `scripts/roadmap_io.py:118`, `scripts/roadmap_io.py:136`, `scripts/anja/roadmap.py:1`, `scripts/anja/memory.py:70`, `scripts/anja/wiki.py:475`.

Accettazione: task omonimi mantengono identità dopo riordino e riavvio. Due writer sulla stessa revisione producono un successo e un conflitto, mai una sovrascrittura silenziosa. Interruzione durante il salvataggio lascia un documento precedente o nuovo completo. La migrazione può essere rieseguita senza cambiare ID.

## Q3 — Indice affidabile e aggiornato

- [x] **Q3.1** Validare numero, dimensione e valori finiti dei vettori restituiti. Qualunque batch fallito o risposta incompleta impedisce l’avanzamento del checkpoint globale di completamento.
- [x] **Q3.2** Pubblicare chunk, cancellazioni, manifesto e checkpoint nella stessa transazione. `force` non cancella l’indice sano prima del successo. L’indice wiki condiviso rimane coerente.
- [x] **Q3.3** Gestire limit/debug come esecuzione esplicitamente parziale. Errori Git non equivalgono a “nessun cambiamento”. File diventati vuoti o esclusi devono perdere i vecchi chunk al successivo aggiornamento riuscito.
- [x] **Q3.4** Rilevare contenuto del working tree, staged, untracked ammessi, delete e rename. Un rename rimuove il vecchio path e indicizza quello nuovo; gestire anche path con spazi, tab e Unicode senza parsing ambiguo.
- [x] **Q3.5** Usare una policy di discovery unica fra full e incremental. Confrontare hash dei contenuti effettivamente letti; se un file cambia durante il build, marcarlo dirty e non dichiarare aggiornata una revisione diversa.
- [x] **Q3.6** Coprire tutti gli intervalli significativi dei file supportati. Sliding chunks per top-level Python lungo, decorator e parti non coperte; verificare anche preamboli e troncamenti dei chunker regex. Riferimenti di riga devono corrispondere al testo restituito.
- [x] **Q3.7** Bloccare ricerca vettoriale incompatibile e avviare rebuild coordinato codice/wiki quando cambia fingerprint. Supportare stessa dimensione con modello diverso e dimensione diversa. Non riscrivere metadati per “sanare” vettori vecchi.
- [x] **Q3.8** Distinguere `ready`, `stale`, `building`, `partial`, `failed` con ultimo successo, tentativo corrente, file mancanti ed errore. Fallback lessicale dichiarato nella risposta.

File iniziali: `scripts/code_index.py:77`, `scripts/code_index.py:227`, `scripts/code_index.py:269`, `scripts/code_db.py:33`, `scripts/wiki_embed.py:149`, `scripts/code_search.py:364`.

Accettazione: errore al primo batch e a un batch intermedio, crash prima della pubblicazione, force interrotto e retry preservano l’ultimo indice valido. Un aggiornamento senza commit trova il contenuto nuovo. Cambio modello non mescola spazi vettoriali. Una singola esecuzione con `limit` non marca l’intero progetto come completato.

Strategia di scala: preparare vettori fuori dal lock; se il buffer supera un limite documentato, usare staging locale. Per schema vettoriale incompatibile valutare DB temporaneo, validazione e sostituzione con coordinamento delle connessioni aperte. Il protocollo di pubblicazione deve essere testato prima di adottare questa variante.

## Q4 — Fiducia delle pagine e processi background

- [x] **Q4.1** Legare ogni verifica alla revisione del contenuto. Conservare eventi storici, ma derivare il trust corrente solo dalle verifiche della revisione attuale. Le verifiche legacy senza hash hanno stato esplicitamente legacy/non revisionato.
- [x] **Q4.2** Applicare la stessa regola a upsert, edit diretto, rename e modifiche dello steward; stabilire quali metadati alterano il contenuto verificato. Un agente non può inventare conferme umane.
- [x] **Q4.3** Serializzare/deduplicare gli embedding per progetto e pubblicare solo se l’hash del job coincide con quello corrente. Testare completamento fuori ordine.
- [x] **Q4.4** Se serve la queue persistente: job con path, hash, fingerprint, stato, tentativi, lease e ultimo errore; worker limitato, backoff finito, ripresa dopo crash e stato terminale visibile. Nessun nuovo servizio esterno.

File iniziali: `scripts/anja/wiki_maint.py:82`, `scripts/anja/wiki.py:523`, `scripts/anja/wiki.py:547`, `scripts/wiki_embed.py:149`; integrare steward e hook dopo l’inventario dei writer.

Accettazione: modifica dopo verifica → trust stale; modifica solo dell’evento di verifica non invalida se stessa. Job vecchio terminato dopo quello nuovo non ripristina vettori obsoleti. Riavvio recupera il lavoro pendente senza loop di retry illimitati.

## Q5 — Policy dati, diagnosi e recupero

- [x] **Q5.1** Definire esclusioni comuni: default, `.gitignore`, `.anjaignore` e configurazione esplicita, con precedenza documentata. Rispettare le regole senza implementare un interprete parziale e sorprendente di `.gitignore`.
- [x] **Q5.2** Aggiungere preview/dry-run: file ammessi, esclusi con motivo, dimensioni, provider e destinazione locale/remota. Il dry-run non invia contenuti né modifica l’indice. Il primo invio remoto deve essere coerente con la configurazione autorizzata.
- [x] **Q5.3** Diagnostica di root, versione/schema, provider senza credenziali, compatibilità DB, età indice, errori/job e capacità opzionali. Errori macchina con codici stabili, messaggi utili e stdout riservato al protocollo.
- [x] **Q5.4** Conservare la policy journal esistente; aggiungere controllo dei transcript mancanti. Archivio integrale opzionale, default journal-only, con retention e cancellazione coerenti. Non promettere recovery lossless quando manca la fonte.
- [x] **Q5.5** Backup/restoration prima delle migrazioni: wiki, roadmap e manifesti; indice ricostruibile. Provare ripristino su copia, anche dopo migrazione interrotta.

Accettazione: fixture ignorate e file sensibili esclusi non arrivano al provider mock; full/incremental selezionano gli stessi file. Status distingue indice mancante, incompatibile e parziale. Recovery ripristina gli ID e il contenuto; un transcript assente è segnalato correttamente.

## Q6 — Qualità reale per chi programma

### Benchmark iniziale e criteri

- [x] **Q6.1** Creare almeno 40 query curate su almeno 3 piccoli progetti fixture, con risultati rilevanti e righe attese: navigazione simboli, comportamento, bug, configurazione, decisioni architetturali e query senza risposta. Separare almeno 10 query di valutazione da quelle usate per tuning.
- [x] **Q6.2** Misurare Recall@1/3/5, MRR, correttezza di path/righe, freschezza, latenza p50/p95, token restituiti e costo embedding. Registrare hardware, provider, modello, warm/cold e versione dataset. Mock per correttezza; qualità semantica su un provider reale configurato, senza confondere le due prove.
- [x] **Q6.3** Baseline lessicale, vettoriale e ibrida. Provare una modifica alla volta a chunking, ranking o fusione; riportare tradeoff e risultati sul set tenuto separato.

Soglie proposte da congelare prima del tuning: 100% target corretto e riferimenti esistenti nei casi deterministici; 100% test di freshness e query senza risposta correttamente gestiti nelle fixture dedicate; Recall@5 ≥ 0,85 e MRR ≥ 0,70 sul set con risposta. Nessun peggioramento di p95 oltre il 20% rispetto alla baseline senza un beneficio qualità documentato. Le soglie non sono prestazioni attuali e si rivalutano soltanto motivando un problema del dataset o del budget operativo.

### Flussi end-to-end

- [x] **Q6.4** Validare: “dove viene gestito X?”, “quali file devo leggere per cambiare Y?”, “cosa è cambiato da questa decisione?”, “quali test coprono questo comportamento?”. Risultati con fonte, righe, revisione/freschezza e limiti; budget di contesto controllato.
- [x] **Q6.5** Eseguire almeno 10 scenari di sviluppo su fixture: ricerca → modifica autorizzata nella fixture → test → refresh → nuova ricerca. Misurare successo, riferimenti errati, informazioni obsolete e token, mantenendo harness/modello costanti nei confronti.
- [x] **Q6.6** Introdurre relazioni strutturali (`imports`, `implemented_by`, poi eventualmente `calls`) solo con provenienza e tipo espliciti. La similarità resta distinta. Parser più complessi o LSP sono opzioni, da adottare solo se migliorano benchmark e costo di manutenzione.

Accettazione: report riproducibile e soglie raggiunte oppure gap dichiarati che bloccano la promessa di qualità. Nessun edge di similarità presentato come prova di dipendenza o chiamata. Nessuna capacità di analisi interprocedurale promessa da semplici regex.

## Q7 — Release e compatibilità

- [x] **Q7.1** Matrice CI già prevista Linux/macOS × Python 3.9/3.10/3.12 verde; embedding realmente eseguito nel job con sqlite-vec, senza skip inattesi. Mantenere soglia coverage esistente; aumentarla solo da misure reali.
  Matrice GitHub verde sul commit ecb2049 (run 34242506313), 192 test, zero skip; coverage 69,3%. Su macOS il driver resta 3.9/3.10/3.12, i sottoprocessi embedding usano Homebrew 3.12 con sqlite-vec.
- [ ] **Q7.2** Smoke degli adapter e del protocollo con nomi canonici/flat e gruppi filtrati. Prova manuale di installazione, restart e upgrade per ciascun host dichiarato supportato; annotare versione dell’host e distinguere test adapter da validazione reale.
- [x] **Q7.3** Migrare una copia di progetto v0.30.0 con task duplicati, wiki verificato e indice esistente. Provare interruzione, riesecuzione e restore. Definire esplicitamente la compatibilità con versioni vecchie dopo la migrazione. Verificato su fixture ricostruita dal contratto v0.30.0, senza migrare il progetto reale; dettagli nell'esecuzione Q7 migrazione.
- [x] **Q7.4** Aggiornare schema, SECURITY, README generato e changelog per il comportamento finale. Verificare che il pacchetto contenga tutti i file necessari, senza dipendere dai path personali dell’autore.

Gate finale: suite e controlli di coerenza verdi, zero regressioni di integrità note, migrazione e restore provati, benchmark pubblicabile e limiti dichiarati. Commit, tag e pubblicazione richiedono la successiva istruzione dell’utente; questo piano non li esegue.

## Mappa di copertura dell’audit

| Voce audit | Chiusura prevista |
|---|---|
| 1 path confinement | Q1.2 |
| 2 embedding/checkpoint/force | Q3.1–Q3.3 |
| 3 working tree/rename | Q3.4–Q3.5 |
| 4 ID task | Q2.1–Q2.2 |
| 5 modello embedding | Q3.7 |
| 6 verifica contenuto | Q4.1–Q4.2 |
| 7 collisioni note | Q2.3 |
| 8 copertura chunker | Q3.6 |
| 9 payload MCP | Q1.3–Q1.4 |
| 10 root ricerca | Q1.1 |
| Persistenza/concorrenza | Q2.4–Q2.5 |
| Queue embedding | Q4.3–Q4.4 |
| Retrieval benchmark | Q6.1–Q6.3 |
| Grafo tipizzato | Q6.6 |
| Test transizioni | Q0.3, criteri Q2–Q4 |
| Privacy/esclusioni | Q5.1–Q5.2 |
| Transcript recovery | Q5.4–Q5.5 |

## Dimensionamento e ordine delle consegne

Stima orientativa per una persona, comprensiva di regressioni e review: Q0–Q1 2–4 giorni; Q2 2–4; Q3 4–7; Q4–Q5 4–7; Q6 4–8; Q7 2–3. Totale indicativo 18–33 giornate effettive, non un impegno di calendario. Ricalibrare dopo Q0 e Q3; analisi LSP e supporto a nuovi linguaggi sono fuori da questa stima.

Consegne consigliate:

1. **Integrità:** baseline, root/path, MCP, ID e collisioni.
2. **Indice:** transazioni, working tree, chunker e modello.
3. **Memoria:** revisioni, concorrenza, background e recovery.
4. **Qualità:** benchmark, flussi reali e compatibilità di release.

Le versioni si assegnano quando il contenuto è chiuso; non promettere una release per ciascuna fase a priori.

## Definizione di completamento di ogni voce

Una casella si chiude solo con implementazione, regressione pertinente superata e verifica degli effetti sul contratto pubblico. Per una migrazione servono anche idempotenza e restore; per un miglioramento retrieval servono risultati sul benchmark. Il report di esecuzione indica cambiamenti, test, limiti e punti ancora aperti.

## Esecuzione Q0/Q1 — 6 settembre 2026

Implementati root esplicito nella ricerca, stdin separato per il reranker, risoluzione confinata dei path wiki e delle destinazioni di allegati/export, validazione dell’envelope condivisa dai due server MCP. I nomi dei tool e lo schema di persistenza non cambiano. Le notifiche valide non generano risposte; batch non supportati e richieste strutturalmente errate producono errori senza interrompere il server.

Diagnosi smoke: il figlio del reranker poteva leggere la pipe MCP. Riprodotto con un eseguibile fittizio che consuma stdin e una sequenza superiore al buffer del server; prima del fix si perdevano risposte, dopo arrivano tutte. Anche i due test originali sono passati prima di rendere lo smoke indipendente dal CLI installato. Lo smoke ora usa un reranker assente intenzionalmente; il percorso rerank riuscito è coperto dal figlio deterministico della nuova regressione.

Verifica finale locale:

```bash
ANJA_TEST_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -o addopts='' -q --tb=short -p no:cacheprovider
python3 -m ruff check .
python3 scripts/gen_tools_doc.py --check
python3 scripts/release_check.py
git diff --check
```

Risultato: **35 passed in 12.37s**, nessuno skip. Ruff, README/registry e release check verdi (v0.30.0, 57 tool, 9 gruppi). Runner Python 3.9.6, sottoprocessi Homebrew Python 3.12. Nuove regressioni in `tests/test_hardening.py:1`; nessuna chiamata a provider reale necessaria.

Limiti: CI remota e host reali non rieseguiti; canonicalizzazione dei path non è una sandbox contro un processo locale ostile che cambia i symlink durante l’I/O. Gli altri bug dell’audit restano aperti. Q0.3 si completa progressivamente con le regressioni delle fasi successive.

Alla chiusura Q0/Q1 il blocco successivo era Q2, completato come descritto sotto. Nessun commit, tag o pubblicazione eseguito.


## Esecuzione Q2 — 6 settembre 2026

- ID `task-<uuid>` persistiti nelle righe Markdown, indipendenti da titolo e ordine. Lettura legacy senza scritture; migrazione al primo aggiornamento con `roadmap_schema: 2`, backup esatto e mappa di alias legacy/tombstone. Alias ambigui e ID duplicati producono errori espliciti.
- Helper `scripts/anja/persistence.py`: pubblicazione atomica, creazione esclusiva, lock laterali, confronto revisioni e preflight delle operazioni su più file. Le note con stesso titolo e orologio congelato non si sovrascrivono.
- `revision` restituita da letture e writer pertinenti; `expected_revision` opzionale per wiki/roadmap, `expected_revisions` per sostituzioni di link dalla preview. Senza revisione client si protegge il ciclo interno, non una lettura precedente del client.
- Archivio prima della rimozione dalla roadmap, retry deduplicato per ID. Rename mantiene il vecchio nome fino a pubblicazione del nuovo e aggiornamento dei link; interruzioni possono lasciare entrambi i nomi.
- Steward passa la revisione del testo da cui ha preparato il merge; conflitti non contano come patch applicate, non marcano il cluster distillato e mantengono le patch pending fallite per il retry. Frontmatter e sezioni supportati dal parser esistente restano preservati; non è stato introdotto un parser YAML generale.

Validazione finale: **54 passed in 13.28s**, nessuno skip. Stesso runner Python 3.9.6 e sottoprocessi Homebrew Python 3.12 del blocco precedente; Ruff, README/registry, release check e diff check verdi. Le 19 regressioni Q2 coprono migrazione/restore/retry, alias, identità dopo transizioni, due processi sulla stessa revisione, lock rilasciato dopo kill, pubblicazione interrotta, archivio parziale e retry, clock congelato e conflitti wiki/steward.

Contratto e recupero documentati in `SCHEMA.md`, sezioni Roadmap format e Revisioni e scritture concorrenti. Il sotto-formato roadmap è versionato separatamente dal layout globale. Non usare writer precedenti sui file v2; il backup pre-migrazione non contiene gli aggiornamenti successivi.

Limiti: garanzie per writer cooperanti su filesystem locale; nessuna transazione o rollback globale su più file, nessuna protezione assoluta da editor esterni che ignorano i lock. I file legacy reali migrano quando vengono aggiornati: le prove di migrazione di questa sessione hanno usato fixture temporanee. CI remota e host reali non eseguiti. Nessun commit, tag o rilascio.

Prossimo blocco dopo Q2: Q3, completato come descritto sotto.


## Esecuzione Q3 — 7 settembre 2026

Implementato `scripts/index_pipeline.py`: snapshot dei contenuti, staging SQLite su disco (un file fino a 500.000 byte e un batch di 64 vettori in memoria), validazione dei vettori e pubblicazione unica di chunk, vettori, cancellazioni, manifesto e checkpoint. Codice e wiki usano lo stesso percorso. `force` conserva l'indice precedente fino al commit; `limit` e aggiornamenti di una sola pagina hanno stato parziale. Le esclusioni per file vuoto o troppo grande espongono il motivo e rimuovono i vecchi chunk al commit.

La discovery ordinata è unica per full/incremental e confronta hash del filesystem: include modifiche staged, working tree e untracked ammessi, rename/delete e nomi con tab/Unicode. Esclude directory di build, nascoste e symlink; la policy configurabile di Q5 resta aperta. Il commit Git è solo metadato, non il criterio di freschezza. Prima di pubblicare si ricontrolla lo snapshot. Pubblicazioni concorrenti con stesso fingerprint e snapshot compatibile possono completarsi; cambi incompatibili causano retry limitato a tre tentativi. I manifesti di file differiti non sovrascrivono aggiornamenti concorrenti.

Chunker Python e regex coprono preamboli, decorator, top-level e corpi lunghi con finestre fino a 80 righe e riferimenti verificati. Il fingerprint comprende provider, modello, dimensione, metrica e versione pipeline. Un cambiamento o un indice legacy senza fingerprint avvia un rebuild coordinato codice/wiki; un limite parziale viene rifiutato per la migrazione. Il cambio della tabella vettoriale avviene nella stessa transazione, con rollback DDL verificato. I lettori controllano il fingerprint su una transazione di lettura coerente.

`code.status` espone stato per codice/wiki, ultimo successo, ultimo tentativo, errore, file cambiati e diagnostica del processo interrotto. `code.search` L2 usa fallback lessicale dichiarato quando l'indice non è pronto o è incompatibile; i risultati vettoriali del codice sono filtrati per kind. La freschezza richiede una scansione con hash: la latenza su repository grandi va misurata in Q6.

Validazione: 22 regressioni Q3 su SQLite/vec reale e provider deterministico, comprese risposte invalide, guasto a batch intermedio, rollback dopo cancellazioni e migrazione, morte del processo durante la pubblicazione, retry, contese compatibili, working tree Git, limiti, copertura e fallback. **Suite finale: 76 passed in 16.15s, zero skip.** Ruff, README/registry, release check e diff check verdi. Runner Python 3.9.6 e subprocess Homebrew Python 3.12. Nessuna chiamata a provider reale: qualità semantica e prestazioni non sono ancora misurate. La CI remota non è stata eseguita.

Limiti: filesystem esterno non transazionale; modifiche successive all'ultimo controllo diventano stale alla scansione successiva. Crash può lasciare staging temporaneo nascosto; retention/recovery sono Q5. I job background ancora possono duplicare richieste embedding: deduplicazione, scheduling e lease restano Q4. Non è stato ricostruito l'indice reale del progetto né eseguito commit/tag/release.

Prossimo blocco dopo Q3: Q4, completato come descritto sotto.


## Esecuzione Q4 — 7 settembre 2026

`anja/trust.py` deriva il trust dalla revisione del contenuto, escludendo dall'hash soltanto verified/generated/updated. Conserva eventi legacy e revisionati; distingue unverified, legacy, stale, machine-confirmed e human-reviewed. `wiki.read` calcola trust e revisioni prima del troncamento; `wiki.verify` e i due lint usano lo stesso contratto. I cambiamenti via upsert, edit diretto, backlink e steward invalidano le vecchie verifiche quando cambia il testo. Un rename che conserva il testo conserva la verifica.

Corretto il default umano implicito: `wiki.verify` registra `process:anja` e rifiuta `human:*`. Una review umana effettivamente svolta si registra dalla CLI locale `verify_page.py`, con revisione attesa obbligatoria. Non è un'autenticazione dell'identità né una firma: chi può scrivere i file resta nel confine di fiducia locale. Il contratto e la procedura sono in SCHEMA.md.

`wiki_jobs.py` introduce la coda SQLite persistente condivisa fra writer e hook. Deduplica lo snapshot corrente; mantiene nuovi job per A→B→A; legge lo snapshot dopo aver ottenuto la transazione della coda, così un accodamento ritardato non annulla un job nuovo. Stato, hash, fingerprint, tentativi, lease, errori e timestamp restano diagnosticabili. Un flock limita a un worker per progetto, con un figlio per job. Massimo tre tentativi con backoff 2/4 secondi; timeout di 300 secondi anche nel figlio. Il figlio conserva il lock e conclude/risveglia la coda se muore il coordinatore. Riavvio MCP e CLI recuperano il pendente; --retry-failed apre esplicitamente un nuovo ciclo conservando lo storico.

Il job verifica snapshot e fingerprint prima di lavorare; la pipeline ricontrolla lo snapshot prima del commit. Testato il completamento fuori ordine senza ripristino dei vettori vecchi. `code.status.wiki_jobs` espone conteggi e ultimi 20 job. Aggiornati hook, writer di pagine, backlink, rename/delete, log, indice e immagini; gli opt-out esistenti, incluso quello dello steward, restano rispettati. Refresh espliciti continuano a usare la pubblicazione transazionale Q3.

Validazione Q4: 21 nuove regressioni su trust, legacy, conferme umane, revisioni obsolete, modifica diretta/rename/steward/lint, dedup, cancellazione, accodamenti ritardati, timeout, retry terminale/esplicito, tre worker concorrenti, morte del coordinatore prima e durante il job e completamento fuori ordine. Test della coda con SQLite/vec reale e provider mock, senza rete. Suite completa: **97 test passati in 19.31s, zero skip**; Ruff, README/registry, release check e diff check verdi. Runner Python 3.9.6, sottoprocessi Homebrew Python 3.12.

Limiti: lock locali POSIX (macOS/Linux), non coordinamento distribuito; nessuna autenticazione contro writer con accesso libero al filesystem. Retention di coda/staging e policy dati sono Q5; benchmark semantici e prestazioni Q6. Nessuna CI remota, nessun indice reale ricostruito, nessun commit/tag/release.

Prossimo blocco dopo Q4: Q5, completato come descritto sotto.


## Esecuzione Q5 — 7 settembre 2026

Policy unica in `index_policy.py`: default di esclusione, Git nativo per `.gitignore` e `.anjaignore`, include/exclude letterali e configurazione validata. Regole applicate a full/incremental, wiki/single, job e candidati del reranker. File diventati esclusi perdono i vecchi vettori dopo un refresh riuscito; cambi di policy interrompono i batch successivi. Senza Git quando necessario si fallisce chiusi. I nomi sensibili sono un default, non uno scanner universale del contenuto.

Preview su code.reindex/wiki.embed e slash command: eleggibili, motivi di esclusione, dimensioni sorgente/hash, provider/destinazione, autorizzazione e migrazione coordinata codice/wiki. Nessuna costruzione del provider, probe HTTP o scrittura sull'indice. L'invio remoto richiede coppia provider/modello esplicita; il reranker ha autorizzazione indipendente. Query vettoriali senza autorizzazione non avviano neppure il costruttore remoto. Nessuna autorizzazione reale è stata abilitata durante questo lavoro.

`project_diagnostics.py` arricchisce code.status con root/versione/schema, capacità, configurazione senza chiavi, compatibilità/età indice, job e transcript mancanti/corrotti. Aggiunti codici macchina per i nuovi errori. Policy, index/queue state e manutenzione rifiutano i percorsi symlink non previsti.

`project_recovery.py` produce backup verificati prima dei rebuild di migrazione embedding, delle migrazioni roadmap e dell'upgrade schema progetto. Restore su directory nuova, checksum verificati prima di pubblicare, ID/contenuti conservati e manifesto dell'indice recuperato; vettori da ricostruire. Ripristino provato anche dopo migrazione embedding interrotta. Snapshot limitato a wiki/roadmap/configurazione/manifesti: codice, raw e transcript non sono inclusi.

Policy journal mantenuta. Archivio integrale opt-in al SessionEnd, limite 100 MiB e copie con checksum; disponibilità delle fonti distinta dalla presenza del journal. `project_maintenance.py` offre preview/apply per retention di copie integrali, job terminali e staging abbandonati, mantenendo journal, job pendenti, build attivi e backup. I riferimenti a transcript scaduti restano storici e sono diagnosticati come missing; nessuna promessa di recovery lossless.

Aggiornati SCHEMA, SECURITY, README generato e workflow anja-index-code/anja-config. Nuove regressioni in test_index_policy.py e test_recovery_policy.py; test reranker Q1 mantiene l'esecuzione del figlio con policy esplicita. 20 nuove regressioni Q5. **117 test passati in 23.83s, zero skip**; Ruff, README/registry, release check e diff check verdi. Runner Python 3.9.6, sottoprocessi Homebrew Python 3.12.

Limiti: filesystem locale cooperante; restore multi-file può lasciare la nuova destinazione parziale dopo errore, senza alterare l'originale. La manutenzione è esplicita, non un nuovo daemon. CI remota e provider reali non eseguiti; nessun commit/tag/release, nessun indice o archivio reale ricostruito.

Prossimo blocco: **Q6 — benchmark retrieval, qualità misurata e flussi di programmazione end-to-end**.


## Esecuzione Q6, primo incremento — 8 settembre 2026

Creati dataset congelato di 42 query/3 fixture con 12 query separate, runner offline, baseline lessicale/vettoriale mock/RRF e 10 scenari deterministici ricerca→edit→test→refresh→ricerca. Report completo: [benchmarks/retrieval/REPORT.md](benchmarks/retrieval/REPORT.md). Q6.5 è verificato per edit prescritti, non per programmazione autonoma con LLM.

Il benchmark ha scoperto riferimenti oltre EOF: corretto il newline finale in `code_index.chunk_text`, fingerprint pipeline 3 per invalidare gli indici precedenti. Stesso corpus: riferimenti vettoriali invalidi 38→0 nel primo passaggio, scenari 4/10→10/10. Ricerca stale riconosciuta 10/10, nessun testo vecchio dopo refresh. Gli indici reali richiedono il normale rebuild, non eseguito qui.

Gate qualità ancora aperto: MRR su set separato 0,519 vettoriale mock e 0,639 RRF, no-answer 0/3 per entrambi. Nessuna pretesa semantica dal mock. Q6.2–Q6.4 e Q6.6 rimangono incompleti: provider reale/costi, stabilità latenza, contratto evidenze/decisioni/budget, relazioni strutturali. Prossimo lavoro: evidenza e astensione prima di tuning e validazione reale.

Validazione: **121 test passati in 24.37s, zero skip**, benchmark standalone separato (42 query × 3 metodi × 2 passaggi, 10 scenari), Ruff, README/registry, release check e diff check. Nessun commit/tag/release o invio remoto.

## Esecuzione Q6, secondo incremento — 8 settembre 2026

Aggiunti a `code.search` revisione SHA256, righe verificate, stato di evidenza e budget anteprime. Il manifesto dei vettori è letto nella stessa transazione degli hit; verifica successiva del contenuto corrente esclude anche mutazioni avvenute durante l'embedding della query. Risultati non verificabili esclusi con motivazione. Fallback lessicale verificato conservando la distinzione tra sorgente corrente e indice stale.

Astensione prudente senza soglie arbitrarie sul mock: nessun riscontro letterale → `abstain=true`, candidati ancora consultabili; un match letterale non certifica una risposta comportamentale. 84 risposte su corpus invariato: astensione 3/3 no-answer, ma anche 12/39 con risposta per entrambi i livelli. Gate semantico non superato: il report espone esplicitamente queste astensioni su domande rispondibili, senza trasformare il nuovo segnale in miglioramento artificiale del retrieval.

**137 test passati in 24.81s, zero skip**, 16 nuove regressioni e controllo standalone separato. Ruff, README/registry, release check e diff check verdi. Dettagli e JSON in [report Q6](benchmarks/retrieval/REPORT.md). Schema e README aggiornati.

Q6 resta in corso: completata la parte riferimenti/freschezza e budget delle anteprime di Q6.4, non ancora confronto decisioni o copertura strutturale. Prossimi: corpus ampliato e provider reale per calibrare rilevanza/astensione; decisioni storiche e relazioni con provenienza. Nessun commit/tag/release, rete o indice reale modificato.


## Esecuzione Q6, terzo incremento — 8 settembre 2026

Corpus v2: 72 query, 36 file, 24 distrattori aggiuntivi, 27 query di valutazione (18 rispondibili, 9 no-answer), domande italiane e 6 query multi-target. Preservati dataset e file v1. Tre passaggi, 648 righe di valutazione: mock vettoriale Recall@5 0,639 e MRR 0,349; RRF MRR 0,394, no-answer retrieval 0% per entrambi. Zero riferimenti invalidi. Gate qualità non superato; nessun tuning sul mock.

Runner reale preparato con preview file/query/provider/modello, digest di approvazione, copie temporanee, allowlist esatta dei testi in uscita e budget aggregati. Anteprima default OpenRouter: 4.125 byte sorgenti, massimo 261 testi/11.973 caratteri e 253 richieste incluse un eventuale probe. Nessuna chiamata remota eseguita: credenziali assenti anche dopo caricamento dei secrets di progetto, dipendenza locale assente. Scelta provider richiesta all'utente; non confondere preparazione con calibrazione effettuata.

Report e riproduzione: [benchmarks/retrieval/v2/REPORT.md](benchmarks/retrieval/v2/REPORT.md). Q6 resta aperto per calibrazione reale, decisioni storiche e relazioni strutturali. Nessuna modifica di indice/policy del progetto reale, nessun commit/tag/release.


Validazione finale del terzo incremento: **143 test passati in 25.05s, zero skip**. Sei nuove regressioni su preservazione del corpus, preview senza provider, digest errato, sorgenti cambiate, allowlist in uscita e budget aggregati. Benchmark standalone distinto: 648 valutazioni, zero riferimenti invalidi. Ruff, README/registry, release check e diff check verdi.


## Esecuzione Q6, prova reale — 8 settembre 2026

Su richiesta esplicita dopo configurazione locale, eseguito OpenRouter `google/gemini-embedding-2` (dim3072) sul corpus v2:72 query,36 file,3 passaggi,648 righe di valutazione. Hash corpus, sorgenti, runner e implementazione uguali al mock. Nessun tuning. Valutazione rispondibile (18 query): Recall@5=1,000 e MRR=0,972, identici nei tre passaggi. RRF non migliora questi valori. Zero riferimenti invalidi.

No-answer retrieval ancora0/9; astensione letterale true anche sulle18 query rispondibili. Gate qualità complessivo aperto. Q6.2/Q6.3 ora misurati anche con provider reale; le caselle non attestano superamento del gate no-answer o calibrazione completata. P95 vector427–487ms nella valutazione; tradeoff rispetto al mock dichiarato, senza garanzie di latenza produttiva.

Consumo dichiarato dall'API per il run riuscito:220 HTTP completate inclprobe,219 batch,261 testi/11973 caratteri,2604 total_tokens,0,0005501 USD. Report [REPORT-GEMINI.md](benchmarks/retrieval/v2/REPORT-GEMINI.md). Controllati completezza, budget e identità del confronto; nessun codice runtime modificato o pytest rieseguito.

Prossimo: calibrazione astensione sullo sviluppo e nuovo set di valutazione congelato; Q6.4 workflow decisioni storiche e Q6.6 relazioni strutturali ancora da completare. Nessun indice/policy reale modificato, nessun commit/tag/release.


## Esecuzione Q6, calibrazione dell'astensione — 8 settembre 2026

Esperimento sullo stesso modello Gemini:45 query sviluppo,24 nuove valutazione, fixture v2 invariate. Regola predefinita massimo cutoff stretto che accetta zero negativi di sviluppo; soglia0,3329 congelata dopo sviluppo e prima delle query di valutazione. Nessun tuning sul nuovo set.

Esito negativo: sullo sviluppo Recall@5=0,773; sulle12 rispondibili nuove Recall@5 1,000→0,708, MRR0,917→0,708. Il filtro scarta3 rispondibili e accetta erroneamente7/12 no-answer. Soglia non applicata al plugin. La vicinanza semantica a una funzione/variabile non verifica il comportamento attribuito dalla domanda.

147 test passati in25.48s, zero skip, quattro regressioni aggiuntive; Ruff/registry/release/diff verdi. Run reale:73 HTTP completate,1716 total_tokens,costo API0,00035474 USD,zero riferimenti invalidi. Report [calibration/REPORT.md](benchmarks/retrieval/calibration/REPORT.md). Prossimo: verifica delle affermazioni nelle fonti e relazioni strutturali con provenienza. Q6 resta aperto, nessuna modifica runtime, indice reale, commit o release.


## Esecuzione Q6, ispezione ed evidenze strutturali — 8 settembre 2026

Aggiunto `code.inspect` (58° tool, stesso gruppo code): fonte/hash/righe, selezione di funzioni/classi e binding di modulo, operazioni AST, budget estratto/fatti, revisione attesa. Non esegue codice o test e non inizializza provider. Path ammessi dalla policy codice; symlink, esclusi e revisioni cambiate rifiutati. Simboli duplicati segnalati, operazioni annidate con proprietario lessicale distinto.

Q6.6 completato nel perimetro dichiarativo: relazioni imports con provenienza AST e risoluzione non inferita; implemented_by solo da dichiarazioni Markdown esplicite, con verifica dell'esistenza del simbolo e hash target. Nessuna prova di correttezza comportamentale e nessun edge di similarità trasformato in dipendenza. Relazioni restituite dal tool, non persistite nel grafo.

Verifica offline:36 file,7 casi mirati dai falsi positivi precedenti,6 import di nomi e3 dichiarazioni implemented_by,zero riferimenti invalidi. **163 test passati in25.01s, zero skip**;16 nuove regressioni, smoke aggiornato alla copertura58/58 tool. Ruff, README/registry, release check e diff check verdi. Aggiornati schemi, README e conteggio descrittivo marketplace; nessun bump/installazione/release.

Report [STRUCTURE-REPORT.md](benchmarks/retrieval/STRUCTURE-REPORT.md). Q6.4 resta parziale: mancano confronto decisioni storiche e verifica completa dei workflow. Astensione semantica ancora aperta; behavioral_claims=not_assessed e tests_executed=false espliciti. Nessun invio remoto o indice reale modificato.


## Esecuzione Q6, confronto delle decisioni — 8 settembre 2026

Aggiunto `code.compare_decision` (59° tool): decisione Markdown anche nel wiki e sorgenti correnti contro un commit Git completo scelto dal chiamante. Diff condivisi entro budget, SHA256/righe per versione, blob OID storico, revisione decisione attesa, stati separati per decisione/file. Supporto root annidato, pathspec letterali; replace objects e lazy fetch disabilitati. Nessun checkout, commit o provider.

Storico della decisione assente, oggetto non commit, symlink storici, sorgenti esclusi e cambiamenti della revisione sono espliciti. Per file correnti mancanti/esclusi non si inferisce cancellazione o rename. `decision_fulfillment=not_assessed`: il diff non certifica la conformità della decisione.

Q6.4 verificato su3 workflow reali in fixture temporanee: ricerca→ispezione della revisione→asserzione nuova che fallisce→edit prescritto codice/test→stale e revisione vecchia rifiutati→test eseguiti con successo→refresh→ricerca→diff contro commit baseline.3/3 passati, zero outbound. Commit creati soltanto nei repository temporanei dei test, mai nel progetto utente.

**178 test passati in27.82s, zero skip**;15 nuove regressioni. Smoke59/59, Ruff, README/registry, release check e diff check verdi. Report [DECISION-REPORT.md](benchmarks/retrieval/DECISION-REPORT.md). Conteggi README/marketplace aggiornati senza bump, installazione o release.

Le attività Q6 sono implementate/verificate nel perimetro descritto; il gate qualità sull'astensione semantica rimane fallito, non si promette una risposta autonoma affidabile. Q7 (compatibilità, migrazione e distribuzione) resta da affrontare, mantenendo visibile questo gap. Nessun indice/policy reale modificato o invio remoto.


## Esecuzione Q7, gate CI — 8 settembre 2026

La matrice esegue un caricamento reale di sqlite-vec prima di pytest. Il nuovo
`--fail-on-skip`, attivo anche nel job coverage, rende negativo l'esito in presenza
di skip durante raccolta/esecuzione o xfail. L'opzione resta esplicita per mantenere
possibili gli skip locali. Sei regressioni verificano processi pytest reali:
successo, skip di test, skip di modulo, skip locale consentito, xfail e fallimento.

Suite ordinaria: **184 passati in 28,59 s, zero skip**, driver Python 3.9 e
sottoprocessi embedding Homebrew 3.12. Suite coverage in ambiente temporaneo Python
3.12 con pytest 9.1.1 / coverage 7.16.0: **184 passati in 48,35 s, zero skip**.
Coverage aggregata dei sottoprocessi **68,4% (9835 statement, 3106 non coperti)**;
soglia CI invariata al 56%. Il combine ha ignorato 46 file dati duplicati, non test.

Il primo tentativo coverage con interpreti misti aveva prodotto 39 skip ed è stato
respinto dal gate. L'hook sitecustomize di coverage nascondeva il sitecustomize
Homebrew: nella verifica riuscita PYTHONPATH include esplicitamente il percorso
Homebrew dei pacchetti 3.12, oltre a tests/_coverage_hook. Nessun workaround con
path personali aggiunto alla CI o al plugin. Dipendenze aggiuntive solo nel venv
temporaneo /private/tmp/anja-q7-venv; dati coverage in /private/tmp/anja-q7-verified-coverage.

Ruff, README/registry, release check (59 tool, 9 gruppi, 12 comandi, 26 file di test)
e diff check verdi. Gli adapter e i contratti nomi canonici/flat/gruppi sono inclusi
nella suite: questo non certifica installazione/restart negli host reali.
Q7.1 resta aperto per la matrice remota; prossima attività Q7.3, migrazione di una
fixture v0.30.0 con interruzione, riesecuzione e restore, poi pacchetto e host Q7.2/Q7.4.
Il gate di astensione semantica Q6 rimane fallito. Nessun commit, tag o pubblicazione.


## Esecuzione Q7, migrazione — 8 settembre 2026

Q7.3 verificato con sei scenari in `tests/test_release_migration.py`: percorso
normale e morte reale del processo (`os._exit`) durante upgrade, prima/dopo replace
della roadmap, durante la pubblicazione SQLite e durante restore. La fixture
ricostruisce il contratto v0.30.0 letto da `e5604f1:scripts/code_db.py`: layout 1.2,
roadmap con due titoli uguali e owner diversi, verifica wiki YAML senza revisione,
indice cosine con codice/wiki e checkpoint legacy, senza fingerprint/manifests.
È una fixture sintetica, non una copia del progetto privato dell'utente.

Ogni scenario riesegue due volte upgrade → scrittura roadmap → refresh completo.
Verificati tre task conservati, ID distinti/stabili, alias ambiguo rifiutato,
configurazione e sorgenti identitari preservati, file host generati, contenuto wiki
invariato con trust `legacy`, manifesti codice/wiki e fingerprint corrente.
Dopo crash della transazione, chunk e vettori legacy restano identici.

Backup pre/post migrazione ripristinati su nuove directory con confronto byte per
byte e ID preservati. Restore interrotto lascia una destinazione parziale che non
viene sovrascritta; nuovo tentativo su altra destinazione riuscito. Originale
invariato. Codice applicativo, credenziali e vettori non sono inclusi nel restore:
servono recupero separato e rebuild, come documentato in SCHEMA.md.

**190 test passati in 31,79 s, zero skip** con `--fail-on-skip`; sei nuove
regressioni. Ruff, README/registry, release check (59 tool, 9 gruppi, 12 comandi,
27 file test) e diff check verdi. Nessuna correzione al runtime necessaria in questi
scenari. La misura coverage 68,4% appartiene alla precedente suite di 184 test,
non è stata ricalcolata in questo passaggio.

Q7.3 chiuso nel perimetro delle fixture; restano Q7.1 matrice GitHub, Q7.2 prove
host e Q7.4 distribuzione/documentazione finale. Astensione semantica Q6 ancora
aperta. Nessun provider remoto, migrazione del progetto reale, bump, commit,
installazione o pubblicazione eseguiti.


## Esecuzione v0.31.0 e prova crypto — 8 settembre 2026

Versioni allineate a 0.31.0, changelog finale e stato README aggiornati. Manifest
Codex con server MCP inline, metadata interface completi; frontmatter YAML della
skill init-analyze corretto. Configurazione inline confrontata con .mcp.codex.json
nel release check. Validatore plugin Codex superato su runtime estratto e relocato.

`package_plugin.py` serve soltanto come verifica di portabilità: la distribuzione
richiesta dall'utente è GitHub. Inventario esplicito (121 file/link), senza stato
privato del progetto o credenziali, link esterni rifiutati; due regressioni dedicate.
**192 test passati in 31,45 s, zero skip**; Ruff, registry e release check verdi.

`benchmarks/integration/crypto_smoke.py` eseguito contro il runtime estratto su
una copia temporanea di 86 sorgenti crypto (780845 byte): framework/strategies/tests
e backtest_v3.py. Sei controlli superati via MCP stdio: ricerca BacktestEngineV3,
ricerca _parse_dt, inspect BacktestEngineV3._track_trade, reindex completo, ricerca
vettoriale level 2 senza fallback, modifica solo della copia con revision_conflict
e refresh. SHA256 degli originali invariati. Nessun codice trading eseguito,
nessun dato/credenziale/stato privato copiato, nessun invio remoto. Provider mock:
non misura qualità semantica. Report con nomi/hash privati escluso da Git; harness
riutilizzabile incluso. Restano prove host reali e gate astensione semantica.

Commit/push GitHub autorizzati dalla richiesta di aggiornare da GitHub. Matrice
remota da verificare sul commit pubblicato; nessun archivio richiesto all'utente.


## Pubblicazione GitHub v0.31.0 — 8 settembre 2026

Commit funzionale `62c971f`, correzione setup CI `ecb2049`: pubblicati su origin/main.
La prima CI ha rilevato ripgrep assente su Linux e Python macOS senza caricamento
estensioni SQLite. Installato ripgrep nei job test/coverage; su macOS mantenuti
i driver 3.9/3.10/3.12 con interprete Homebrew 3.12 dedicato agli embedding.

Run https://github.com/vincent-vigorito/anjadev/actions/runs/34242506313:
**8/8 job verdi, 192 test, zero skip; coverage 69,3% (9877 statement, 3031 mancanti)**.
Q7.1 e Q7.4 chiusi nel perimetro documentato. Q7.2 resta aperto per installazione,
restart e upgrade negli host reali; il limite di astensione semantica resta esplicito.

Prova crypto ripetuta anche usando un clone effettivo da GitHub: stessi sei
controlli MCP superati su 86 file, nessun fallback nella ricerca vettoriale mock,
originali invariati. Non è stato eseguito un backtest o installato il plugin nel
progetto crypto reale. Distribuzione tramite GitHub; archivio usato solo per QA.
