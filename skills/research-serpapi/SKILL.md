---
name: research-serpapi
description: Ricerca web Google via SerpAPI (richiede SERPAPI_KEY in env o <hub>/.secrets.env). Quality più alta di DuckDuckGo per query specifiche, paid quota (free tier 100 req/mese). Da usare quando l'utente chiede ricerche precise con risultati Google, o quando research-duckduckgo non ritorna risultati sufficienti. Drop-in compatible con research-duckduckgo (stesso schema JSON output).
version: 1.0.0
category: research
tags: [web-search, research, google, serpapi, paid]
platforms: [macos, linux]
requires_tools: [Bash]
---

# Skill: research-serpapi

Ricerca Google via SerpAPI, quality alta. **Richiede API key** — opt-in alternativa a `research-duckduckgo`.

## Pre-condizioni

1. **Account SerpAPI**: registrati su https://serpapi.com (free tier: 100 req/mese)
2. **API key configurata**: env `SERPAPI_KEY=<your-key>` in uno dei modi:
   - **Via webapp Anja**: Settings → Custom Secrets → aggiungi `SERPAPI_KEY` con il valore. Viene salvato in `<hub>/.secrets.env`, ereditato automaticamente da subprocess MCP/skill.
   - **Manuale**: `export SERPAPI_KEY=<key>` nel tuo shell prima di lanciare l'agent
3. Tool `Bash` disponibile + Python 3.10+ (stdlib only).

Se la key manca, lo script ritorna errore esplicito con link al setup.

## Quando attivare

Stessi trigger di `research-duckduckgo`:
- "cerca info su X" / "trova news su Y" / "google Z"

Preferisci `research-serpapi` se:
- La query è molto specifica (es. "JAX vmap benchmark vs torch.func.vmap 2026")
- DDG ha ritornato 0-2 risultati troppo generici (fallback automatico)
- L'utente esplicitamente ha chiesto "usa Google"

Per uso quotidiano generico (~80% dei casi), `research-duckduckgo` basta ed è gratis.

## Workflow

### Step 1 — Esegui lo script

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/research-serpapi/scripts/serpapi_search.py" "<query>" [limit] [gl] [hl]
```

Parametri:
- `query`: stringa di ricerca (obbligatorio)
- `limit`: numero risultati (default 10, cap 20)
- `gl`: Google country (default `us`, es. `it` per Italia)
- `hl`: Google language (default `en`, es. `it` per italiano)

### Step 2 — Parse JSON

Output identico a `research-duckduckgo` (drop-in compatible):

**Successo**:
```json
{
  "query": "...",
  "count": 10,
  "results": [{"title", "url", "snippet"}, ...]
}
```

**Errore**:
```json
{"error": "SERPAPI_KEY missing. Configure in Settings..."}
```

### Step 3 — Sintesi con citazioni

Identico al pattern `research-duckduckgo`: cita con `[title](url)`, riporta snippet, sintetizza 3-5 risultati rilevanti.

## Confronto con research-duckduckgo

| Dimensione | duckduckgo | serpapi |
|---|---|---|
| API key | ❌ no | ✅ richiesta |
| Costo | gratis | free tier 100/mese, poi paid |
| Quality | media | alta (Google) |
| Region/lang | sì (kl, kp) | sì (gl, hl) |
| Setup | zero | 5 min (signup + key) |
| Rate limit | non dichiarato | esplicito per piano |
| Best for | uso quotidiano | query critiche/specifiche |

## Best practice

Stesse di `research-duckduckgo` + considerazioni quota:

1. **Quota awareness**: ogni call consuma 1 req del piano. Free tier = 100/mese → ~3 al giorno.
2. **Cache risultati**: se rifare la stessa query nello stesso giorno, leggi prima da memoria di sessione invece di ri-fetchare
3. **Combo strategico**: usa DDG per scoperta veloce, SerpAPI quando serve precisione
4. **Multi-locale**: `gl=it&hl=it` per risultati italiani specifici, `gl=us&hl=en` per global tech/research

## Anti-pattern

1. ❌ Usare SerpAPI per ogni ricerca quando DDG basta — spreco quota
2. ❌ Ignorare l'errore "SERPAPI_KEY missing" e procedere comunque — fail fast
3. ❌ Esporre la chiave nel log o nel risultato — vive solo in env

## Setup veloce

1. Vai su https://serpapi.com → signup
2. Dashboard → copia "Your Private API Key"
3. In webapp Anja: Settings → Custom Secrets → aggiungi `SERPAPI_KEY` = `<key>` → Save
4. Restart webapp (per ri-iniettare env nei subprocess MCP/skill)
5. Test: `skill.load("research-serpapi")` poi chiedi all'agent di cercare qualcosa
