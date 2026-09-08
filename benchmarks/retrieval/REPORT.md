# Q6 — baseline retrieval locale, 8 settembre 2026

Stato: benchmark offline eseguibile, difetto di riferimenti corretto. **Q6 resta aperto**: nessuna evidenza sufficiente per promettere qualità semantica o programmazione autonoma.

## Riproduzione

Dalla root del repository, con Python e sqlite-vec disponibili:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.12/bin/python3.12 benchmarks/retrieval/run.py --output /tmp/anja-retrieval.json
```

Il percorso Python è quello di questo Mac; su altri sistemi usare un interprete con `sqlite-vec` e `enable_load_extension`. Il runner forza `mock`, usa copie temporanee e non invia dati in rete. Non costruisce l'indice del progetto utente. I JSON contengono ogni query, risultato, intervallo, hash sorgente, metrica e scenario, oltre a hardware/interprete/provider/modello. La misura successiva al fix registra anche hash del runner e dei moduli principali.

## Corpus e protocollo

`dataset.json` v1: 42 query curate, 3 progetti Python eseguibili (checkout, worker, cache), 30 query di sviluppo e 12 di valutazione separate. Include simboli, comportamento, bug, configurazione, decisioni, test e 3 query senza risposta. Le query senza risposta sono nel set di sviluppo; la valutazione separata contiene domande parafrasate con risposta. Il corpus è intenzionalmente piccolo, con soli 4 file per progetto: Recall@5 elevata può essere facile e non generalizza a repository reali. Va esteso con distrattori e no-answer separati prima di ottimizzare per produzione. Soglie congelate nel manifest prima delle misure; nessun tuning sui 12 casi di valutazione.

La baseline lessicale usa `search_level_0`, quella vettoriale `search_level_2` con sqlite-vec reale e mock-bow a 64 dimensioni. Il mock misura sovrapposizione lessicale con hash, non comprensione semantica. Il confronto RRF (k=60) è un esperimento offline per file: un voto per file per lista, conserva gli intervalli di evidenza, limita a 5 file. La ricerca vettoriale restituisce chunk, quella lessicale file: questa differenza di unità va considerata confrontando Recall@k. Nessuna nuova modalità del prodotto è stata abilitata.

Recall@k: quota di target attesi trovati entro k, richiede path esatto e sovrapposizione con l'intervallo etichettato. Non basta un percorso esistente. MRR: reciproco del rango del primo target valido, zero se assente. Path/righe: esistenza e limiti fisici verificati separatamente dalla rilevanza. Il runner aggiunge hash correnti; questi hash non sono ancora metadati restituiti dall'API del prodotto.

Ogni query esegue primo passaggio e ripetizione; indice già costruito, cache filesystem non svuotata, dunque **non è una prova cold-cache**. p50 mediana, p95 nearest-rank tra query; due passaggi non stimano la varianza tra run. Il tempo include normalizzazione/verifica dei riferimenti; RRF usa la somma dei tempi dei due retriever più fusione. I token sono stime `ceil(caratteri JSON normalizzato / 4)`, non uso fatturato di un tokenizer. Costo mock: $0; costi reali non misurati.

## Risultati dopo la correzione, set separato / ripetizione

| Metodo | Recall@1 | Recall@3 | Recall@5 | MRR | p50 / p95 ms | Token stimati medi |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 0.000 | 0.000 | 0.000 | 0.000 | 6.5 / 9.6 | 1.0 |
| vector_mock | 0.333 | 0.583 | 0.917 | 0.519 | 40.1 / 48.2 | 327.0 |
| hybrid_rrf60_mock | 0.417 | 0.917 | 0.917 | 0.639 | 46.7 / 54.7 | 207.3 |

Nel set di sviluppo: lessicale Recall@1 e MRR 1,000; vettoriale Recall@5 1,000 e MRR 0,840; RRF Recall@1 e MRR 1,000. Il lessicale non trova le parafrasi del set separato. No-answer: lessicale 3/3, vettoriale e RRF 0/3. Non impostiamo una soglia di distanza calibrata sul mock: non sarebbe trasferibile a embedding reali.

## Difetto trovato e modifica isolata

`baseline-mock.json` conserva la misura iniziale: 38 riferimenti vettoriali non validi nelle 42 query del primo passaggio, 4/10 scenari riusciti. Un newline terminale veniva contato come una riga aggiuntiva nei chunk. Unica modifica al retrieval: rimuovere il delimitatore finale prima del chunking, senza cambiare ranking/fusione. Dopo il fix: **zero riferimenti invalidi e 10/10 scenari riusciti**. Dataset e sorgenti fixture identici tra i report. La seconda misura aggiunge anche un controllo esplicito del testo aggiornato negli scenari, oltre ai controlli originari.

Fingerprint pipeline 2 → 3: gli indici precedenti devono essere ricostruiti con il normale flusso preview/reindex; fino ad allora viene usato il fallback dichiarato. Nessun indice reale è stato ricostruito durante questo lavoro. La versione del plugin non è stata incrementata e non è stata pubblicata una release.

## Scenari di sviluppo

Dieci modifiche prescritte su soglie di spedizione, sconti, retry, lease, scadenza cache, isolamento tenant e timeout. Ogni scenario: copia isolata → indice → ricerca del simbolo → asserzione del nuovo comportamento che fallisce → modifica → stessa asserzione che passa → ricerca che rileva indice stale → refresh → ricerca con testo nuovo e riferimenti validi. Tutti 10 rilevano stale prima del refresh; dopo il refresh zero hit con testo precedente. Gli scenari eseguono asserzioni mirate al comportamento cambiato, non dichiarano che i test originali della fixture restino invariati e verdi dopo un cambio di requisito.

Harness deterministico Python fisso, nessun modello generativo: misura integrazione e freshness, non abilità di un agente di scegliere edit o test. La verifica delle revisioni è nel runner e non prova ancora il workflow decisione storica → diff o un budget di contesto imposto dall'API.

## Gate e prossimo lavoro

- Q6.1 e Q6.5: implementati nel perimetro fixture/deterministico. Nuove regressioni proteggono dataset, metriche, fusione e righe finali.
- Q6.2–Q6.3: baseline mock e confronto isolato disponibili; restano provider reale, costo effettivo, run ripetuti e valutazione della latenza stabile. La soglia p95 del 20% non può essere certificata da due passaggi: anche il lessicale immutato oscilla oltre il 20% tra alcune misure. Nessun miglioramento prestazionale dichiarato.
- Qualità separata: MRR vettoriale e ibrida sotto 0,70; no-answer vettoriale 0%. Gate qualità **non superato**, anche se Recall@5 mock supera 0,85.
- Q6.4: navigazione e test esercitati dal dataset; mancano risposte strutturate con revisione/freschezza, confronto decisioni storiche e budget esplicito di contesto.
- Q6.6: relazioni strutturali non introdotte. Le righe `Implemented by` delle fixture sono documentazione esplicita, non edge generati. Nessuna similarità è presentata come dipendenza o prova di copertura.

Prossimo incremento: contratto di evidenza e astensione, ampliamento del corpus, poi baseline su provider reale scelto/configurato con preview dei soli file fixture. Q7 rimane successivo. Nessuna installazione o invio remoto eseguiti.

## Secondo incremento Q6 — contratto evidenze, 8 settembre 2026

L'entry point pubblico `code.search` ora restituisce SHA256 della sorgente e intervalli verificati. Per il vettoriale controlla sia il manifesto letto nella transazione dei vettori sia il testo effettivo del chunk. Una modifica durante l'embedding della query viene rilevata anche se il controllo iniziale dell'indice era passato. Gli hit non verificabili vengono esclusi con motivo; nessun hash corrente viene attribuito a testo vecchio.

`max_preview_chars` limita il testo delle anteprime (default 12000), con indicazione dei tagli; non promette un conteggio esatto dei token. API, schemi e README aggiornati. L'ordine dei risultati validi rimane quello del retriever. La similarità resta distinta dalla prova di comportamento o di dipendenza.

Nuovo `evidence.status`: `literal_evidence`, `candidates_only`, `no_evidence`. `abstain` è un segnale conservativo di assenza di riscontro **letterale negli intervalli restituiti**, non una misura semantica. I candidati restano disponibili per analisi. Un match letterale, anche in un commento o nome, non basta a provare la risposta a una domanda comportamentale.

Riproduzione del nuovo controllo pubblico (stesso interprete del benchmark):

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.12/bin/python3.12 benchmarks/retrieval/check_evidence.py --output /tmp/anja-evidence.json
```

