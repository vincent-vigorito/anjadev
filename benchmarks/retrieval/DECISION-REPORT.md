# Q6 — confronto tra decisioni e codice attuale

8 settembre 2026. Aggiunto `code.compare_decision`, senza provider embedding o modifiche all'indice reale.

## Risultato disponibile

Il chiamante seleziona un commit Git completo, una decisione Markdown e i file da confrontare. Il tool restituisce separatamente differenze della decisione e dei sorgenti, hash SHA256, blob OID storico e hunk con riferimenti alle rispettive versioni. Mostra se la decisione è cambiata mentre il codice è rimasto uguale, oppure viceversa; non attribuisce automaticamente correttezza o violazione.

La baseline è dichiarata dal chiamante, non dedotta da una data o da un checkpoint embedding. Mancanza dello storico della decisione, oggetto non commit, sorgenti esclusi, symlink storici e cambiamenti della revisione attesa sono diagnosticati. Sono supportati i root di progetto annidati in un repository. Git replace objects non può alterare il contenuto attribuito al commit scelto.

File mancanti o esclusi nel working tree rimangono indisponibili; non si inferiscono rinomine o cancellazioni. Il testo dei diff condivide un budget; troncamenti e differenze di byte non visibili nella normalizzazione delle righe sono espliciti. Sono letture per file, non uno snapshot atomico del repository.

## Flussi end-to-end verificati

Lo script [check_decision_workflow.py](check_decision_workflow.py) usa tre copie temporanee delle fixture v2 e crea commit esclusivamente in quei repository temporanei. Non crea commit nel repository di sviluppo.

Per checkout, worker e cache:

1. Crea una baseline Git contenente decisione, codice e test.
2. Indicizza con mock locale, cerca il simbolo e verifica che siano reperibili implementazione e test.
3. Ispeziona il simbolo con la revisione ottenuta dalla ricerca e legge le asserzioni sintattiche.
4. Esegue un'asserzione del requisito nuovo, che fallisce prima della modifica prescritta nella fixture.
5. Modifica codice e test. Verifica indice stale e rifiuto della revisione precedente.
6. Esegue le funzioni test della fixture e la nuova asserzione con successo, quindi aggiorna l'indice e ricerca di nuovo.
7. Confronta decisione e file modificati contro il commit iniziale. La decisione resta invariata, codice e test risultano modificati, con diff entro 2000 caratteri.

**3/3 flussi riusciti**, zero chiamate outbound. [Risultati completi](decision-workflow-results.json). Le azioni sono prescritte e i test sono eseguiti realmente dal runner; non è una misura di programmazione autonoma. La presenza di asserzioni nell'ispezione resta distinta dai test passati nel workflow.

Riproduzione:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.12/bin/python3.12 benchmarks/retrieval/check_decision_workflow.py
```

Richiede Git e un interprete con sqlite-vec per il workflow di indicizzazione. `code.compare_decision` da solo non richiede sqlite-vec o credenziali embedding.

## Stato di Q6

Il percorso ricerca → evidenze → test → refresh → confronto storico è disponibile e verificato sulle fixture. Q6.4 è completato in questo perimetro. Il giudizio sul rispetto della decisione rimane `not_assessed`, così come la verifica generale delle affermazioni comportamentali. Il precedente fallimento del cutoff semantico non viene riclassificato come risolto: il gate di astensione resta aperto.

Nessun commit/tag/release del progetto, nessuna installazione e nessun invio remoto in questo incremento. L'aggiunta del tool richiede riavvio del server MCP quando verrà caricata la versione di sviluppo; non è stata modificata automaticamente la copia installata del plugin.


Verifica finale: **178 test passati in27.82s, zero skip**,15 nuove regressioni sul confronto storico. Smoke59/59 tool; Ruff, README/registry, release check e diff check verdi. I3 workflow standalone sono riportati separatamente dal conteggio pytest.
