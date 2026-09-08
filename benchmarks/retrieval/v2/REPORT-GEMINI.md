# Q6 — prova reale Gemini embedding

**8 settembre 2026 — esecuzione completata**, autorizzata dall'utente dopo aver configurato localmente la chiave. Provider OpenRouter, modello `google/gemini-embedding-2`, dimensione rilevata **3072**. Le credenziali non sono incluse nei report. Il primo tentativo nel sandbox non ha completato l'indicizzazione; l'esecuzione con accesso rete autorizzato è terminata correttamente. I contatori riportati riguardano quest'ultima esecuzione.

## Protocollo e riproducibilità

Corpus v2: 72 query, 36 file fixture, 3 progetti e 3 passaggi, per 648 righe di valutazione. Sviluppo: 45 query (33 con risposta, 12 senza). Valutazione separata: 27 query (18 con risposta, 9 senza). La valutazione include parafrasi storiche e nuove query; non costituisce un holdout integralmente mai osservato.

Sono identici alla baseline mock: hash del dataset, hash di tutti i file, hash del runner e dei moduli registrati, numero di passaggi e parametri di retrieval. Nessun tuning applicato. I report contengono tutte le query con hit/righe/revisioni, metriche per passaggio, ambiente e contatori API. Mac arm64, Python 3.12.13; il backend remoto non è fissato a una revisione dei pesi verificabile dal runner.

- [Anteprima approvata](preview-gemini.json)
- [Dati completi della prova reale](baseline-gemini.json)
- [Baseline mock sullo stesso corpus](baseline-mock.json)

La riproduzione usa il runner già descritto in [REPORT.md](REPORT.md) con `--provider openrouter --model google/gemini-embedding-2`, prima preview e poi `--execute --approved-preview` con il digest corrente. La configurazione del file secrets viene caricata senza copiarla nelle fixture. Il test invia solo i testi approvati; non indicizza il repository utente.

## Qualità, set di valutazione, terzo passaggio

Recall è macro-media della quota di target attesi trovati, con path esatto e sovrapposizione delle righe; alcune domande richiedono due fonti. MRR considera il primo target valido. Per questo Recall@1 e MRR non sono direttamente equivalenti.

| Metodo | Recall@1 | Recall@3 | Recall@5 | MRR | p50 / p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Mock vettoriale | 0.139 | 0.417 | 0.639 | 0.349 | 15.6 / 17.6 |
| Gemini vettoriale | 0.861 | 0.972 | 1.000 | 0.972 | 395.9 / 426.9 |
| Gemini + RRF offline | 0.861 | 0.972 | 1.000 | 0.972 | 407.5 / 437.0 |

La qualità su questo set è identica nei tre passaggi. Gemini supera le soglie proposte di Recall@5 ≥ 0,85 e MRR ≥ 0,70 **sulle 18 query rispondibili della valutazione**. Zero riferimenti invalidi su tutte le 648 righe; il runner avrebbe interrotto la prova in caso di fallback o evidenze escluse nelle fixture stabili.

Nel set di sviluppo: vettoriale Recall@5 1,000 e MRR 0,826; RRF Recall@5 1,000 e MRR 0,949. Nel set di valutazione la fusione non migliora il vettoriale e aggiunge il tempo della ricerca lessicale: nessun motivo emerso per promuoverla a default. Il lessicale non trova le domande parafrasate della valutazione (Recall e MRR zero).

## Assenza di risposta: gate ancora aperto

Vettoriale e RRF restituiscono candidati in **tutti i 9 casi senza risposta** della valutazione: no-answer retrieval **0/9**, invariato nei tre passaggi. Il lessicale restituisce zero risultati su quei casi, ma anche sulle domande rispondibili parafrasate.

Il segnale `evidence.abstain` resta basato sul riscontro letterale: per Gemini è true su tutte le 27 query della valutazione, incluse le 18 con risposta. Non lo presentiamo come un classificatore semantico efficace né usiamo quel flag per alterare la metrica no-answer del retrieval.

Conclusione del gate: ranking semantico promettente e soglie Recall/MRR superate su queste fixture; **Q6 non concluso**. Servono una regola di astensione calibrata senza usare il set di valutazione per tuning e prove più rappresentative. Non sono state applicate soglie arbitrarie alle distanze, né modificati ranking o comportamento del plugin durante questa prova.

## Latenza e costo osservati

Vettoriale, valutazione: p50 396–408 ms e p95 427–487 ms tra i tre passaggi. Rispetto al mock il costo temporale aumenta nettamente, insieme al guadagno di qualità (Recall@5 0,639→1,000; MRR 0,349→0,972). Questo documenta il tradeoff, non una garanzia di latenza produttiva. Tre passaggi, cache non svuotata, una macchina e un provider remoto non bastano per certificare la stabilità del p95 o un livello di servizio.

Il provider è riutilizzato dal runner; il tempo delle query include chiamata embedding e verifica delle evidenze, non costruzione iniziale dell'indice. Le latenze non rappresentano una sessione cold-cache. I token restituiti rimangono stime JSON/4 (circa 750 per risposta vettoriale nella valutazione); il payload RRF contiene meno metadati e non è confrontabile a parità di schema.

Consumo del run riuscito, dalla risposta API:

- **220 richieste HTTP completate**, inclusa una probe della dimensione; limite approvato 253.
- **219 batch embedding**, 261 testi, 11.973 caratteri; i contatori testo non includono la probe `ping`.
- **2.604 total_tokens** sommati dai metadati usage, probe inclusa.
- **0,0005501 USD** di costo totale dichiarato dall'API (circa 0,00055 USD). È il costo riportato per questo run, da distinguere dalla fattura definitiva e da eventuali altre attività dell'account.

## Verifiche e prossima azione

Verificati completezza del report, identità corpus/implementazione con il mock, budget, successo di tutte le richieste e validità dei riferimenti. Nessuna modifica al codice runtime: la suite precedente resta documentata separatamente; non si dichiara una nuova esecuzione pytest in questo turno.

Q6.2 e Q6.3 dispongono ora anche di baseline reale, costi e confronto dei metodi. Prossima azione: preparare e validare l'astensione semantica sul solo set di sviluppo e congelarla prima di un nuovo set di valutazione; restano inoltre workflow sulle decisioni storiche e relazioni strutturali con provenienza. Nessun indice/policy del progetto reale modificato, nessun commit/tag/release.