`evidence-mock.json`: 42 query × 2 livelli, budget preview di 400 caratteri per risposta, zero riferimenti invalidi o esclusi nelle fixture stabili. Entrambi i livelli segnalano astensione sui 3/3 casi senza risposta, **ma anche su 12/39 casi con risposta**, tutte le parafrasi separate. Il benchmark di retrieval continua correttamente a contare i candidati vettoriali come risultati: il precedente fallimento del no-answer vettoriale non viene occultato cambiando la metrica. Non viene dichiarato superato il gate qualità semantica; serve ancora calibrazione su provider reale e corpus più ampio.

Verifica: **137 test passati in 24.81s, zero skip**. 16 nuove regressioni: budget e opzioni, revisione/riferimenti via MCP, candidati senza evidenza letterale, sorgenti cambiate, mismatch contenuto/manifesto, path/symlink e modifica durante embedding con fallback stale verificato. Il controllo standalone delle 84 risposte è distinto dal conteggio pytest. Ruff, registry/README, release check e diff check verdi.

Q6.4 avanza sul contratto delle evidenze e sul budget delle anteprime; restano confronto delle decisioni storiche e valutazione dei workflow completi. Q6.6 e validazione su provider reale rimangono aperti. Nessun commit, release, invio remoto o rebuild dell'indice reale.


