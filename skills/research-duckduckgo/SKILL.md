---
name: research-duckduckgo
description: Ricerca web tramite DuckDuckGo (no API key, no setup). Da usare quando l'utente chiede "cerca info su X", "trova news di Y", "google Z", "cosa dicono online di W", o quando l'agent ha bisogno di context fresco dal web per ragionare. Output JSON strutturato {title, url, snippet} pronto da sintetizzare in risposta con citazioni.
version: 1.0.0
category: research
tags: [web-search, research, duckduckgo, free, no-api-key]
platforms: [macos, linux]
requires_tools: [Bash]
---

# Skill: research-duckduckgo

Ricerca web minimale, **zero setup**, privacy-friendly. Backend: DuckDuckGo HTML scrape (rispetta robots, User-Agent identificato, no rate limit dichiarato per uso ragionevole).

## Quando attivare

Sì:
- "cerca info su <topic>" / "trova qualcosa su <X>"
- "ultimi <topic>" / "news su <X>"
- "google <query>" / "cerca online <Y>"
- "cosa dicono di <topic>" / "letteratura recente su <Z>"
- l'agent (es. paper-scout, research analyst) ha bisogno di web context per ragionare

No (usa altri tool):
- ricerca dentro il wiki di progetto → `wiki.search` / `wiki.search_semantic`
- ricerca nel codebase → `code.search`
- query strutturata su API specifica (arxiv, github) → tool dedicato se presente

## Pre-condizioni

- Tool `Bash` disponibile per eseguire lo script Python
- Connettività internet (no fallback offline — se DDG unreachable, ritorna errore esplicito)
- Python 3.10+ (stdlib only, no install)

## Workflow

### Step 1 — Esegui lo script

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/research-duckduckgo/scripts/ddg_search.py" "<query>" [limit] [region] [safesearch]
```

Parametri:
- `query`: stringa di ricerca (obbligatorio). Massimo ~200 char.
- `limit`: numero risultati (default 10, max ragionevole 20)
- `region`: `wt-wt` (default, no region) | `it-it` | `us-en` | `fr-fr` | ...
- `safesearch`: `strict` | `moderate` (default) | `off`

### Step 2 — Parse JSON

Output è SEMPRE JSON parseable:

**Caso successo**:
```json
{
  "query": "claude code plugins",
  "count": 3,
  "results": [
    {"title": "...", "url": "https://...", "snippet": "..."},
    ...
  ]
}
```

**Caso errore** (network, HTTP, parse fail):
```json
{"error": "HTTP 503: Service Unavailable"}
```

### Step 3 — Sintesi con citazioni

Per ogni risposta all'utente:
1. Cita la fonte usando il `url` come link markdown: `[title](url)`
2. Riporta lo `snippet` come contesto
3. Aggrega 3-5 risultati rilevanti, non riportare tutto crudo
4. Distingui chiaramente fatti dichiarati nei risultati vs tue inferenze

Esempio sintesi:
> Secondo [Anthropic](https://claude.com/product/claude-code), Claude Code è il loro agentic coding tool per developers. Esiste anche una [directory community](https://claudecodexplugins.com/) che cataloga i plugin più popolari ranked per install count.

## Best practice

1. **Query breve e specifica**: "transformer architecture survey 2026" > "transformers"
2. **Lingua coerente**: se utente parla italiano, query in italiano + `region=it-it` per risultati locali rilevanti
3. **Re-query se 0 results**: prova varianti (synonyms, query più ampia)
4. **No scraping aggressivo**: massimo 3-5 query per turno utente, non in loop infinito
5. **WebFetch per approfondire**: se uno snippet ti interessa ma è troncato, usa `WebFetch` sul `url` per scaricare l'articolo intero

## Anti-pattern

1. ❌ Loop infinito di ricerche per "trovare la risposta perfetta" — 2-3 query bastano
2. ❌ Copia/incolla risultati senza sintetizzare — l'utente vuole sintesi
3. ❌ Inventare URL non presenti nei risultati — cita SOLO URL reali ritornati
4. ❌ Saltare la citazione — ogni fact dal web va con [link]
5. ❌ Fare ricerche su info che già hai nel wiki — controlla prima `wiki.search`

## Output format strutturato (per uso programmatico)

Se chiamato da un agent o routine che vuole risultati raw, parsa il JSON con:
```python
import json, subprocess
r = subprocess.run(["python3", "<path>/ddg_search.py", "query", "10"],
                   capture_output=True, text=True)
data = json.loads(r.stdout)
for item in data.get("results", []):
    print(item["title"], item["url"])
```

## Quando NON funziona

- **DDG unreachable / 503**: rete giù o DDG ban temporaneo. Fallback: prova SerpAPI se configurato (`skill.load("research-serpapi")`).
- **HTML structure changed**: se DDG cambia il template HTML, il parser regex può smettere di matchare. In quel caso: aggiornare regex in `ddg_search.py` (commit suggestion al plugin).
- **Risultati irrelevant**: probabilmente query troppo generica. Reformula.

## Provider alternativi nel catalog

- `research-serpapi` — Google via SerpAPI (richiede SERPAPI_KEY, paid). Quality più alta per query specifiche, paid quota.
- `research-arxiv` (futuro) — arxiv API dedicato per paper academic
- `research-github` (futuro) — GitHub API per repo/issue/code search
