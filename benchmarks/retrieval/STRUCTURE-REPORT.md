# Q6 — evidenze strutturali locali

8 settembre 2026. Implementato `code.inspect`, senza nuove chiamate remote. Lo scopo è rendere verificabili le fonti recuperate; **non è un classificatore automatico delle affermazioni o dei casi senza risposta**.

## Flusso disponibile

1. `code.search` individua candidati e restituisce hash/righe verificati.
2. `code.inspect` riceve il path, l'eventuale nome qualificato del simbolo e `expected_revision` dall'hit. Se la sorgente è cambiata, rifiuta la revisione.
3. Restituisce il corpo corrente con budget, le categorie AST e le dichiarazioni strutturali. L'agente può esaminare cosa è scritto, ciò che una funzione richiama sintatticamente e le asserzioni nei file test.
4. L'esito dei test deve provenire da un'esecuzione distinta. `tests_executed=false` e `behavioral_claims=not_assessed` restano espliciti nell'ispezione.

Esempio utile ai falsi positivi precedenti: ispezionare `worker/config.py`, simbolo `WORKER_TIMEOUT`, mostra solo `WORKER_TIMEOUT = 300` con categoria `Assign`. Il fatto non dimostra la cancellazione di un processo al timeout. `heartbeat_due` mostra return/confronto/sottrazione; `cache_key` mostra return/concatenazione. Il tool non trasforma queste categorie in una prova generale di assenza di rete o hashing: il comportamento dinamico richiede ulteriore analisi.

## Relazioni con provenienza

`imports` deriva da AST: nomi, alias, modulo relativo e posizione. Rimane non risolto; nessun collegamento runtime viene inventato. Le chiamate sintattiche sono operazioni, non un call graph.

`implemented_by` deriva solo dalla dichiarazione Markdown esplicita. Per target validi vengono verificati esistenza/univocità del simbolo, righe e hash; il contenuto della dichiarazione resta non verificato sul piano comportamentale. Target mancanti, esclusi e ambigui sono segnalati. Le relazioni non sono persistite né confuse con archi di similarità del grafo.

## Verifica offline riproducibile

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.12/bin/python3.12 benchmarks/retrieval/check_structure.py
```

[structure-results.json](structure-results.json): ispezionati **36 file** del corpus v2, **7 casi mirati** corrispondenti ai falsi positivi dell'esperimento di astensione, **6 dichiarazioni di nomi importati** e **3 dichiarazioni implemented_by**. Zero riferimenti invalidi; zero richieste outbound. Il controllo verifica intervalli e categorie AST attese, non misura accuratezza semantica sulle domande naturali. I dataset e report embedding precedenti restano storici e non sono rigenerati.

## Limiti e stato del piano

Python analizzato con la versione AST dell'interprete ospitante; altre lingue non analizzate. Snapshot singoli, non snapshot atomico multi-file. Il budget riguarda estratto e numero totale di fatti, con troncamenti espliciti. Non sono inferite esecuzione dei rami, dispatch dinamico, copertura test o veridicità dei documenti.

Q6.6 dispone ora di relazioni strutturali tipizzate e tracciabili tramite ispezione locale. Q6.4 avanza su navigazione e lettura delle evidenze/test; restano il confronto con decisioni storiche e la valutazione completa dei workflow. Il gate di astensione semantica rimane aperto: nessuna accuratezza nuova viene attribuita a questo strumento.


Validazione finale: **163 test passati in25.01s, zero skip**,16 nuove regressioni. Smoke aggiornato per chiamare anche code.inspect, copertura58/58 tool. Ruff, README/registry, release check e diff check verdi. Nessuna installazione, commit o release.