## Terzo incremento Q6 — corpus v2

Disponibile il [report separato v2](v2/REPORT.md): 72 query, 36 file, 27 query di valutazione con no-answer, tre passaggi e preview vincolata per una futura prova reale. I risultati v1 restano storici; non vengono sovrascritti. Provider reale non disponibile nell'ambiente corrente.


## Prova reale completata — Gemini embedding

[Report Gemini](v2/REPORT-GEMINI.md): stesso corpus v2 e stessa implementazione, Recall@5 1,000 e MRR 0,972 nella valutazione rispondibile. No-answer retrieval ancora0/9; gate Q6 incompleto. Costo API0,0005501 USD,220 HTTP completate. Nessun tuning applicato.


## Astensione semantica — esperimento concluso

[Report calibrazione](calibration/REPORT.md): soglia globale derivata solo dallo sviluppo e congelata prima di24 query nuove. Il filtro non supera la valutazione:7/12 no-answer ancora accettati,3/12 rispondibili scartati. Nessuna soglia applicata alla ricerca del plugin.


## Evidenze strutturali locali

[Report code.inspect](STRUCTURE-REPORT.md): AST, riferimenti/revisioni, imports e dichiarazioni implemented_by con provenienza.36 file e7 casi mirati controllati offline. Nessuna nuova accuratezza semantica dichiarata; behavioral_claims rimane not_assessed.


## Confronto con decisioni storiche

[Report code.compare_decision](DECISION-REPORT.md): baseline Git esplicita e immutabile, differenze decisione/codice con riferimenti per versione.3 workflow completi eseguiti sulle fixture; conformità semantica non dedotta automaticamente.
