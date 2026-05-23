#!/usr/bin/env python3
"""serpapi_search.py — Google search via SerpAPI (https://serpapi.com).

Richiede env SERPAPI_KEY. Se mancante, ritorna errore esplicito con
istruzioni per la configurazione.

Usage:
    python3 serpapi_search.py "query" [limit=10] [gl=us] [hl=en]

Output: JSON identico a ddg_search.py (drop-in compatible).
  {"query", "count", "results": [{title, url, snippet}]}
  oppure {"error": "..."}

Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


ENDPOINT = "https://serpapi.com/search"
TIMEOUT_SEC = 25


def search(query: str, limit: int = 10, gl: str = "us", hl: str = "en") -> dict:
    """Search Google via SerpAPI. Ritorna dict {results, ...} o {error}."""
    api_key = os.environ.get("SERPAPI_KEY") or os.environ.get("SERP_API_KEY")
    if not api_key:
        return {
            "error": (
                "SERPAPI_KEY missing. Configure in Settings → Secrets della "
                "webapp Anja (verrà salvata in <hub>/.secrets.env) oppure "
                "exporta nell'env: export SERPAPI_KEY=<your-key>. "
                "Get a key at https://serpapi.com (free tier 100 req/month)."
            )
        }
    if not query or not query.strip():
        return {"error": "query required"}

    params = {
        "engine": "google",
        "q": query.strip(),
        "api_key": api_key,
        "num": min(max(int(limit), 1), 20),
        "gl": gl,
        "hl": hl,
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SEC) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        return {"error": f"HTTP {e.code}: {e.reason}. {body}"}
    except urllib.error.URLError as e:
        return {"error": f"network error: {e.reason}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    # SerpAPI response: {organic_results: [{title, link, snippet, position, ...}], ...}
    organic = data.get("organic_results") or []
    results = []
    for item in organic[:limit]:
        url_field = item.get("link") or item.get("url") or ""
        results.append({
            "title": item.get("title", ""),
            "url": url_field,
            "snippet": item.get("snippet", ""),
        })

    return {"query": query, "count": len(results), "results": results}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: serpapi_search.py <query> [limit] [gl] [hl]"}))
        sys.exit(2)
    query = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    gl = sys.argv[3] if len(sys.argv) > 3 else "us"
    hl = sys.argv[4] if len(sys.argv) > 4 else "en"
    result = search(query, limit=limit, gl=gl, hl=hl)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
