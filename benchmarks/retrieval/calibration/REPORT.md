# Q6 — esperimento di astensione semantica

8 settembre 2026. **Esito: soglia globale non promossa nel plugin.** La distanza semantica identifica candidati simili ma non verifica che implementino il comportamento richiesto.

## Protocollo congelato prima della valutazione

Modello: OpenRouter `google/gemini-embedding-2`, dim3072. Sorgenti fixture invariate rispetto al corpus v2. Dataset nuovo:45 query di sviluppo già presenti in v2 (33 rispondibili,12 senza risposta), seguite da24 query nuove (12 rispondibili,12 senza risposta). Nessuna stringa di query di valutazione è riutilizzata da v2; le fixture e i temi rimangono noti, quindi non si dichiara indipendenza a livello di repository.

Regola fissata nel codice prima delle richieste: scegliere il massimo cutoff stretto che non accetta alcun hit dei casi di sviluppo senza risposta. In concreto, soglia uguale alla minima distanza dei loro hit, accettazione soltanto con `distance < threshold`. Distanze a quattro decimali come restituite dall'API del plugin; non probabilità.

Il callback ha salvato [threshold.json](threshold.json) dopo tutte le45 query di sviluppo e prima della prima richiesta di valutazione. Soglia risultante: **0,3329**. Non è stata ritoccata dopo aver osservato la valutazione. Le regole sono testate per rifiutare dati di valutazione durante il fit e mantenere immutata la soglia dopo il congelamento.

Un passaggio per69 query, con tre metodi riportati:207 righe. RRF riusa i due retriever; la calibrazione riguarda solo il vettoriale. Nessuna politica runtime modificata: il filtro è applicato offline ai risultati del benchmark.

## Risultati prima e dopo il filtro, sulle24 query nuove

| Misura | Vettoriale senza filtro | Cutoff0,3329 |
|---|---:|---:|
| Recall@5 sulle12 rispondibili | 1,000 | 0,708 |
| MRR sulle12 rispondibili | 0,917 | 0,708 |
| Query rispondibili con un risultato rilevante | 12/12 | 9/12 |
| Query senza risposta accettate erroneamente | 12/12 | 7/12 |
| Astensioni corrette sui casi senza risposta | 0/12 | 5/12 |

Le tre domande rispondibili scartate penalizzano anche Recall e MRR: non sono rimosse dal denominatore. Alcune query hanno due fonti attese, perciò Recall@5 non coincide con la quota di domande aventi almeno un hit rilevante.

Sul solo sviluppo, la regola azzera gli errori sui12 no-answer per costruzione, ma accetta27/33 rispondibili e solo26 hanno un hit rilevante; Recall@5=0,773, MRR=0,679. Già qui non raggiunge la soglia Recall0,85. Nella famiglia di filtri globali a cutoff provata, alzare la soglia oltre questo valore ammetterebbe almeno un negativo di sviluppo: nessuna ragione per adottarla come soluzione del gate.

## Errori che spiegano il limite

Sette falsi positivi rimasti comprendono domande come:

- «Which function cancels a running subprocess when WORKER_TIMEOUT elapses?» La fixture definisce la costante, non la cancellazione di processi.
- «How does heartbeat_due send a heartbeat over the network?» La funzione confronta tempi, non invia messaggi.
- «How is cache_key hashed cryptographically to hide tenant identifiers?» Il codice concatena identificatori, senza hashing crittografico.

Nomi e argomenti pertinenti rendono vicino il candidato anche quando il comportamento richiesto manca. Serve verificare che le fonti supportino la specifica affermazione. Questo esperimento non dimostra che ogni forma di calibrazione sia impossibile; mostra che il cutoff globale testato non soddisfa i criteri.

## Tracciabilità, costo e riproduzione

- [Preview con scope e limiti](preview.json)
- [Dataset sviluppo/valutazione](dataset.json)
- [Risultati grezzi con distanze](raw-results.json)
- [Valutazione dopo il filtro](evaluation.json)
- [Hash degli artefatti e algoritmo](provenance.json)

Richieste completate: **73 HTTP**, di cui una probe; limite106.72 batch,114 testi,7081 caratteri embedding;1716 total_tokens inclusa probe. Costo totale riportato dall'API per questa esecuzione: **0,00035474 USD**. Solo contenuti fixture approvati; nessun codice privato indicizzato o inviato. Zero riferimenti invalidi e zero fallback durante il run.

Per riprodurre, con lo stesso interprete dotato di sqlite-vec:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.12/bin/python3.12 benchmarks/retrieval/calibrate.py
```

La prima invocazione produce soltanto la preview. Per eseguire usare `--execute --approved-preview HASH` con il digest appena verificato. Lo script registra la soglia prima delle richieste di valutazione. Ripetere un esperimento non rende nuovamente mai osservato questo set: future scelte del modello/regola richiederanno una nuova valutazione separata.

## Verifiche e decisione

**147 test passati in25.48s, zero skip**. Quattro nuove regressioni su separazione sviluppo/valutazione, congelamento, penalizzazione delle astensioni errate e novità/validità delle etichette. Ruff, registry/README, release check e diff check verdi. Benchmark reale separato dal conteggio pytest.

La soglia resta solo negli artefatti sperimentali. Q6 rimane aperto per verifica delle affermazioni nelle fonti, workflow sulle decisioni storiche e relazioni strutturali. Il prossimo incremento dovrebbe costruire evidenze del comportamento a partire dal codice e dichiarare ciò che resta non verificato. Nessun commit/tag/release o rebuild dell'indice utente.
