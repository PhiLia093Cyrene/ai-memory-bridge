# bridge_pusher — AstrBot → cyrene-bridge 反向推送 daemon

监控 AstrBot 容器内的 `conversations.db` 的 `messages` 表,自动把 user 消息推送到 cyrene-bridge,让 AI agent 实现跨端记忆共享。

## 特点

- **零侵入**:不 patch AstrBot 代码,只读 SQLite
- **3 秒延迟**:消息发出后 3 秒内推到 bridge
- **配置驱动**:全部从环境变量读,无 hardcode
- **可作模块**:既可独立跑(`python3 bridge_pusher.py`),也可被其他代码 `import`

## 适用场景

- 你跑了一个 AstrBot QQ / 微信 bot
- 你想让 bot 的用户消息也能进入 cyrene-bridge,被桌面端 / 其他端 recall 到
- 你不想修改 AstrBot 源码(担心升级兼容性)

## 安装

```bash
# 1. 复制配置文件
cp .env.example .env
# 编辑 .env,填好 BRIDGE_URL / BRIDGE_TOKEN / ASTRBOT_CONVERSATIONS_DB

# 2. 安装依赖
pip install aiohttp
```

## 启动

### 方式 A:在 AstrBot 容器内运行(推荐)

```bash
# 1. 把 bridge_pusher.py 拷进容器
docker cp bridge_pusher.py <astrbot_container>:/app/bridge_pusher.py
docker cp .env <astrbot_container>:/app/.env

# 2. 启动(注意 redirect 捕获 log)
docker exec -d <astrbot_container> sh -c 'python3 -u /app/bridge_pusher.py >> /var/log/bridge_pusher.log 2>&1'
```

### 方式 B:独立运行

```bash
# 把 .env 放在同目录,直接跑
python3 bridge_pusher.py
```

## 验证

```bash
# 看 daemon 日志
docker exec <astrbot_container> tail -5 /var/log/bridge_pusher.log
# 应该看到:
#   bridge_pusher daemon starting
#   initialized: last_id=N
# (用户发消息后):
#   bridge push ok: id=N content=...

# 看 bridge 端
curl http://localhost:18800/v1/mem/recent?limit=5 \
  -H "Authorization: Bearer $BRIDGE_TOKEN"
# 应该能看到 cyrene-bot 标签的记录
```

## 配置说明

| 环境变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `BRIDGE_URL` | 是 | (空) | cyrene-bridge 服务地址 |
| `BRIDGE_TOKEN` | 是 | (空) | 与 bridge 的 BRIDGE_AUTH_TOKEN 一致 |
| `ASTRBOT_CONVERSATIONS_DB` | 是 | (空) | AstrBot conversations.db 完整路径 |
| `ASTRBOT_MESSAGES_TABLE` | 否 | `messages` | messages 表名(一般不用改) |
| `BRIDGE_PUSHER_ENABLED` | 否 | `true` | 推送开关 |
| `BRIDGE_POLL_INTERVAL` | 否 | `3` | 轮询间隔(秒) |
| `BRIDGE_MAX_PER_DAY` | 否 | `500` | 每日推送上限 |

## 已知限制

- **只推 user 消息**:role='user' 的行,role='assistant' 自动过滤
- **过滤 bot 镜像**:`metadata.is_bot_message=true` 的行也跳过
- **trim AstrBot 提示词**:如果你的 bot 配置了 user prompt 模板(如 `[本地协议] 叙事层已隔离...`),daemon 会以 `\n\n` 分隔,只推最后一段 user 原话。**如果你的提示词格式不同**,需要改 `_strip_user_prompt_prefix` 的逻辑
- **不推历史消息**:启动时 `last_id = MAX(id)`,只推启动后新写的行(避免重复推历史 200+ 条)

## 故障排查

| 症状 | 排查 |
|---|---|
| daemon 启动后无 log | 检查 stdout/stderr 重定向(必须 `>> log 2>&1`,不要加 `setsid`) |
| `bridge push failed 401` | BRIDGE_TOKEN 与 bridge 不一致 |
| `bridge push failed 422` | 请求体不合法,看 daemon log 的 `body=` 字段 |
| `sqlite read error` | ASTRBOT_CONVERSATIONS_DB 路径错,确认文件存在且可读 |
| 启动了但 0 推送 | `last_id` 已等于当前最大 id,需要等新消息;或所有 user 消息都被 `is_bot_message` 过滤了 |

## 协议参考

- `POST /v1/mem/items` 由 cyrene-bridge 提供,见 [bridge/README.md](../bridge/README.md)
- 数据 schema 在 [docs/architecture.md](../docs/architecture.md)
