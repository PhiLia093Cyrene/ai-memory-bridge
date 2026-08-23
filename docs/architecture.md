# cyrene-bridge 架构

## 完整数据流

```
┌────────────────────────────────────────────────────────────────────┐
│                         AI Agent 客户端                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ 桌面端   │ │ IM Bot   │ │ 浏览器   │ │ CLI 工具 │ │ 其他    │ │
│  │(Electron)│ │(AstrBot) │ │(Plugin)  │ │(cron job)│ │ ...     │ │
│  └─────┬────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬────┘ │
└────────┼──────────┼──────────┼──────────┼───────────────┼────────┘
         │          │          │          │               │
         │  push    │  poll    │  push    │  push         │
         │  3-5s    │  3s      │  即时    │  定时         │
         ▼          ▼          ▼          ▼               ▼
┌────────────────────────────────────────────────────────────────────┐
│                      cyrene-bridge (FastAPI)                        │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                      HTTP API                                 │   │
│  │  POST /v1/mem/items  GET /v1/mem/recent  GET /v1/mem/search  │   │
│  │  GET  /v1/health     POST /v1/sync                            │   │
│  └──────────────────────────────────────────────────────────────┘   │
│         │                                                             │
│         ▼                                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     业务层 (services/)                        │   │
│  │  - l2_transform.py  L0/L1/L2 → MemoryEvent                    │   │
│  │  - embedding.py     OpenAI 兼容 embedding (1024 维)          │   │
│  │  - memory_writer.py 写 JSONL + 关键词检索                     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│         │                                                             │
│         ▼                                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  存储 (默认 JSONL,可换 SQLite)               │   │
│  │              ./data/cyrene_bridge.jsonl                       │   │
│  └──────────────────────────────────────────────────────────────┘   │
│         │                                                             │
│         │ 60s auto-sync / 手动 POST /v1/sync (可选)                  │
│         ▼                                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              下游 LivingMemory SQLite (可选)                  │   │
│  │  - documents 表 (FTS5 全文检索)                               │   │
│  │  - bot recall 走这里                                          │   │
│  └──────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
         ▲
         │ bot recall
         │
┌────────┴────────────────────────────────────────────────────────────┐
│                        AI Bot (AstrBot 等)                          │
│  - recall 时查 LivingMemory documents 表                             │
│  - 命中则把 context 注入 prompt                                       │
└────────────────────────────────────────────────────────────────────┘
```

## 端到端延迟

| 路径 | 延迟 |
|---|---|
| 桌面端 push → bridge 收到 | 即时 (< 100ms) |
| AstrBot daemon poll → bridge | 3 秒 (poll interval) |
| bridge → JSONL 落盘 | 即时 |
| bridge → LivingMemory sync | 60 秒 (auto-sync) 或 即时 (手动 /v1/sync) |
| LivingMemory → bot recall | 即时 (FTS5 query) |

**总延迟**:3-60 秒,看 auto-sync 配置和 push 来源。

## 数据 Schema

### MemoryItem (push 请求体)

```json
{
  "id": "client-generated-uuid-001",  // 必填,客户端唯一
  "layer": "L2",  // L0 / L1 / L2
  "field": "nickname",  // L0/L1 必填
  "content": "用户喜欢喝冰美式",
  "event_type": "PREFERENCE",  // L2: FACT / PREFERENCE / GOAL / OPINION / RELATIONSHIP / OTHER
  "entities": [],
  "importance": 0.7,
  "is_pinned": false,
  "source_ts": 1724123456789,  // 必填,毫秒
  "keywords": ["冰美式", "咖啡"]
}
```

### 内部存储格式 (cyrene_bridge.jsonl)

```json
{
  "memory_content": "用户喜欢喝冰美式",
  "event_type": "PREFERENCE",
  "entities": [],
  "importance_score": 0.7,
  "metadata": {
    "cyrene_layer": "L2",
    "cyrene_id": "client-generated-uuid-001",
    "cyrene_source": "cyrene-global",
    "source_ts": 1724123456789
  },
  "embedding": [0.012, -0.034, ...],  // 1024 维 float
  "server_ts": 1724123457000
}
```

### LivingMemory documents 表 (下游,可选)

```sql
CREATE TABLE documents (
  doc_id TEXT PRIMARY KEY,  -- cyrene-bridge-<cyrene_id>
  text TEXT,
  metadata TEXT,  -- JSON
  created_at TEXT,  -- ISO 8601
  updated_at TEXT
);
```

## 安全模型

- **单一鉴权 token**:`BRIDGE_AUTH_TOKEN` 必须从环境变量读,启动时校验(不能为空)
- **写入默认关闭**:`BRIDGE_WRITE_ENABLED=false` 启动时拒写,只算 embedding
- **Bearer Token 鉴权**:所有写端点 `/v1/mem/items`、`/v1/sync` 都要 `Authorization: Bearer <token>`
- **健康检查豁免**:`/v1/health` 不需 token,供 docker healthcheck
- **本地文件读权限**:同步 LivingMemory 时 db 路径 `ro` 或 `rw` 取决于 sync 配置(默认 `rw`)

## 部署拓扑

### 单机(最简)

```
[VPS]
├─ cyrene-bridge 容器 (port 18800)
├─ AstrBot 容器 + bridge_pusher.py daemon
└─ (可选) LivingMemory SQLite 在 AstrBot 容器内,bridge 通过 volume mount 访问
```

### 多机(更复杂)

```
[VPS-A: bridge]
└─ cyrene-bridge 容器 (port 18800)

[VPS-B: AstrBot]
└─ AstrBot 容器 + bridge_pusher.py daemon
   → 通过 BRIDGE_URL=http://VPS-A:18800 推送

[VPS-C: 桌面端]
└─ Cyrene 桌面端 → 通过 BRIDGE_URL=http://VPS-A:18800 推送
```

## 扩展点

- **替换 JSONL 为 SQLite / Faiss**:改 `services/memory_writer.py`,API 不变
- **添加新端**:实现 `POST /v1/mem/items` 客户端,任意语言
- **加 webhook 通知**:bot 收到重要记忆时通过其他 channel 推送
- **同步到其他下游**:Faiss / Milvus / Chroma / Qdrant / Pinecone,在 `_build_livingmemory_document` 加分支
