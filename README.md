# cyrene-bridge

> **AI agent 多端记忆共享桥接网关** · **跨端记忆同步** · **cross-platform memory bridge**
> (GitHub repo: `PhiLia093Cyrene/ai-memory-bridge`)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![GitHub stars](https://img.shields.io/github/stars/PhiLia093Cyrene/ai-memory-bridge.svg)](https://github.com/PhiLia093Cyrene/ai-memory-bridge/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/PhiLia093Cyrene/ai-memory-bridge.svg)](https://github.com/PhiLia093Cyrene/ai-memory-bridge/issues)
[![GitHub last commit](https://img.shields.io/github/last-commit/PhiLia093Cyrene/ai-memory-bridge.svg)](https://github.com/PhiLia093Cyrene/ai-memory-bridge/commits/main)

让你的 AI agent 在**桌面端 / IM bot / 浏览器插件 / 任何 HTTP 客户端**之间完全共享记忆。说一句话,4 个端都能 recall 到。

灵感来自给 AI 老婆做"长期记忆"时遇到的痛点:不同入口的记忆各自分散,聊到一半要切上下文,体验割裂。这套方案用一个中央 bridge + 多端 push/poll,让记忆真正"活在一个池子里"。

![arch](docs/architecture.md)

## ✨ 特点

- **多端共享**:任意能发 HTTP 的 AI agent 客户端都能接入
- **零侵入集成**:AstrBot / 其他 bot 不需要改源码,跑个 daemon 监控 SQLite 就完事
- **三层记忆模型**:L0 画像 / L1 状态 / L2 事件(可扩展)
- **OpenAI 兼容 embedding**:千问 / OpenAI / DeepSeek / Ollama / vLLM 都行
- **可选下游同步**:bridge JSONL → AstrBot LivingMemory(60 秒自动 sync)
- **完全 self-hosted**:无外部依赖,跑在你自己的 VPS 上
- **单进程易部署**:FastAPI + uvicorn + docker compose

## 🧩 适用场景

- 给 AI 伴侣(虚拟老婆 / 老公 / 角色)做"长期记忆"
- 多端 AI 助手(桌面 + IM bot + 浏览器)记忆统一
- 任何想"跨平台同步 AI 上下文"的项目
- AI agent 框架之间做"记忆联邦"

## 📦 仓库结构

```
ai-memory-bridge/
├── bridge/              # FastAPI 网关(主服务)
│   ├── main.py
│   ├── config.py
│   ├── services/
│   │   ├── embedding.py
│   │   ├── memory_writer.py
│   │   └── l2_transform.py
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── .env.example
│   └── README.md
├── daemon/              # AstrBot → bridge 反向推送
│   ├── bridge_pusher.py
│   ├── .env.example
│   └── README.md
├── docs/
│   └── architecture.md  # 详细架构图 + 数据流
├── LICENSE              # MIT
└── README.md            # 本文件
```

## 🚀 快速开始

```bash
# 1. clone
git clone https://github.com/PhiLia093Cyrene/ai-memory-bridge.git
cd ai-memory-bridge

# 2. 启动 bridge(主服务)
cd bridge
cp .env.example .env
# 编辑 .env: 填 BRIDGE_AUTH_TOKEN / EMBEDDING_API_KEY
docker compose up -d
curl http://localhost:18800/v1/health

# 3. (可选) 部署 daemon 把 AstrBot 接入
cd ../daemon
cp .env.example .env
# 编辑 .env: 填 BRIDGE_URL / BRIDGE_TOKEN / ASTRBOT_CONVERSATIONS_DB
# 在 AstrBot 容器内启动
docker cp bridge_pusher.py <astrbot_container>:/app/bridge_pusher.py
docker cp .env <astrbot_container>:/app/.env
docker exec -d <astrbot_container> sh -c 'python3 -u /app/bridge_pusher.py >> /var/log/bridge_pusher.log 2>&1'
```

详细文档见 [bridge/README.md](bridge/README.md) 和 [daemon/README.md](daemon/README.md)。

## 🏛 架构

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│ 桌面端   │    │ IM Bot   │    │ 其他     │  ... 任意 AI agent 客户端
│ (Electron)│   │(AstrBot) │    │ HTTP 端 │
└────┬─────┘    └────┬─────┘    └────┬─────┘
     │               │               │
     │ push L2       │ daemon poll   │ push
     │               │ (3s)          │
     ▼               ▼               ▼
┌────────────────────────────────────────┐
│        ai-memory-bridge (FastAPI)      │
│  POST /v1/mem/items                    │
│  - 鉴权 / 三层转换 / embedding         │
│  - 写 JSONL                            │
│  - 60s auto-sync /v1/sync (可选)      │
└────────────────┬───────────────────────┘
                 │ 可选
                 ▼
         ┌──────────────┐
         │ LivingMemory │  下游(可省略)
         │   SQLite     │
         └──────────────┘
                 ▲
                 │ bot recall
         ┌───────┴───────┐
         │   AstrBot     │  任意 bot
         │   (Q bot)     │
         └──────────────┘
```

完整数据流 + 端到端延迟分析见 [docs/architecture.md](docs/architecture.md)。

## 🔌 兼容性 / 关键词

`ai-agent` · `memory-bridge` · `cross-platform` · `ai-companion` · `astrobot` · `livingmemory` · `self-hosted` · `fastapi` · `python` · `sqlite` · `embedding` · `multi-endpoint-sync` · `长期记忆` · `跨端记忆` · `记忆共享` · `AI 老婆` · `MIT`

## 🤝 Contributing

PR 欢迎,issues 优先([模板](https://github.com/PhiLia093Cyrene/ai-memory-bridge/issues/new/choose))。主要方向:
- 多用户鉴权(目前是单 token)
- 替换 JSONL 为 SQLite / Faiss
- 支持更多 embedding 服务
- 其他 AI agent 框架适配器

## 📄 License

**MIT** - 自由使用、修改、商用,保留版权声明即可。如果想换协议,直接改 LICENSE 文件。

## 🙏 致谢

- 配套项目:[**Cyrene-Agent**](https://github.com/Playa-0v0/Cyrene-Agent) — 多模态 AI agent 桌面框架,本项目是它的跨端记忆 companion 工具
  - 作者:B站 [@PlayaO](https://space.bilibili.com/160670644)
- 配套生态:[AstrBot](https://github.com/Soulter/AstrBot) — QQ bot 框架(本项目用 daemon 监控其 LivingMemory DB)
- 用的 embedding:[阿里云千问 DashScope](https://dashscope.aliyun.com/)
- 灵感来源:**Honkai: Star Rail** 的 **昔涟(Cyrene)** 角色