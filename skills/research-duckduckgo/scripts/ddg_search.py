#!/usr/bin/env python3
"""ddg_search.py — DuckDuckGo HTML scrape (no API key, no deps).

Usage:
    python3 ddg_search.py "query string" [limit=10]

Output: JSON array [{title, url, snippet}].
On error: JSON object {"error": "..."} (always parseable).

Backend: https://html.duckduckgo.com/html/?q=... (DDG HTML endpoint,
stabile da anni, no rate limit dichiarato, rispetta User-Agent + delay
ragionevole tra richieste).

Stdlib only.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
import urllib.request
from typing import Optional


USER_AGENT = "Mozilla/5.0 (compatible; anja-research/1.0; +https://github.com/vincent-vigorito/anjadev)"
ENDPOINT = "https://html.duckduckgo.com/html/"
TIMEOUT_SEC = 20


def _strip_html(s: str) -> str:
    """Remove tags + decode entities."""
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def _resolve_ddg_redirect(url: str) -> str:
    """DDG HTML wrappa URL in /l/?uddg=<encoded>. Estrae l'URL reale."""
    if "/l/?" not in url and "uddg=" not in url:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        real = qs.get("uddg", [None])[0]
        if real:
            return urllib.parse.unquote(real)
    except Exception:
        pass
    return url


def search(query: str, limit: int = 10, region: str = "wt-wt", safesearch: str = "moderate") -> list:
    """Esegui search DDG. Ritorna lista di dict {title, url, snippet}.

    region: 'wt-wt' (no region) | 'it-it' | 'us-en' | ...
    safesearch: 'strict' | 'moderate' | 'off'
    """
    if not query or not query.strip():
        return []
    params = {
        "q": query.strip(),
        "kl": region,
        "kp": {"strict": "1", "moderate": "-1", "off": "-2"}.get(safesearch, "-1"),
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
        body = r.read().decode("utf-8", errors="replace")

    # Parse: pattern stabile di DDG HTML (verificato 2026-05-23).
    # Ogni risultato ha:
    #   <a ... class="result__a" href="<url>">title</a>     (con class può avere altre)
    #   ... (markup intermedio) ...
    #   <a ... class="result__snippet" ...>snippet</a>
    # I 2 link sono separati da markup ma sempre nello stesso "result" — cerco
    # entrambi sequenzialmente e li pairo per indice.
    results = []
    title_pattern = re.compile(
        r'<a[^>]*\bclass="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*\bclass="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    titles = list(title_pattern.finditer(body))
    snippets = list(snippet_pattern.finditer(body))
    for i, t_m in enumerate(titles):
        if len(results) >= limit:
            break
        raw_url = html.unescape(t_m.group(1))
        # DDG ritorna //duckduckgo.com/l/?uddg=... (no scheme), normalize prima del resolve
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        title = _strip_html(t_m.group(2))
        clean_url = _resolve_ddg_redirect(raw_url)
        # Pairing snippet: il primo snippet AFTER la posizione di questo title
        snippet = ""
        for s_m in snippets:
            if s_m.start() > t_m.end():
                snippet = _strip_html(s_m.group(1))
                break
        if not title or not clean_url:
            continue
        # Skip DDG internal links
        if "duckduckgo.com" in clean_url and "/l/?" in clean_url:
            continue
        results.append({"title": title, "url": clean_url, "snippet": snippet})
    return results


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: ddg_search.py <query> [limit] [region] [safesearch]"}))
        sys.exit(2)
    query = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    region = sys.argv[3] if len(sys.argv) > 3 else "wt-wt"
    safesearch = sys.argv[4] if len(sys.argv) > 4 else "moderate"
    try:
        results = search(query, limit=limit, region=region, safesearch=safesearch)
        print(json.dumps({"query": query, "count": len(results), "results": results},
                          ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": f"HTTP {e.code}: {e.reason}"}))
        sys.exit(1)
    except urllib.error.URLError as e:
        print(json.dumps({"error": f"network error: {e.reason}"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
