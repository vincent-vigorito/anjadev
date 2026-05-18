#!/usr/bin/env python3
"""embed_providers.py — interfaccia pluggable per embedding providers.

Provider supportati:
  - openrouter (default): proxy multi-provider, 1 API key per OpenAI/Voyage/Cohere/...
  - voyage:               diretto Voyage AI (qualità top su codice, `voyage-code-3`)
  - openai:               diretto OpenAI (`text-embedding-3-small`)
  - local:                sentence-transformers offline (BGE-small, opt-in heavy deps)
  - none:                 disabilita semantic search (solo ripgrep)

Selezione via env ANJA_EMBED_PROVIDER. API key da env provider-specific o fallback
ANJA_EMBED_API_KEY. Model override via ANJA_EMBED_MODEL.

Stdlib + `httpx` per API providers. Sentence-transformers solo se `local`.
"""

import json
import os
import sys
from typing import Optional


# ============================================================
# ABC
# ============================================================

class EmbedProvider:
    """Interfaccia comune embedding provider."""

    name: str = ""
    dim: int = 0
    model: str = ""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embedding di N testi. Restituisce list di vettori float32."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} model={self.model} dim={self.dim}>"


# ============================================================
# API providers (HTTP)
# ============================================================

def _http_post_json(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    """POST JSON via httpx (lazy import). Restituisce dict response."""
    try:
        import httpx  # noqa
    except ImportError:
        raise RuntimeError("httpx required for API embedding providers. Install: pip install httpx")
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


class OpenRouterProvider(EmbedProvider):
    """OpenRouter — proxy multi-provider. 1 key per accesso a OpenAI/Qwen/Cohere/Voyage.

    Modelli noti (dim hard-coded):
      - openai/text-embedding-3-small (default, $0.02/1M, dim 1536)
      - openai/text-embedding-3-large ($0.13/1M, dim 3072)
      - openai/text-embedding-ada-002 (legacy, dim 1536)
      - qwen/qwen3-embedding-8b (top MTEB, dim 4096)
      - qwen/qwen3-embedding-4b (dim 2560)
      - qwen/qwen3-embedding-0.6b (dim 1024)
      - cohere/embed-english-v3.0 (dim 1024)

    Per modelli non noti: probe automatico via HTTP test ("ping") al primo init,
    poi cache locale. ANJA_EMBED_DIM env override come fallback ultimo.
    """
    name = "openrouter"

    _KNOWN_DIMS = {
        "openai/text-embedding-3-small": 1536,
        "openai/text-embedding-3-large": 3072,
        "openai/text-embedding-ada-002": 1536,
        "qwen/qwen3-embedding-8b": 4096,
        "qwen/qwen3-embedding-4b": 2560,
        "qwen/qwen3-embedding-0.6b": 1024,
        "cohere/embed-english-v3.0": 1024,
        "cohere/embed-multilingual-v3.0": 1024,
    }

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or os.environ.get("ANJA_EMBED_MODEL", "openai/text-embedding-3-small")
        self.url = "https://openrouter.ai/api/v1/embeddings"
        # Determine dim: dict noto → env override → HTTP probe
        if self.model in self._KNOWN_DIMS:
            self.dim = self._KNOWN_DIMS[self.model]
        else:
            env_dim = os.environ.get("ANJA_EMBED_DIM")
            if env_dim and env_dim.isdigit():
                self.dim = int(env_dim)
            else:
                self.dim = self._probe_dim()

    def _probe_dim(self) -> int:
        """HTTP call test per scoprire dim del model unknown."""
        try:
            probe = self._do_embed(["ping"])
            return len(probe[0]) if probe else 0
        except Exception:
            # Fallback safe: 1536 (OpenAI default). User dovrà settare ANJA_EMBED_DIM se sbagliato.
            return 1536

    def _do_embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": texts}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/vincent-vigorito/anja",
            "X-Title": "anja-code-search",
        }
        data = _http_post_json(self.url, headers, payload)
        return [item["embedding"] for item in data["data"]]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._do_embed(texts)


