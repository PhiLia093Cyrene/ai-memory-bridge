#!/usr/bin/env python3
"""AstrBot LivingMemory → cyrene-bridge 反向推送 daemon

用途:监控 AstrBot 容器内的 conversations.db,自动把 user 消息推送到 cyrene-bridge。
特点:
- 单进程 daemon,3 秒 poll 一次
- 不需要 patch AstrBot 代码,只读 SQLite
- 配置全部从环境变量读,无 hardcode

部署:
  1. 复制 .env.example 为 .env,填好 BRIDGE_URL / BRIDGE_TOKEN
  2. 启动(用 redirect 捕获 log):
     docker exec -d <astrbot_container> sh -c 'python3 -u /path/to/bridge_pusher.py >> /var/log/bridge_pusher.log 2>&1'
  3. 验证:
     docker exec <astrbot_container> tail -5 /var/log/bridge_pusher.log
     应该看到 "bridge push ok: id=N content=..."
"""
import asyncio
import logging
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

# ============================================================
# 配置(全部从 env 读,无 hardcode,避免在版本控制里泄露)
# ============================================================
URL = os.getenv("BRIDGE_URL", "").rstrip("/")
TOKEN = os.getenv("BRIDGE_TOKEN", "")
ENABLED = os.getenv("BRIDGE_PUSHER_ENABLED", "true").lower() == "true"
DB_PATH = os.getenv("ASTRBOT_CONVERSATIONS_DB", "")
TABLE = os.getenv("ASTRBOT_MESSAGES_TABLE", "messages")
POLL_INTERVAL = int(os.getenv("BRIDGE_POLL_INTERVAL", "3"))
MAX_PER_DAY = int(os.getenv("BRIDGE_MAX_PER_DAY", "500"))

# ============================================================
# aiohttp 可选依赖
# ============================================================
try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore

# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bridge_pusher")

# ============================================================
# 状态
# ============================================================
_seen_ids: set = set()
_day_count = 0
_day_started = time.time()


def _reset_if_new_day() -> None:
    global _day_count, _day_started
    if time.time() - _day_started > 86400:
        _day_count = 0
        _day_started = time.time()


def _allow() -> bool:
    _reset_if_new_day()
    return _day_count < MAX_PER_DAY


async def push_message(session: "aiohttp.ClientSession", msg: Dict[str, Any]) -> bool:
    """Fire-and-forget 推 1 条 user 消息到 cyrene-bridge。"""
    global _day_count
    if not ENABLED or not URL or not TOKEN:
        return False
    if not _allow():
        log.info("daily limit reached (%d/%d), skip msg %s",
                 _day_count, MAX_PER_DAY, msg.get("id"))
        return False
    if aiohttp is None:
        log.warning("aiohttp not installed, skip bridge push")
        return False

    content = (msg.get("content") or "").strip()
    if not content:
        return False

    # 截掉 AstrBot user prompt 模板 prefix(以 \n\n 分隔的最后一段是 user 原话)
    if "\n\n" in content:
        tail = content.rsplit("\n\n", 1)[-1].strip()
        if tail:
            content = tail

    item = {
        "id": f"bot-msg-{msg.get('session_id', '')}-{msg.get('id', '')}",
        "layer": "L2",
        "content": content,
        "event_type": "OTHER",
        "source_ts": int((msg.get("timestamp") or time.time()) * 1000),
        "metadata": {
            "cyrene_source": "cyrene-global",
            "cyrene_origin": "cyrene-bot",  # 标记来源端
            "user_id": str(msg.get("sender_id", "")),
            "sender_name": str(msg.get("sender_name", "")),
            "session_id": str(msg.get("session_id", "")),
            "platform": str(msg.get("platform", "")),
            "msg_role": "user",
            "msg_timestamp": msg.get("timestamp"),
            "bot_written": True,  # 标记 bot 端已直接写,bridge sync 跳过避免重复
        },
    }

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with session.post(
            f"{URL}/v1/mem/items",
            json={"items": [item]},
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as resp:
            if resp.status < 300:
                _day_count += 1
                log.info("bridge push ok: id=%s content=%s",
                         msg.get("id"), content[:40])
                return True
            body = await resp.text()
            log.warning("bridge push failed: status=%d body=%s", resp.status, body[:200])
            return False
    except Exception as e:
        log.warning("bridge push error: %s", e)
        return False


def _read_new_messages(last_id: int) -> List[tuple]:
    """读 messages 表中 id > last_id 且 role='user' 的新行,过滤掉 bot 镜像。"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=3)
        rows = conn.execute(
            f"SELECT id, content, role, sender_id, session_id, timestamp, platform, sender_name, metadata "
            f"FROM {TABLE} WHERE id > ? AND role = 'user' ORDER BY id ASC LIMIT 50",
            (last_id,),
        ).fetchall()
        conn.close()

        import json
        filtered = []
        for r in rows:
            metadata_str = r[8] or ""
            if metadata_str:
                try:
                    meta = json.loads(metadata_str)
                    if meta.get("is_bot_message"):
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            filtered.append(r)
        return filtered
    except Exception as e:
        log.warning("sqlite read error: %s", e)
        return []


async def poll_loop() -> None:
    """主轮询循环:每 POLL_INTERVAL 秒检查 messages 表,user 消息推 bridge。"""
    log.info("starting poll loop: url=%s db=%s table=%s interval=%ds max_per_day=%d",
             URL, DB_PATH, TABLE, POLL_INTERVAL, MAX_PER_DAY)

    last_id = 0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=3)
        row = conn.execute(f"SELECT MAX(id) FROM {TABLE}").fetchone()
        if row and row[0]:
            last_id = row[0]
        conn.close()
        log.info("initialized: last_id=%d (only push messages with id > %d)", last_id, last_id)
    except Exception as e:
        log.warning("failed to read initial last_id (will start from 0): %s", e)

    if aiohttp is None:
        log.error("aiohttp not installed; bridge push disabled")
        return

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                rows = _read_new_messages(last_id)
                for r in rows:
                    mid = r[0]
                    if mid in _seen_ids:
                        continue
                    _seen_ids.add(mid)
                    msg = {
                        "id": mid,
                        "content": r[1] or "",
                        "role": r[2] or "user",
                        "sender_id": r[3] or "",
                        "session_id": r[4] or "",
                        "timestamp": r[5],
                        "platform": r[6] or "",
                        "sender_name": r[7] or "",
                    }
                    await push_message(session, msg)
                    last_id = max(last_id, mid)
            except Exception as e:
                log.warning("poll loop error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    """独立 daemon 入口:python3 bridge_pusher.py"""
    log.info("=" * 60)
    log.info("bridge_pusher daemon starting")
    log.info("  enabled=%s url=%s db=%s table=%s", ENABLED, URL, DB_PATH, TABLE)
    log.info("  poll_interval=%ds max_per_day=%d", POLL_INTERVAL, MAX_PER_DAY)
    log.info("=" * 60)
    if not ENABLED:
        log.warning("bridge pusher is DISABLED (set BRIDGE_PUSHER_ENABLED=true to enable)")
        sys.exit(0)
    if not URL or not TOKEN:
        log.error("BRIDGE_URL or BRIDGE_TOKEN not set; check your .env")
        sys.exit(1)
    if not DB_PATH:
        log.error("ASTRBOT_CONVERSATIONS_DB not set; check your .env")
        sys.exit(1)
    try:
        asyncio.run(poll_loop())
    except KeyboardInterrupt:
        log.info("interrupted, exiting")
        sys.exit(0)


if __name__ == "__main__":
    main()
