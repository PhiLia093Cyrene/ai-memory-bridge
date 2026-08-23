# cyrene-bridge — AI agent 多端记忆共享网关

FastAPI 网关,接收来自任意 AI agent 端(桌面端 / IM bot / 浏览器插件等)的记忆推送,统一存储,可选同步到下游(AstrBot LivingMemory / 任意 SQLite+Faiss 存储)。

## 特点

- **多端共享**:任何能发 HTTP 的客户端都能推送记忆
- **三层记忆**:L0 画像 / L1 状态 / L2 事件(可扩展)
- **OpenAI 兼容 embedding**:支持千问 / OpenAI / Ollama / vLLM / 任何 OpenAI 兼容服务
- **可选下游同步**:bridge JSONL → AstrBot LivingMemory(60 秒自动 / 手动 POST /v1/sync)
- **完全 self-hosted**:无外部依赖,跑在你自己的 VPS 上
- **单进程,易部署**:FastAPI + uvicorn,docker compose up 即可

## 适用场景

- 你的 AI agent 有多个入口(桌面 chat + IM bot + 浏览器插件)
- 你希望记忆在所有端共享(一个端写,其他端都能 recall 到)
- 你不想写分布式存储(单机 SQLite 够了)

## 快速开始

### 1. 准备

```bash
# 生成鉴权 token
openssl rand -hex 32

# 复制配置
cp .env.example .env
# 编辑 .env,填好:
#   BRIDGE_AUTH_TOKEN=上面生成的 token
#   EMBEDDING_API_KEY=你的 OpenAI 兼容 embedding key
#   BRIDGE_WRITE_ENABLED=true
```

### 2. 启动

```bash
docker compose up -d
```

### 3. 验证

```bash
# 健康检查
curl http://localhost:18800/v1/health

# 推一条测试记忆
curl -X POST http://localhost:18800/v1/mem/items \
  -H "Authorization: Bearer $BRIDGE_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "test",
    "persona_id": "default",
    "session_id": "test",
    "items": [{
      "id": "test-001",
      "layer": "L2",
      "content": "用户喜欢喝冰美式",
      "event_type": "PREFERENCE",
      "importance": 0.7,
      "source_ts": 1724123456789
    }]
  }'

# 检索
curl "http://localhost:18800/v1/mem/recent?limit=5" \
  -H "Authorization: Bearer $BRIDGE_AUTH_TOKEN"
```

## API 文档

启动后访问 `http://localhost:18800/docs` 看 Swagger UI。

### 核心端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/v1/health` | 健康检查(无需鉴权) |
| `POST` | `/v1/mem/items` | 推送记忆条目(需鉴权) |
| `GET` | `/v1/mem/recent` | 检索最近记忆(支持 layer / persona / 时间窗口 / 关键词) |
| `GET` | `/v1/mem/search` | 关键词检索 |
| `POST` | `/v1/sync` | 手动同步 JSONL → 下游 LivingMemory(可选) |

### MemoryItem 字段

```json
{
  "id": "client-uuid-001",
  "layer": "L2",  // L0 / L1 / L2
  "field": "nickname",  // L0/L1 用
  "content": "用户昵称是 X",
  "event_type": "FACT",  // L2 用: FACT / PREFERENCE / GOAL / OPINION / RELATIONSHIP / OTHER
  "entities": [],  // 选填
  "importance": 0.8,  // 0-1
  "is_pinned": false,
  "source_ts": 1724123456789,  // 必填,毫秒
  "keywords": ["用户", "昵称"]  // 选填
}
```

## 配置说明

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `BRIDGE_PORT` | 否 | 8000 | 网关端口(对外) |
| `BRIDGE_AUTH_TOKEN` | **是** | (无默认,启动报错) | 鉴权 token,所有写入端点必填 |
| `EMBEDDING_API_KEY` | **是** | (空) | OpenAI 兼容 embedding key |
| `EMBEDDING_MODEL` | 否 | text-embedding-v3 | 模型名 |
| `BRIDGE_WRITE_ENABLED` | 否 | false | 写入开关(默认 false 防误写) |
| `LIVINGMEMORY_DB_PATH` | 否 | (空) | 下游同步目标(留空 = 不同步) |
| `LIVINGMEMORY_SYNC_PERSONA` | 否 | default | 写入下游时的 persona 名 |
| `AUTO_SYNC_ENABLED` | 否 | true | 是否后台 60 秒自动同步 |
| `AUTO_SYNC_INTERVAL_SEC` | 否 | 60 | 自动同步间隔 |

## 架构

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Desktop  │  │ IM Bot   │  │ Browser  │  ... 任一能发 HTTP 的 AI agent 端
│ Client   │  │ (AstrBot)│  │ Plugin   │
└────┬─────┘  └────┬─────┘  └────┬─────┘
     │ POST /v1/mem/items          │
     │  Bearer <token>             │
     ▼                             ▼
┌─────────────────────────────────────┐
│         cyrene-bridge (FastAPI)     │
│  - 鉴权                              │
│  - L0/L1/L2 -> MemoryEvent 转换     │
│  - 1024 维 embedding                 │
│  - 写 JSONL (bridge 主存)           │
│  - 60s auto-sync 或手动 /v1/sync   │
└──────────────┬──────────────────────┘
               │ 可选
               ▼
       ┌──────────────┐
       │ LivingMemory │  下游(可省略)
       │   SQLite     │
       │ (任意兼容实现) │
       └──────────────┘
```

## 跟 AstrBot 集成

`daemon/bridge_pusher.py` 是一个独立 daemon,跑在 AstrBot 容器内,3 秒 poll 一次 `conversations.db`,自动把 user 消息推 bridge。这样 bot 端不用改代码,零侵入接入。

详见 [../daemon/README.md](../daemon/README.md)。

## License

AGPLv3(因 FastAPI / Pydantic 生态兼容,跟原作者的 AI agent 框架一致)
