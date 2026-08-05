from __future__ import annotations

import json
import os

from ..core.config import settings
import httpx
from ..core.upload_security import UnsafeRemoteURLError
from .open_source_config import load_open_source_config_from_store

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


async def load_embedding_runtime_config(
    runtime_config: dict[str, str] | None = None,
) -> dict[str, str]:
    if runtime_config:
        override = {
            "base_url": str(runtime_config.get("base_url") or "").strip(),
            "api_key": str(runtime_config.get("api_key") or "").strip(),
            "model": str(runtime_config.get("model") or "").strip(),
        }
        if all(override.values()):
            return override
    try:
        config = await load_open_source_config_from_store()
        embedding_model = (config or {}).get("embeddingModel") or {}
        base_url = str(embedding_model.get("baseUrl") or "").strip()
        api_key = str(embedding_model.get("apiKey") or "").strip()
        model = str(embedding_model.get("modelName") or "").strip()
        if base_url and api_key and model:
            return {"base_url": base_url, "api_key": api_key, "model": model}
    except Exception:
        pass

    return {
        "base_url": os.environ.get("EMBEDDING_BASE_URL", "") or settings.embedding_base_url,
        "api_key": os.environ.get("EMBEDDING_API_KEY", "") or settings.embedding_api_key,
        "model": os.environ.get("EMBEDDING_MODEL", "") or settings.embedding_model or DEFAULT_EMBEDDING_MODEL,
    }


async def generate_embedding(
    text: str,
    *,
    runtime_config: dict[str, str] | None = None,
) -> list[float]:
    if not text or not text.strip():
        return []

    config = await load_embedding_runtime_config(runtime_config)
    if not config["base_url"] or not config["api_key"]:
        raise RuntimeError("Embedding 模型未配置，请先在系统配置中填写向量模型")

    base_url = config["base_url"].rstrip("/")
    if base_url.endswith("/v1") or "/v1/" in base_url:
        endpoint = f"{base_url}/embeddings"
    else:
        endpoint = f"{base_url}/v1/embeddings"

    payload = {
        "model": config["model"] or DEFAULT_EMBEDDING_MODEL,
        "input": text,
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise RuntimeError(f"Embedding API request failed: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(f"Embedding API returned HTTP {response.status_code}")
    try:
        data = response.json()
        rows = data.get("data") if isinstance(data, dict) else None
        embedding = rows[0].get("embedding", []) if isinstance(rows, list) and rows else []
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        raise RuntimeError("Embedding API returned an invalid response") from exc
    if not isinstance(embedding, list) or not embedding:
        raise RuntimeError("Embedding API returned an empty embedding")
    return embedding
