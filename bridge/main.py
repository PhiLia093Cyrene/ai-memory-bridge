"""cyrene-bridge - FastAPI 入口
多端 AI agent 记忆共享桥接网关
- POST /v1/mem/items  接收任一端推送
- GET  /v1/health     健康检查
- GET  /v1/mem/recent 检索最近记忆
- GET  /v1/mem/search 关键词检索
- POST /v1/sync       同步到 LivingMemory(可选)
"""
import json
import logging
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from config import settings
from services.l2_transform import cyrene_l2_to_memory_event
from services.memory_writer import (
    append_to_jsonl,
    count_records,
    query_records,
    search_by_keyword,
)
from services.embedding import embed_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("cyrene-bridge")


# ============== Background Auto-Sync ==============


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: 启动时开启后台 auto-sync task, 关闭时取消。"""
    auto_sync_enabled = getattr(settings, "auto_sync_enabled", True)
    auto_sync_interval = getattr(settings, "auto_sync_interval_sec", 60)

    task = None
    db_ok = bool(settings.livingmemory_db_path) and Path(settings.livingmemory_db_path).exists()
    if auto_sync_enabled and db_ok:
        task = asyncio.create_task(_auto_sync_loop(auto_sync_interval))
        log.info(
            f"auto-sync enabled: interval={auto_sync_interval}s "
            f"persona={settings.livingmemory_sync_persona} "
            f"db={settings.livingmemory_db_path}"
        )
    else:
        log.info(
            f"auto-sync disabled "
            f"(enabled={auto_sync_enabled}, db_ok={db_ok}, "
            f"db_path={settings.livingmemory_db_path or '<unset>'})"
        )

    yield

    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log.info("auto-sync task cancelled on shutdown")


async def _auto_sync_loop(interval_sec: int):
    """每 interval_sec 秒调一次 _sync_to_livingmemory。失败不中断,下次继续。"""
    while True:
        try:
            await asyncio.sleep(interval_sec)
            result = await _sync_to_livingmemory()
            inserted = result.get("inserted", 0)
            skipped = result.get("skipped", 0)
            if inserted or skipped:
                log.info(f"auto-sync: inserted={inserted} skipped={skipped}")
            else:
                log.debug("auto-sync: nothing to sync")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception(f"auto-sync failed (will retry next cycle): {e}")


app = FastAPI(
    title="cyrene-bridge",
    version="0.1.0",
    description="AI agent 多端记忆共享桥接网关(单向推送 + 可选 sync 到下游)",
    lifespan=lifespan,
)


# ============== Pydantic Models ==============


class MemoryItem(BaseModel):
    """AI agent 端推送的单个记忆条目"""
    id: str = Field(..., description="客户端唯一 id,用于幂等去重")
    layer: str = Field("L2", pattern="^L[012]$", description="L0 / L1 / L2")
    field: Optional[str] = Field(None, description="L0/L1 用,例如 nickname / occupation")
    content: str = Field(..., min_length=1, description="记忆内容")
    event_type: Optional[str] = Field(None, description="L2 用,FACT/PREFERENCE/GOAL/OPINION/RELATIONSHIP/OTHER")
    entities: List[dict] = Field(default_factory=list, description="实体列表")
    importance: float = Field(0.6, ge=0.0, le=1.0, description="重要性 0-1")
    is_pinned: bool = Field(False, description="是否固定")
    source_ts: int = Field(..., description="客户端时间戳(毫秒)")
    keywords: List[str] = Field(default_factory=list, description="L2 关键词(可选)")

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content cannot be empty or whitespace")
        return v


class PushRequest(BaseModel):
    """推送请求体"""
    source: str = Field("cyrene", description="来源标识(自由命名)")
    persona_id: str = Field("default", description="人格 id,对应你的 AI agent persona")
    session_id: str = Field("default", description="会话 id")
    items: List[MemoryItem] = Field(..., min_length=1, description="记忆条目列表")


class PushResult(BaseModel):
    """单条推送结果"""
    id: str
    server_ts: int
    embedding_dim: int = 0
    embedding_error: Optional[str] = None
    dedup_hit: bool = False


class PushResponse(BaseModel):
    """推送响应"""
    results: List[PushResult]
    write_enabled: bool
    accepted: int
    rejected: int


class HealthResponse(BaseModel):
    status: str
    write_enabled: bool
    embedding_model: str
    embedding_dim: int
    jsonl_file: str
    jsonl_exists: bool
    record_count: int
    api_key_configured: bool


# ============== Auth ==============


async def verify_token(authorization: Optional[str] = Header(None)) -> bool:
    """Bearer Token 鉴权"""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    expected = f"Bearer {settings.bridge_auth_token}"
    if authorization != expected:
        log.warning(f"auth failed from header: {authorization[:30]}...")
        raise HTTPException(status_code=401, detail="invalid token")
    return True


# ============== Routes ==============


@app.get("/v1/health", response_model=HealthResponse)
async def health():
    """健康检查 - 无需鉴权,供 docker healthcheck 和前端轮询"""
    p = Path(settings.jsonl_file)
    return HealthResponse(
        status="ok",
        write_enabled=settings.bridge_write_enabled,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        jsonl_file=settings.jsonl_file,
        jsonl_exists=p.exists(),
        record_count=count_records(),
        api_key_configured=bool(settings.embedding_api_key),
    )


@app.post("/v1/mem/items", response_model=PushResponse, dependencies=[Depends(verify_token)])
async def push_items(req: PushRequest):
    """接收 AI agent 端推送的记忆条目,落盘。

    处理流程:
    1. 鉴权(已通过 Depends)
    2. 逐条转换 L0/L1/L2 -> MemoryEvent
    3. 调 embedding 服务(失败也继续,LivingMemory 用 BM25 兜底)
    4. 追加到 JSONL 文件(BRIDGE_WRITE_ENABLED=true 时才真写)
    5. 返回每条的结果
    """
    log.info(
        f"push from source={req.source} persona={req.persona_id} "
        f"session={req.session_id} items={len(req.items)}"
    )

    results: List[PushResult] = []
    accepted = 0
    rejected = 0

    for item in req.items:
        try:
            event = cyrene_l2_to_memory_event(item.model_dump())
        except ValueError as e:
            log.warning(f"skip item {item.id}: {e}")
            rejected += 1
            continue

        embedding_error: Optional[str] = None
        embedding_dim = 0
        try:
            vector = await embed_text(event["memory_content"])
            event["embedding"] = vector
            embedding_dim = len(vector)
        except Exception as e:
            log.error(f"embedding failed for {item.id}: {e}")
            event["embedding"] = None
            embedding_error = str(e)

        try:
            server_ts = await append_to_jsonl(event)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except Exception as e:
            log.exception(f"write failed for {item.id}")
            raise HTTPException(status_code=500, detail=f"write failed: {e}")

        results.append(PushResult(
            id=item.id,
            server_ts=server_ts,
            embedding_dim=embedding_dim,
            embedding_error=embedding_error,
        ))
        accepted += 1

    log.info(f"push done: accepted={accepted} rejected={rejected}")
    return PushResponse(
        results=results,
        write_enabled=settings.bridge_write_enabled,
        accepted=accepted,
        rejected=rejected,
    )


@app.get("/v1/mem/recent", dependencies=[Depends(verify_token)])
async def recent_items(
    limit: int = 20,
    persona_id: Optional[str] = None,
    session_id: Optional[str] = None,
    layer: Optional[str] = None,
    since_ts: Optional[int] = None,
    until_ts: Optional[int] = None,
    query: Optional[str] = None,
):
    """按条件检索最近的记忆条目

    参数(全部可选):
    - limit: 返回条数,1-200,默认 20
    - persona_id: 过滤人格
    - session_id: 过滤会话
    - layer: L0 / L1 / L2
    - since_ts: 起始时间戳(毫秒),只返回 >= 此时间
    - until_ts: 截止时间戳(毫秒),只返回 <= 此时间
    - query: 关键词,在 content 和 keywords 里模糊匹配
    """
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1-200")
    if layer and layer not in ("L0", "L1", "L2"):
        raise HTTPException(status_code=400, detail="layer must be L0/L1/L2")

    items = query_records(
        persona_id=persona_id,
        session_id=session_id,
        layer=layer,
        since_ts=since_ts,
        until_ts=until_ts,
        query=query,
        limit=limit,
    )
    return {
        "count": len(items),
        "total": count_records(),
        "items": items,
    }


@app.get("/v1/mem/search", dependencies=[Depends(verify_token)])
async def search_items(
    q: str,
    persona_id: Optional[str] = None,
    layer: Optional[str] = None,
    limit: int = 20,
):
    """关键词检索"""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="q must be non-empty")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be 1-100")

    items = search_by_keyword(
        persona_id=persona_id or "",
        query=q,
        layer=layer,
        limit=limit,
    )
    return {
        "query": q,
        "count": len(items),
        "items": items,
    }


@app.get("/")
async def root():
    return {
        "name": "cyrene-bridge",
        "version": "0.1.0",
        "endpoints": [
            "GET  /v1/health",
            "POST /v1/mem/items",
            "GET  /v1/mem/recent?limit=N&persona_id=&layer=&since_ts=&query=",
            "GET  /v1/mem/search?q=&persona_id=&layer=&limit=",
            "POST /v1/sync   (可选: bridge JSONL → 下游存储)",
        ],
    }


# ============== 可选: 下游存储同步端点 ==============


def _build_livingmemory_document(rec: dict) -> dict | None:
    """把一条 bridge 记录转成下游 documents 行的 dict(下游 schema 兼容 AstrBot LivingMemory)。"""
    meta = rec.get("metadata", {})
    cyrene_id = meta.get("cyrene_id")
    if not cyrene_id:
        return None
    content = (rec.get("memory_content") or "").strip()
    if not content:
        return None

    layer = meta.get("cyrene_layer", "L2")
    cyrene_event_type = rec.get("event_type", "OTHER")
    if layer in ("L0", "L1"):
        atom_type = "preference"
    else:
        atom_type = {
            "FACT": "factual",
            "PREFERENCE": "preference",
            "GOAL": "planned",
            "OPINION": "preference",
            "RELATIONSHIP": "relational",
        }.get(cyrene_event_type, "unknown")

    source_ts_ms = float(meta.get("source_ts") or (int(time.time() * 1000)))
    server_ts_ms = float(rec.get("server_ts") or source_ts_ms)
    created_at = source_ts_ms / 1000.0
    last_accessed = server_ts_ms / 1000.0

    importance = max(0.0, min(1.0, float(rec.get("importance_score") or 0.5)))

    entities = rec.get("entities") or []
    keywords = rec.get("keywords") or []

    created_at_dt = datetime.fromtimestamp(source_ts_ms / 1000.0).isoformat()
    updated_at_dt = datetime.fromtimestamp(server_ts_ms / 1000.0).isoformat()

    metadata = {
        "session_id": settings.livingmemory_sync_session,
        "persona_id": settings.livingmemory_sync_persona,
        "importance": importance,
        "create_time": created_at,
        "last_access_time": last_accessed,
        "status": "active",
        "topics": keywords or [f"cyrene_{layer.lower()}"],
        "key_facts": [content[:300]],
        "sentiment": "neutral",
        "interaction_type": "private_chat",
        "summary_quality": "normal",
        "atom_types": [atom_type],
        "atom_id": cyrene_id,
        "cyrene_layer": layer,
        "cyrene_source": meta.get("cyrene_source", ""),
        "external_source": "cyrene-bridge",
    }

    return {
        "doc_id": f"cyrene-bridge-{cyrene_id}",
        "text": content,
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
        "created_at": created_at_dt,
        "updated_at": updated_at_dt,
        "cyrene_id": cyrene_id,  # 用于幂等
    }


async def _sync_to_livingmemory() -> dict:
    """读 bridge JSONL,写入下游 livingmemory.db(若有 FTS5 documents 表)。幂等。"""
    import sqlite3

    db_path = settings.livingmemory_db_path
    if not db_path:
        return {"skipped": "livingmemory_db_path not configured"}
    if not Path(db_path).exists():
        return {"error": f"db not found: {db_path}"}
    if not Path(settings.jsonl_file).exists():
        return {"error": f"jsonl not found: {settings.jsonl_file}"}

    persona = settings.livingmemory_sync_persona
    session = settings.livingmemory_sync_session

    inserted = 0
    skipped = 0
    invalid = 0
    total = 0

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        cur = conn.execute(
            "SELECT doc_id FROM documents WHERE doc_id LIKE 'cyrene-bridge-%'",
        )
        existing = set()
        for (doc_id,) in cur:
            if doc_id and doc_id.startswith("cyrene-bridge-"):
                existing.add(doc_id[len("cyrene-bridge-"):])

        with open(settings.jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                doc = _build_livingmemory_document(rec)
                if doc is None:
                    invalid += 1
                    continue
                if doc["cyrene_id"] in existing:
                    skipped += 1
                    continue
                rec_meta = rec.get("metadata", {}) or {}
                if rec_meta.get("bot_written"):
                    skipped += 1
                    existing.add(doc["cyrene_id"])
                    continue
                cur = conn.execute(
                    """
                    INSERT INTO documents (doc_id, text, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        doc["doc_id"],
                        doc["text"],
                        doc["metadata_json"],
                        doc["created_at"],
                        doc["updated_at"],
                    ),
                )
                doc_rowid = cur.lastrowid
                try:
                    conn.execute(
                        "INSERT INTO documents_fts (rowid, text) VALUES (?, ?)",
                        (doc_rowid, doc["text"]),
                    )
                except Exception as _fts_err:
                    log.warning(f"FTS insert failed (non-fatal): {_fts_err}")
                existing.add(doc["cyrene_id"])
                inserted += 1

        conn.commit()

    return {
        "persona_id": persona,
        "session_id": session,
        "total": total,
        "inserted": inserted,
        "skipped": skipped,
        "invalid": invalid,
    }


@app.post("/v1/sync", dependencies=[Depends(verify_token)])
async def sync_to_livingmemory():
    """把 bridge JSONL 全部记录同步到下游 livingmemory.db(AstrBot LivingMemory 兼容)。"""
    if not settings.livingmemory_db_path:
        raise HTTPException(
            status_code=503,
            detail="LIVINGMEMORY_DB_PATH not configured. Mount livingmemory.db and set the env var.",
        )
    try:
        result = await _sync_to_livingmemory()
    except Exception as e:
        log.exception("sync to livingmemory failed")
        raise HTTPException(status_code=500, detail=f"sync failed: {e}")
    log.info(f"sync result: {result}")
    return result
