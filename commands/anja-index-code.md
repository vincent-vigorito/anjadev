---
description: Build, refresh o anteprima del vector index del codebase
argument-hint: [--dry-run] [--force] [--limit N]
allowed-tools: mcp__anja_memory__code_reindex, mcp__anja_memory__code_status, mcp__anja_memory__wiki_log_append
---

# /anja-index-code

Aggiorna `.anjawiki/code-index.db` confrontando gli hash del working tree, compresi file staged/untracked ammessi, rename e delete. `--force` ricostruisce mantenendo l'indice valido fino al commit.

Argomenti: `$ARGUMENTS`

1. Chiama `code.status` per root, provider, compatibilità, capacità e job pendenti. Non mostrare credenziali.
2. Chiama `code.reindex` con `dry_run: true`: mostra file ammessi/esclusi, motivi, byte sorgente, destinazione e possibile migrazione coordinata codice/wiki. La lista indica i file eleggibili; quelli invariati possono non richiedere embedding.
3. Se richiesto `--dry-run`, termina qui. Se l'invio remoto non è autorizzato nella policy, indica `.anjawiki/index-policy.json` e `/anja-config`: la presenza di una API key non costituisce autorizzazione all'invio. Non abilitare un provider a nome dell'utente.
4. Se l'operazione è già autorizzata, chiama `code.reindex` con `force` e `limit` richiesti, senza ripetere conferme. Un cambio fingerprint richiede rebuild completo codice/wiki e rifiuta `limit`.
5. Riporta stato effettivo, file/chunk pubblicati, esclusioni e problemi. `partial`, `stale` e `failed` non sono un completamento globale. In caso di errore, conserva e mostra il codice diagnostico e il riferimento al backup se presente.
6. Registra un log `refresh` soltanto dopo una pubblicazione riuscita, distinguendo completo/parziale.

Le regole sono condivise fra preview, full, incremental e job wiki: default di sicurezza, `.gitignore` nativo Git, `.anjaignore` e policy esplicita. Senza Git e con regole ignore da applicare l'operazione fallisce chiusa, senza inviare file potenzialmente esclusi.

L'indice è ricostruibile. Prima delle migrazioni viene creato uno snapshot verificabile di wiki/roadmap/configurazione/manifesti. Per diagnosi e recupero consultare SCHEMA.md. Tempi, dimensione dell'indice e qualità semantica vanno misurati sul progetto; `limit` è uno strumento di prova parziale.
