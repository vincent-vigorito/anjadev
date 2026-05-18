#!/usr/bin/env python3
"""
mcp_videos_server.py — MCP server per generazione video (Fase 23).

Provider supportati (auto-routing in base alle key disponibili):
- xAI Grok Imagine Video → https://api.x.ai/v1/videos/generations (poll: /v1/videos/{id})
- OpenRouter Veo/Wan     → https://openrouter.ai/api/v1/videos    (poll: /api/v1/videos/{id})

Tool esposti:
- video.generate(prompt, provider?, model?, duration?, resolution?, aspect_ratio?, image_url?)
  → submit + poll fino a completion + download + salva mp4
- video.list(limit?)             → lista video precedenti
- video.status(job_id, provider) → check manuale di un job (se generate è andato in timeout)

Salvataggio: <ANJA_ROOT>/raw/videos/<YYYY-MM-DD>/<slug>-<hex4>.mp4
Config via env: ANJA_ROOT (default cwd), API keys via os.environ.

Stdlib only (urllib + json + base64 + time).

Pattern polling: max 5min, intervallo 5s (poi 10s dopo 30s). MCP tool call può
durare quel tempo perché il client (LLM) attende. Se eccede → ritorna job_id per
check manuale via video.status.
"""

import base64
import json
import os
import re
import secrets as _secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


SCOPE = os.environ.get("ANJA_SCOPE", "hub")
ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()


# =================================================================
# Helpers
# =================================================================

def _videos_dir() -> Path:
    if SCOPE == "project":
        base = ROOT / ".anjawiki" / "raw" / "videos"
    else:
        base = ROOT / "raw" / "videos"
    today = datetime.now().strftime("%Y-%m-%d")
    d = base / today
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(s: str, max_len: int = 32) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:max_len] or "video")


def _provider_key(provider: str) -> Optional[str]:
    return {
        "xai":        os.environ.get("XAI_API_KEY"),
        "openrouter": os.environ.get("OPENROUTER_API_KEY"),
    }.get(provider)


def _auto_provider() -> Optional[str]:
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"  # Veo/Wan più stabile + più scelta modelli
    if os.environ.get("XAI_API_KEY"):
        return "xai"
    return None


PROVIDER_DEFAULTS = {
    "xai": {
        "submit_url": "https://api.x.ai/v1/videos/generations",
        "poll_url_template": "https://api.x.ai/v1/videos/{job_id}",
        "model": "grok-imagine-video",
        "id_field": "request_id",
        "done_statuses": {"done", "completed", "success"},
        "failed_statuses": {"failed", "expired", "error"},
    },
    "openrouter": {
        "submit_url": "https://openrouter.ai/api/v1/videos",
        "poll_url_template": "https://openrouter.ai/api/v1/videos/{job_id}",
        "model": "google/veo-3.1-lite",  # Fase 23 — default budget-friendly $0.03-0.08/sec
        "id_field": "id",
        "done_statuses": {"completed", "done", "success"},
        "failed_statuses": {"failed", "error"},
    },
}


# =================================================================
# HTTP helpers
# =================================================================

def _http_post(url: str, body: dict, api_key: str, timeout: int = 60) -> tuple:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "anja-videos/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


