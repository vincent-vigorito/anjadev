#!/usr/bin/env python3
"""
mcp_images_server.py — MCP server per generazione immagini.

Provider supportati (auto-routing in base alla key disponibile):
- xAI Grok Imagine     → https://api.x.ai/v1/images/generations
- OpenAI DALL-E        → https://api.openai.com/v1/images/generations

Tool esposti:
- image.generate(prompt, provider?, model?, size?, n?) → genera + salva PNG
- image.list(limit?) → lista immagini precedenti

Salvataggio: <ANJA_ROOT>/raw/images/<YYYY-MM-DD>/<slug>-<hex4>.png
Config via env: ANJA_ROOT (default cwd), API keys via os.environ.

Stdlib only (urllib + json + base64).
"""

import base64
import json
import os
import re
import secrets as _secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCOPE = os.environ.get("ANJA_SCOPE", "hub")
ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()


# =================================================================
# helpers
# =================================================================

def _images_dir() -> Path:
    """Directory dove salviamo le immagini generate."""
    if SCOPE == "project":
        base = ROOT / ".anjawiki" / "raw" / "images"
    else:  # hub, agent
        base = ROOT / "raw" / "images"
    today = datetime.now().strftime("%Y-%m-%d")
    d = base / today
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(s: str, max_len: int = 32) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:max_len] or "image")


def _provider_key(provider: str) -> Optional[str]:
    return {
        "xai":    os.environ.get("XAI_API_KEY"),
        "openai": os.environ.get("OPENAI_API_KEY"),
    }.get(provider)


def _auto_provider() -> Optional[str]:
    """Sceglie un provider in base alle key disponibili."""
    if os.environ.get("XAI_API_KEY"):
        return "xai"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


PROVIDER_DEFAULTS = {
    "xai": {
        "url": "https://api.x.ai/v1/images/generations",
        "model": "grok-imagine-image",
    },
    "openai": {
        "url": "https://api.openai.com/v1/images/generations",
        "model": "dall-e-3",
    },
}


# =================================================================
# image.generate
# =================================================================

def tool_image_generate(args: dict) -> dict:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt required"}
    if len(prompt) > 4000:
        return {"error": "prompt too long (max 4000 chars)"}

    provider = (args.get("provider") or "").strip().lower() or _auto_provider()
    if not provider:
        return {"error": "no image provider available — set XAI_API_KEY or OPENAI_API_KEY in Custom Secrets"}
    if provider not in PROVIDER_DEFAULTS:
        return {"error": f"unsupported provider '{provider}' (supported: xai, openai)"}

    api_key = _provider_key(provider)
    if not api_key:
        env_name = "XAI_API_KEY" if provider == "xai" else "OPENAI_API_KEY"
        return {"error": f"{env_name} not set in environment"}

    spec = PROVIDER_DEFAULTS[provider]
    model = (args.get("model") or "").strip() or spec["model"]
    n = max(1, min(int(args.get("n", 1)), 4))
    size = (args.get("size") or "").strip()  # "1024x1024" ecc.

    body = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "response_format": "b64_json",
    }
    if size:
        body["size"] = size

    req = urllib.request.Request(
        spec["url"],
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "anja-images/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = str(e)
        return {"error": f"{provider} HTTP {e.code}: {err_body}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    items = payload.get("data") or []
    if not items:
        return {"error": "no image data in response"}

    saved = []
    out_dir = _images_dir()
    slug = _slugify(prompt)
    for i, it in enumerate(items):
        b64 = it.get("b64_json")
        url = it.get("url")
        try:
            if b64:
                raw = base64.b64decode(b64)
            elif url:
                with urllib.request.urlopen(url, timeout=60) as r:
                    raw = r.read()
            else:
                continue
        except Exception as e:
            return {"error": f"image decode failed: {e}"}
        suffix = f"-{i}" if n > 1 else ""
        fname = f"{slug}{suffix}-{_secrets.token_hex(2)}.png"
        fpath = out_dir / fname
        fpath.write_bytes(raw)
        # Fase 23.c — web_url per inline rendering in chat
        today = datetime.now().strftime("%Y-%m-%d")
        saved.append({
            "path": str(fpath),
            "rel_path": str(fpath.relative_to(ROOT)),
            "web_url": f"/api/media/images/{today}/{fname}",
            "size_bytes": len(raw),
            "revised_prompt": it.get("revised_prompt"),
        })

    return {
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "count": len(saved),
        "images": saved,
    }


# =================================================================
# image.list
# =================================================================

def tool_image_list(args: dict) -> dict:
    limit = int(args.get("limit", 20))
    if SCOPE == "project":
        base = ROOT / ".anjawiki" / "raw" / "images"
    else:
        base = ROOT / "raw" / "images"
    if not base.is_dir():
        return {"images": []}
    items = []
    # walk date dirs reverse
    for date_dir in sorted(base.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("*.png"), reverse=True):
            try:
                items.append({
                    "path": str(f),
                    "rel_path": str(f.relative_to(ROOT)),
                    "date": date_dir.name,
                    "size_bytes": f.stat().st_size,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                })
                if len(items) >= limit:
                    break
            except Exception:
                continue
        if len(items) >= limit:
            break
    return {"images": items, "count": len(items)}


# =================================================================
# JSON-RPC dispatch (MCP protocol)
# =================================================================

TOOLS = [
    {
        "name": "image.generate",
        "description": "Genera una o più immagini da un prompt testuale. Default provider: auto (xai se XAI_API_KEY presente, altrimenti openai). Salva PNG in <hub>/raw/images/<date>/. Restituisce path file. Usa quando l'utente chiede di generare/creare/disegnare un'immagine.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt":   {"type": "string", "description": "Prompt testuale dell'immagine"},
                "provider": {"type": "string", "enum": ["xai", "openai", ""], "description": "Provider (auto se vuoto)"},
                "model":    {"type": "string", "description": "Override model (default: grok-imagine-image per xai, dall-e-3 per openai)"},
                "n":        {"type": "integer", "default": 1, "description": "Numero immagini (1-4)"},
                "size":     {"type": "string", "description": "Es. '1024x1024' (provider-dependent)"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "image.list",
        "description": "Lista immagini generate precedentemente, ordinate per data desc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
]

TOOL_HANDLERS = {
    "image.generate": tool_image_generate,
    "image.list": tool_image_list,
}


def handle_request(req: dict) -> Optional[dict]:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "anja_images", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown tool: {name}"}}
        try:
            result = handler(args)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": "error" in result},
        }
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle_request(req)
        if resp is not None:
            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
