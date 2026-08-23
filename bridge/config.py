"""cyrene-bridge 配置 - 从环境变量加载"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 网关服务
    bridge_port: int = 8000

    # 鉴权(必须从环境变量读,不能用默认值;启动时校验)
    bridge_auth_token: str = ""

    # Embedding
    embedding_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v3"
    embedding_dim: int = 1024

    # 写入开关(默认 false,防止误写)
    bridge_write_enabled: bool = False

    # 存储
    data_dir: str = "./data"
    jsonl_file: str = "./data/cyrene_bridge.jsonl"

    # LivingMemory 同步目标(可选,留空 = 禁用 /v1/sync 端点)
    livingmemory_db_path: str = ""
    livingmemory_sync_persona: str = "default"
    livingmemory_sync_session: str = "cyrene-bridge"

    # 后台自动同步(默认 60 秒一次)
    auto_sync_enabled: bool = True
    auto_sync_interval_sec: int = 60


settings = Settings()

# 启动校验
if not settings.bridge_auth_token:
    raise RuntimeError(
        "BRIDGE_AUTH_TOKEN is not set. "
        "Generate one with `openssl rand -hex 32` and put it in .env"
    )

# 确保数据目录存在
Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