def _http_get(url: str, api_key: Optional[str] = None, timeout: int = 30) -> tuple:
    headers = {"User-Agent": "anja-videos/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ct = resp.headers.get("Content-Type", "")
            if "application/json" in ct:
                return resp.status, json.loads(data.decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


# =================================================================
# Submit + Poll
# =================================================================

def _resolve_image_url(image_url: str) -> str:
    """F24.c — Risolve file:// path locali a data URL base64 per upstream API.

    Supportato: file:///abs/path/to/img.png → data:image/png;base64,<...>
    Pass-through: http(s):// e data: rimangono invariati.
    """
    if not image_url or not isinstance(image_url, str):
        return image_url
    if image_url.startswith("file://"):
        try:
            local_path = image_url[len("file://"):]
            p = Path(local_path)
            if not p.is_file():
                return image_url  # fallback, upstream darà errore
            raw = p.read_bytes()
            ext = p.suffix.lower().lstrip(".")
            mime = {
                "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp", "gif": "image/gif",
            }.get(ext, "image/png")
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception:
            return image_url
    return image_url


def _build_body(provider: str, args: dict, model: str) -> dict:
    prompt = args["prompt"]
    body: dict = {"model": model, "prompt": prompt}
    duration = args.get("duration")
    resolution = args.get("resolution")
    aspect_ratio = args.get("aspect_ratio")
    image_url = _resolve_image_url(args.get("image_url"))

    if provider == "openrouter":
        if resolution:
            body["resolution"] = str(resolution)
        if aspect_ratio:
            body["aspect_ratio"] = str(aspect_ratio)
        if duration:
            body["duration"] = int(duration)
        if image_url:
            body["frame_images"] = [{"url": image_url, "position": "first"}]
        if args.get("generate_audio") is not None:
            body["generate_audio"] = bool(args["generate_audio"])
    elif provider == "xai":
        if duration:
            body["duration"] = int(duration)
        if resolution:
            body["resolution"] = str(resolution)
        if aspect_ratio:
            body["aspect_ratio"] = str(aspect_ratio)
        if image_url:
            body["image"] = {"url": image_url}
    return body


def _extract_job_id(payload: dict, id_field: str) -> Optional[str]:
    return payload.get(id_field) or payload.get("id") or payload.get("request_id")


def _is_done(status: str, done_set: set) -> bool:
    return (status or "").lower() in done_set


def _is_failed(status: str, failed_set: set) -> bool:
    return (status or "").lower() in failed_set


def _extract_video_url(payload: dict, provider: str) -> Optional[str]:
    # OpenRouter: unsigned_urls[0]
    urls = payload.get("unsigned_urls") or payload.get("urls") or []
    if urls:
        return urls[0] if isinstance(urls[0], str) else urls[0].get("url")
    # Fallback fields
    for key in ("video_url", "url", "output_url", "result_url"):
        v = payload.get(key)
        if isinstance(v, str):
            return v
    # xAI alternative: nested
    result = payload.get("result") or {}
    if isinstance(result, dict):
        for key in ("video_url", "url"):
            v = result.get(key)
            if isinstance(v, str):
                return v
    return None


def _poll_until_done(provider: str, job_id: str, api_key: str,
                     max_wait_sec: int = 300,
                     initial_interval: int = 5,
                     long_interval: int = 10) -> dict:
    spec = PROVIDER_DEFAULTS[provider]
    url = spec["poll_url_template"].format(job_id=job_id)
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > max_wait_sec:
            return {"error": "timeout", "elapsed_sec": int(elapsed), "job_id": job_id}
        code, payload = _http_get(url, api_key=api_key, timeout=30)
        if code and 200 <= code < 300 and isinstance(payload, dict):
            status = payload.get("status") or ""
            if _is_done(status, spec["done_statuses"]):
                return {"done": True, "payload": payload, "elapsed_sec": int(elapsed)}
            if _is_failed(status, spec["failed_statuses"]):
                return {"error": "job_failed", "payload": payload, "elapsed_sec": int(elapsed)}
        # Backoff
        interval = initial_interval if elapsed < 30 else long_interval
        time.sleep(interval)


def _download_video(url: str, out_path: Path, api_key: Optional[str] = None) -> tuple:
    """Download mp4 da URL. Ritorna (bytes_written, error_str|None).

    Nota: OpenRouter `unsigned_urls` richiedono Authorization Bearer nonostante il nome.
    Passa api_key per autenticare richieste a provider che lo richiedono.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        out_path.write_bytes(data)
        return len(data), None
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


# =================================================================
# Tools
# =================================================================

def tool_video_generate(args: dict) -> dict:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt required"}
    if len(prompt) > 4000:
        return {"error": "prompt too long (max 4000 chars)"}

    provider = (args.get("provider") or "").strip().lower() or _auto_provider()
    if not provider:
        return {"error": "no video provider available — set XAI_API_KEY or OPENROUTER_API_KEY"}
    if provider not in PROVIDER_DEFAULTS:
        return {"error": f"unsupported provider '{provider}' (supported: {list(PROVIDER_DEFAULTS.keys())})"}

    api_key = _provider_key(provider)
    if not api_key:
        env_name = "XAI_API_KEY" if provider == "xai" else "OPENROUTER_API_KEY"
        return {"error": f"{env_name} not set in environment"}

    spec = PROVIDER_DEFAULTS[provider]
    model = (args.get("model") or "").strip() or spec["model"]

    # max wait override (default 90s — MCP client SDK timeout tipicamente 60-120s, quindi
    # cappiamo qui per evitare hang silenzioso. Se eccede, ritorna job_id pending per check
    # successivo via video.status).
    max_wait = min(int(args.get("max_wait_sec", 90)), 300)

    # Submit
    body = _build_body(provider, {**args, "prompt": prompt}, model)
    code, payload = _http_post(spec["submit_url"], body, api_key, timeout=60)
    if not code or code >= 400:
        return {"error": f"{provider} submit HTTP {code}: {payload}"}
    job_id = _extract_job_id(payload, spec["id_field"])
    if not job_id:
        return {"error": f"no job_id in submit response: {payload}"}

    # Fase 23-fix: persist job_id su disk IMMEDIATAMENTE dopo submit.
    # Se Anja timeout SDK durante polling, il job_id NON è perso — possiamo
    # recuperare via video.list_pending o tail del file.
    try:
        log_dir = _videos_dir()
        pending_log = log_dir / ".pending_jobs.jsonl"
        with pending_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(),
                "provider": provider,
                "model": model,
                "job_id": job_id,
                "prompt": prompt[:200],
                "duration": args.get("duration"),
                "resolution": args.get("resolution"),
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # Poll
    poll_res = _poll_until_done(provider, job_id, api_key, max_wait_sec=max_wait)
    if poll_res.get("error") == "timeout":
        return {
            "status": "pending",
            "job_id": job_id,
            "provider": provider,
            "model": model,
            "elapsed_sec": poll_res["elapsed_sec"],
            "hint": f"Polling timed out. Use video.status(job_id='{job_id}', provider='{provider}') to check later.",
        }
    if poll_res.get("error"):
        return {
            "error": poll_res.get("error"),
            "job_id": job_id,
            "provider": provider,
            "payload": poll_res.get("payload"),
        }

    final = poll_res["payload"]
    video_url = _extract_video_url(final, provider)
    if not video_url:
        return {"error": "no video URL in completed response", "payload": final, "job_id": job_id}

    # Download
    slug = _slugify(prompt)
    out_dir = _videos_dir()
    fname = f"{slug}-{_secrets.token_hex(2)}.mp4"
    fpath = out_dir / fname
    n_bytes, err = _download_video(video_url, fpath, api_key=api_key)
    if err:
        return {"error": f"download failed: {err}", "video_url": video_url, "job_id": job_id}

    usage = final.get("usage") or {}
    # Fase 23.c — web_url per inline rendering in chat
    today = datetime.now().strftime("%Y-%m-%d")
    web_url = f"/api/media/videos/{today}/{fname}"
    return {
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "job_id": job_id,
        "path": str(fpath),
        "rel_path": str(fpath.relative_to(ROOT)),
        "web_url": web_url,
        "size_bytes": n_bytes,
        "video_url": video_url,
        "elapsed_sec": poll_res["elapsed_sec"],
        "usage": usage,
    }


def tool_video_status(args: dict) -> dict:
    """Check manuale di un job_id già submitted."""
    job_id = (args.get("job_id") or "").strip()
    provider = (args.get("provider") or "").strip().lower()
    if not job_id or not provider:
        return {"error": "job_id and provider required"}
    if provider not in PROVIDER_DEFAULTS:
        return {"error": f"unsupported provider '{provider}'"}
    api_key = _provider_key(provider)
    if not api_key:
        return {"error": f"API key for {provider} not set"}
    spec = PROVIDER_DEFAULTS[provider]
    url = spec["poll_url_template"].format(job_id=job_id)
    code, payload = _http_get(url, api_key=api_key, timeout=30)
    if not code or code >= 400:
        return {"error": f"HTTP {code}: {payload}"}
    status = payload.get("status", "?")
    out = {"job_id": job_id, "provider": provider, "status": status, "payload": payload}
    if _is_done(status, spec["done_statuses"]):
        url = _extract_video_url(payload, provider)
        if url:
            out["video_url"] = url
            # Auto-download
            slug = f"job-{job_id[:8]}"
            out_dir = _videos_dir()
            fname = f"{slug}-{_secrets.token_hex(2)}.mp4"
            fpath = out_dir / fname
            n_bytes, err = _download_video(url, fpath, api_key=api_key)
            if not err:
                out["path"] = str(fpath)
                out["rel_path"] = str(fpath.relative_to(ROOT))
                out["size_bytes"] = n_bytes
                today = datetime.now().strftime("%Y-%m-%d")
                out["web_url"] = f"/api/media/videos/{today}/{fname}"
    return out


def tool_video_list_models(args: dict) -> dict:
    """Fetch live dei video models disponibili. Filtrabile per provider."""
    provider_filter = (args.get("provider") or "").strip().lower()
    out = {"models": []}

    # OpenRouter — dynamic list via /api/v1/videos/models (no auth needed)
    if not provider_filter or provider_filter == "openrouter":
        code, payload = _http_get("https://openrouter.ai/api/v1/videos/models", timeout=15)
        if code and 200 <= code < 300 and isinstance(payload, dict):
            data = payload.get("data") or payload.get("models") or []
            for m in data:
                if not isinstance(m, dict):
                    continue
                durations = m.get("supported_durations") or []
                max_dur = max(durations) if durations else None
                out["models"].append({
                    "provider": "openrouter",
                    "slug": m.get("id"),
                    "name": m.get("name"),
                    "max_duration_sec": max_dur,
                    "supported_durations": durations,
                    "resolutions": m.get("supported_resolutions") or [],
                    "aspect_ratios": m.get("supported_aspect_ratios") or [],
                    "supported_sizes": m.get("supported_sizes") or [],
                    "audio_supported": bool(m.get("generate_audio")),
                    "first_last_frame": m.get("supported_frame_images") or [],
                    "pricing_skus": m.get("pricing_skus") or {},
                    "passthrough_params": m.get("allowed_passthrough_parameters") or [],
                    "description": (m.get("description") or "")[:300],
                })
        else:
            out["openrouter_error"] = payload if not code else f"HTTP {code}"

    # xAI — currently no public /models endpoint for videos (al 2026-05-14)
    if not provider_filter or provider_filter == "xai":
        if os.environ.get("XAI_API_KEY"):
            out["models"].append({
                "provider": "xai",
                "slug": "grok-imagine-video",
                "name": "xAI Grok Imagine Video",
                "max_duration_sec": 15,
                "resolutions": ["720p", "1080p"],
                "aspect_ratios": ["16:9", "9:16", "1:1"],
                "audio_supported": False,
                "pricing": "per-second (vedi docs.x.ai)",
                "note": "Static entry — xAI non espone /models endpoint pubblico per video",
            })

    out["count"] = len(out["models"])
    return out


def tool_video_list_pending(args: dict) -> dict:
    """Legge `.pending_jobs.jsonl` con job_id submitted ma non confermati come downloaded.

    USE FOR: recovery dopo SDK timeout. 'che video sono in coda?', 'controlla i job pendenti'.
    Per ogni entry fa video.status check live.
    """
    if SCOPE == "project":
        base = ROOT / ".anjawiki" / "raw" / "videos"
    else:
        base = ROOT / "raw" / "videos"
    if not base.is_dir():
        return {"jobs": []}
    pending_files = list(base.rglob(".pending_jobs.jsonl"))
    jobs = []
    seen = set()
    for pf in pending_files:
        try:
            for line in pf.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                key = entry.get("job_id")
                if not key or key in seen:
                    continue
                seen.add(key)
                # Live status check
                provider = entry.get("provider", "openrouter")
                spec = PROVIDER_DEFAULTS.get(provider)
                if spec:
                    api_key = _provider_key(provider)
                    if api_key:
                        url = spec["poll_url_template"].format(job_id=key)
                        code, payload = _http_get(url, api_key=api_key, timeout=10)
                        if code == 200 and isinstance(payload, dict):
                            entry["live_status"] = payload.get("status", "?")
                            video_url = _extract_video_url(payload, provider)
                            if video_url:
                                entry["video_url"] = video_url
                jobs.append(entry)
        except Exception:
            continue
    jobs.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return {"jobs": jobs, "count": len(jobs)}


def tool_video_list(args: dict) -> dict:
    limit = int(args.get("limit", 20))
    if SCOPE == "project":
        base = ROOT / ".anjawiki" / "raw" / "videos"
    else:
        base = ROOT / "raw" / "videos"
    if not base.is_dir():
        return {"videos": []}
    items: list = []
    for date_dir in sorted(base.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.iterdir(), reverse=True):
            if f.is_file() and f.suffix.lower() in (".mp4", ".mov", ".webm"):
                items.append({
                    "name": f.name,
                    "path": str(f),
                    "rel_path": str(f.relative_to(ROOT)),
                    "size_bytes": f.stat().st_size,
                    "date": date_dir.name,
                })
                if len(items) >= limit:
                    break
        if len(items) >= limit:
            break
    return {"videos": items}


# =================================================================
# JSON-RPC dispatcher (MCP server stdio)
# =================================================================

TOOLS = [
    {
        "name": "video.generate",
        "description": (
            "🎬 Genera un video (mp4) da prompt testuale. ASYNC con polling (max 90s default).\n"
            "IMPORTANTE: se la response ha `status='pending'` + `job_id`, NON è errore — significa che il video sta ancora generando. "
            "Devi DIRE all'utente che è in coda, dare il job_id, e fare poi una chiamata `video.status(job_id, provider)` "
            "dopo ~30-60s per recuperare il path quando pronto. NON ri-chiamare video.generate (sprecano soldi).\n"
            "USE FOR: 'genera video di X', 'crea clip', 'animazione', 'short film'.\n"
            "Provider auto: OpenRouter (priorità) o xAI Grok Imagine Video. Override via `provider`.\n"
            "\n"
            "MODELLI disponibili (~13 OpenRouter + 1 xAI): chiama `video.list_models` per lista fresca.\n"
            "Default OpenRouter: `google/veo-3.1-lite` (best value $0.03-0.08/sec, 1080p, 8s).\n"
            "Top quality: `google/veo-3.1` o `openai/sora-2-pro` ($0.20-0.60/sec).\n"
            "Economici token-based: `bytedance/seedance-2.0-fast`, `bytedance/seedance-1-5-pro`.\n"
            "\n"
            "ATTENZIONE: NON confondere `bytedance/seedance-*` (VIDEO) con `bytedance-seed/seedream-*` (IMAGE, no video).\n"
            "image_url opzionale per image-to-video (primo frame).\n"
            "duration tipicamente 5-15 sec, resolution 720p|1080p, aspect_ratio '16:9'|'9:16'|'1:1'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "provider": {"type": "string", "enum": ["xai", "openrouter"]},
                "model": {"type": "string", "description": "Override default model (es. google/veo-3.1, alibaba/wan-2.7)"},
                "duration": {"type": "integer", "description": "Secondi (xAI: 1-15, OR: dipende dal modello)"},
                "resolution": {"type": "string", "description": "'480p'|'720p'|'1080p'|'4K'"},
                "aspect_ratio": {"type": "string", "description": "'16:9'|'9:16'|'1:1'|'4:3'|'3:4'|'21:9'"},
                "image_url": {"type": "string", "description": "Optional: image-to-video, URL del primo frame. Supportato: http(s)://, data:, file:///abs/path (F24.c — file:// viene auto-convertito a data URL per upstream)."},
                "generate_audio": {"type": "boolean", "description": "(OpenRouter only) Include audio. Default true."},
                "max_wait_sec": {"type": "integer", "description": "Max polling sec (default 300, max 600). Se eccede ritorna job_id per check manuale."},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "video.list_models",
        "description": (
            "🎬 Fetch LIVE della lista video models disponibili da OpenRouter (`/api/v1/videos/models`) "
            "+ entry statica xAI. USE FOR quando user chiede 'che video modelli ho?', 'lista modelli video', "
            "'qual è il più economico?'. Ritorna slug, max_duration, resolutions, aspect_ratios, pricing per ogni modello. "
            "Sempre fresco — niente hardcode stale."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["openrouter", "xai"], "description": "Filtra per provider. Omit = tutti."},
            },
        },
    },
    {
        "name": "video.status",
        "description": (
            "🎬 Check status di un video job pendente. USE FOR: 'è pronto il video?' dopo "
            "che video.generate ha ritornato status='pending' con job_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "provider": {"type": "string", "enum": ["xai", "openrouter"]},
            },
            "required": ["job_id", "provider"],
        },
    },
    {
        "name": "video.list_pending",
        "description": (
            "🎬 Lista job video submitted recentemente con check live dello status. "
            "USE FOR: recovery dopo crash/timeout client, 'che video stanno generando?', 'controlla coda'. "
            "Legge `.pending_jobs.jsonl` (persistito al submit) + fa GET live di ogni job. "
            "Risolve il problema dei job_id orfani persi al timeout SDK."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "video.list",
        "description": (
            "🎬 Lista video precedenti salvati in <root>/raw/videos/. USE FOR: 'che video ho generato?'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
            },
        },
    },
]

TOOL_HANDLERS = {
    "video.generate":     tool_video_generate,
    "video.list_models":  tool_video_list_models,
    "video.status":       tool_video_status,
    "video.list":         tool_video_list,
    "video.list_pending": tool_video_list_pending,
}


def handle_request(req: dict) -> dict:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "anja_videos", "version": "1.0"},
            },
        }
    if method == "notifications/initialized":
        return None  # notification, no response
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown tool: {name}"}}
        try:
            result = handler(args)
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
            }
        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}


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
