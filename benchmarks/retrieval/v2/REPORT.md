# Q6 — corpus v2 e preparazione della prova reale

> Aggiornamento: la prova reale con Gemini è stata completata dopo la configurazione dell'utente. Vedi [risultati reali](REPORT-GEMINI.md). Il testo seguente conserva la fase di preparazione, quando le credenziali non erano ancora disponibili.

8 settembre 2026. Q6 resta in corso. Nessun provider reale eseguito: non risultano chiavi embedding nella shell o nei file secrets del progetto; `sentence-transformers` non è installato nell'interprete di test. Il default rilevato è OpenRouter / `openai/text-embedding-3-small`, senza policy remota configurata. Non sono state cercate chiavi in altri account o progetti. La scelta del provider è stata chiesta all'utente.

## Corpus congelato

72 query su 3 progetti, 36 file totali (12 per progetto). Le 42 query e tutte le sorgenti v1 sono conservate byte per byte; v2 aggiunge 24 file distrattori, 18 domande senza risposta e 12 con risposta. Sei nuove query richiedono due fonti; sono presenti domande in italiano. Le fixture sono esempi sintetici di poche righe: l'aumento dei distrattori migliora la prova, ma non equivale a un repository produttivo.

Sviluppo: 45 query, di cui 12 senza risposta. Valutazione: 27 query, di cui 9 senza risposta e 18 rispondibili. Include le 12 parafrasi storiche già osservate, più 15 query nuove: non è un holdout completamente nuovo. Nessun tuning di ranking, chunking o soglie eseguito. Le soglie del piano restano immutate; il corpus v1 e i suoi report non sono sovrascritti.

Tre passaggi completi del runner pubblico, confronto lessicale/vettoriale/mock e RRF offline. Totale 648 righe di valutazione (72 × 3 metodi × 3 passaggi). RRF riutilizza i due retriever e non chiama un terzo provider. Il primo passaggio segue l'indicizzazione e non misura una cache fredda. p50/p95 sono sulle query di un singolo passaggio; tre passaggi non certificano una distribuzione stabile della latenza di produzione.

## Risultati, valutazione separata, terzo passaggio

| Metodo | Recall@1 | Recall@3 | Recall@5 | MRR | No-answer retrieval | p50 / p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 0.000 | 0.000 | 0.000 | 0.000 | 100% | 6.8 / 8.2 |
| vector | 0.139 | 0.417 | 0.639 | 0.349 | 0% | 15.6 / 17.6 |
| hybrid_rrf60 | 0.194 | 0.472 | 0.639 | 0.394 | 0% | 22.3 / 25.8 |

Zero riferimenti invalidi e zero fallback silenziosi. Le metriche richiedono percorso e sovrapposizione delle righe attese; Recall è la media della quota di target trovati per query, MRR considera il primo target trovato. Vettori ordinano chunk, lessicale e RRF file: le unità non sono identiche.

Il mock vettoriale non raggiunge Recall@5 0,85 né MRR 0,70. Il peggioramento rispetto a v1 riflette anche corpus e distribuzione delle query differenti: non è una regressione isolata del codice. Nessuna pretesa di qualità semantica dal mock. Il no-answer di retrieval continua a contare i candidati restituiti; non viene sostituito dall'astensione letterale del secondo incremento. RRF resta un esperimento, non una nuova modalità predefinita.

Le latenze non sono confrontabili direttamente con v1: v2 usa l'API con verifica evidenze, copie senza repository Git e un'istanza provider riutilizzata. Il JSON registra hardware/interprete, fingerprint implementazione, ogni risultato, budget e metriche per passaggio. I token sono stime dei caratteri JSON/4; il payload ibrido contiene meno metadati dell'API, quindi non è un confronto token a parità di schema.

## Riproduzione e prova remota pronta

Dalla root, con Python dotato di sqlite-vec:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.12/bin/python3.12 benchmarks/retrieval/run_v2.py --provider mock --output /tmp/anja-preview.json
```

La preview non inizializza provider e non invia contenuti. Verificare elenco file, hash, modello e quantità; per eseguire passare il valore `approval_sha256` stampato dalla preview:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.12/bin/python3.12 benchmarks/retrieval/run_v2.py --provider mock --execute --approved-preview HASH_DELLA_PREVIEW --output /tmp/anja-v2-result.json
```

Per provider remoto, usare gli stessi `--provider`, `--model` e `--repeats` nelle due invocazioni. Il digest copre quei parametri, le query e gli hash sorgente. Una modifica del corpus invalida la preview. La policy remota viene scritta solo nelle copie temporanee dopo l'approvazione della preview; la policy e l'indice del progetto reale non vengono modificati. Ogni testo destinato all'embedding deve appartenere all'insieme esatto dei chunk/query approvati, con limiti aggregati di testi, caratteri e batch prima della chiamata. L'eventuale probe modello è dichiarato separatamente.

`preview-openrouter.json` è l'anteprima del default del progetto, non una prova remota eseguita né una conferma di scelta dell'utente. Contiene **4.125 byte di sorgenti**, 72 query, massimo 261 testi e 11.973 caratteri embedding per tre passaggi, più un eventuale probe `ping`; limite conservativo 253 richieste HTTP. Il mock ha usato 219 batch, 261 testi, 11.973 caratteri e zero HTTP, costo $0.

Per una prova OpenRouter serve `OPENROUTER_API_KEY` nell'ambiente o nel file locale `.anjawiki/.secrets.env` già previsto dal progetto; non inserirla nel dataset o in chat. Il runner carica quel file solo dopo aver validato la preview. OpenAI e Voyage sono supportati tramite i provider esistenti. La modalità locale richiede dipendenza e modello già presenti e forza `HF_HUB_OFFLINE=1`: non installa o scarica modelli automaticamente.

Il costo remoto è **non misurato**, non assunto zero. Il runner raccoglie i contatori usage/cost quando il provider li restituisce; se il campo costo manca, mantiene null. Il totale va confrontato con la fatturazione del provider. Non sono stati utilizzati prezzi storici presenti nei commenti del codice per formulare un preventivo.

## Esito e lavoro residuo

Benchmark ampliato e percorso di esecuzione reale pronti. La calibrazione semantica resta sospesa fino a disponibilità di provider e credenziali/modello locale: non si regolano soglie sulle distanze mock. Restano anche decisioni storiche e relazioni strutturali con provenienza. Nessun commit, tag, release, invio remoto o rebuild dell'indice utente.


Validazione finale del terzo incremento: **143 test passati in 25.05s, zero skip**. Sei nuove regressioni su preservazione del corpus, preview senza provider, digest errato, sorgenti cambiate, allowlist in uscita e budget aggregati. Benchmark standalone distinto: 648 valutazioni, zero riferimenti invalidi. Ruff, README/registry, release check e diff check verdi.
