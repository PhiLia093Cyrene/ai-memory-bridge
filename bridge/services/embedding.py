"""Embedding 服务 - 调用 OpenAI 兼容 embedding 接口
默认配置走阿里云 DashScope (千问),但你也可以改成任何 OpenAI 兼容服务(OpenAI / DeepSeek / Ollama / vLLM)。"""
import logging
import httpx

from config import settings

log = logging.getLogger(__name__)


async def embed_text(text: str) -> list[float]:
    """对单段文本算 embedding,返回 float 列表

    兼容接口:POST {api_base}/embeddings
    Body: { "model": "...", "input": "text", "encoding_format": "float" }
    """
    if not text or not text.strip():
        raise ValueError("text is empty")

    if not settings.embedding_api_key:
        raise ValueError("EMBEDDING_API_KEY is not configured in .env")

    url = f"{settings.embedding_api_base.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.embedding_model,
        "input": text[:2048],
        "encoding_format": "float",
    }

    log.debug(f"calling embedding api: model={settings.embedding_model} len={len(text)}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["data"][0]["embedding"]
    except (KeyError, IndexError) as e:
        log.error(f"unexpected embedding response: {data}")
        raise ValueError(f"invalid embedding response: {e}")


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量 embedding(预留接口)"""
    if not texts:
        return []
    if not settings.embedding_api_key:
        raise ValueError("EMBEDDING_API_KEY is not configured")

    url = f"{settings.embedding_api_base.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.embedding_model,
        "input": [t[:2048] for t in texts],
        "encoding_format": "float",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return [d["embedding"] for d in data["data"]]
