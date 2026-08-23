"""记忆写入/读取 - JSONL 追加 + 关键词检索
(可替换为 SQLite / Faiss / 任意下游存储,接口保持一致即可)"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import aiofiles

from config import settings

log = logging.getLogger(__name__)

_write_lock = asyncio.Lock()


async def append_to_jsonl(record: dict) -> int:
    """追加一条记录到 JSONL 文件,返回写入时的 server_ts(毫秒)

    Raises:
        PermissionError: 当 BRIDGE_WRITE_ENABLED=false 时
        IOError: 写入失败
    """
    if not settings.bridge_write_enabled:
        raise PermissionError(
            "BRIDGE_WRITE_ENABLED is false - refusing to persist. "
            "Set BRIDGE_WRITE_ENABLED=true in .env and restart the container."
        )

    file_path = Path(settings.jsonl_file)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    server_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    record["server_ts"] = server_ts

    line = json.dumps(record, ensure_ascii=False) + "\n"

    async with _write_lock:
        async with aiofiles.open(file_path, "a", encoding="utf-8") as f:
            await f.write(line)

    log.info(
        f"wrote record: layer={record.get('metadata', {}).get('cyrene_layer', '?')} "
        f"id={record.get('metadata', {}).get('cyrene_id', '?')[:16]} "
        f"content={record.get('memory_content', '')[:50]}..."
    )
    return server_ts


def count_records() -> int:
    """统计 JSONL 文件中记录数(同步方法,用于 health check)"""
    p = Path(settings.jsonl_file)
    if not p.exists():
        return 0
    with open(p, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def query_records(
    persona_id: Optional[str] = None,
    session_id: Optional[str] = None,
    layer: Optional[str] = None,
    since_ts: Optional[int] = None,
    until_ts: Optional[int] = None,
    query: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """从 JSONL 中按条件检索记忆"""
    p = Path(settings.jsonl_file)
    if not p.exists():
        return []

    out: list[dict] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            md = rec.get("metadata") or {}

            if persona_id and md.get("cyrene_persona_id") != persona_id:
                continue
            if session_id and md.get("cyrene_session_id") != session_id:
                continue
            if layer and md.get("cyrene_layer") != layer:
                continue
            ts = rec.get("server_ts", 0)
            if since_ts and ts < since_ts:
                continue
            if until_ts and ts > until_ts:
                continue
            if query:
                q = query.lower()
                content = (rec.get("memory_content") or "").lower()
                keywords = " ".join(md.get("cyrene_keywords") or []).lower()
                if q not in content and q not in keywords:
                    continue

            out.append(rec)

    out.sort(key=lambda r: r.get("server_ts", 0), reverse=True)
    return out[:limit]


def search_by_keyword(
    persona_id: str,
    query: str,
    layer: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """简单关键词检索"""
    return query_records(
        persona_id=persona_id,
        layer=layer,
        query=query,
        limit=limit,
    )