class VoyageProvider(EmbedProvider):
    """Voyage AI — qualità SOTA su codice con `voyage-code-3`.

    Modelli:
      - voyage-code-3 ($0.06/1M, dim 1024, ottimizzato codice)
      - voyage-3 ($0.06/1M, dim 1024, general)
      - voyage-3-lite ($0.02/1M, dim 512, economico)
    """
    name = "voyage"

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or os.environ.get("ANJA_EMBED_MODEL", "voyage-code-3")
        self.dim = {
            "voyage-code-3": 1024,
            "voyage-3": 1024,
            "voyage-3-lite": 512,
            "voyage-large-2": 1536,
        }.get(self.model, 1024)
        self.url = "https://api.voyageai.com/v1/embeddings"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts, "input_type": "document"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = _http_post_json(self.url, headers, payload)
        return [item["embedding"] for item in data["data"]]


class OpenAIProvider(EmbedProvider):
    """OpenAI diretto.

    Modelli:
      - text-embedding-3-small ($0.02/1M, dim 1536)
      - text-embedding-3-large ($0.13/1M, dim 3072)
    """
    name = "openai"

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or os.environ.get("ANJA_EMBED_MODEL", "text-embedding-3-small")
        self.dim = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }.get(self.model, 1536)
        self.url = "https://api.openai.com/v1/embeddings"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = _http_post_json(self.url, headers, payload)
        return [item["embedding"] for item in data["data"]]


# ============================================================
# Local provider (sentence-transformers, opt-in heavy deps)
# ============================================================

class LocalProvider(EmbedProvider):
    """Embedding offline via sentence-transformers.

    Modelli leggeri raccomandati:
      - BAAI/bge-small-en (~133 MB, dim 384, qualità OK)
      - BAAI/bge-base-en  (~450 MB, dim 768, qualità buona)
      - BAAI/bge-large-en (~1.3 GB, dim 1024, qualità top open source)

    Lazy import per evitare crash se torch non installato.
    """
    name = "local"

    def __init__(self, model_name: Optional[str] = None):
        try:
            from sentence_transformers import SentenceTransformer  # noqa
        except ImportError:
            raise RuntimeError(
                "Local embedding requires sentence-transformers. "
                "Install: pip install sentence-transformers"
            )
        self.model = model_name or os.environ.get("ANJA_EMBED_MODEL", "BAAI/bge-small-en")
        from sentence_transformers import SentenceTransformer
        self._st = SentenceTransformer(self.model)
        # Determina dim dal model
        self.dim = self._st.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._st.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]


# ============================================================
# Factory
# ============================================================

def get_provider() -> Optional[EmbedProvider]:
    """Crea provider basato su env ANJA_EMBED_PROVIDER.

    Restituisce None se provider=none o se mancano API key (fallback graceful).
    Solleva RuntimeError per errori esplicit (es. local senza sentence-transformers).
    """
    name = (os.environ.get("ANJA_EMBED_PROVIDER") or "openrouter").lower()

    if name == "none":
        return None

    fallback_key = os.environ.get("ANJA_EMBED_API_KEY")

    if name == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY") or fallback_key
        if not key:
            return None
        return OpenRouterProvider(api_key=key)

    if name == "voyage":
        key = os.environ.get("VOYAGE_API_KEY") or fallback_key
        if not key:
            return None
        return VoyageProvider(api_key=key)

    if name == "openai":
        key = os.environ.get("OPENAI_API_KEY") or fallback_key
        if not key:
            return None
        return OpenAIProvider(api_key=key)

    if name == "local":
        return LocalProvider()

    raise ValueError(f"unknown ANJA_EMBED_PROVIDER: {name!r}. Valid: openrouter|voyage|openai|local|none")


# ============================================================
# CLI for quick test
# ============================================================

if __name__ == "__main__":
    prov = get_provider()
    if prov is None:
        print("Provider disabled (none) or missing API key.", file=sys.stderr)
        sys.exit(0)
    print(f"Provider: {prov}")
    texts = ["def hello(): return 'world'", "import numpy as np"] if len(sys.argv) < 2 else sys.argv[1:]
    vecs = prov.embed(texts)
    for t, v in zip(texts, vecs):
        print(f"  {t[:50]!r:55s} → dim={len(v)} [0]={v[0]:.4f} [1]={v[1]:.4f} ...")
