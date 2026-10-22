"""Redis 配置：进程环境优先于本项目 .env，凭据不参与对象展示。"""

import os
from dataclasses import dataclass, field

from dotenv import dotenv_values

from app.config.platform import PROJECT_ROOT


@dataclass(frozen=True)
class RedisSettings:
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = field(default="", repr=False)
    key_prefix: str = "middleware-service"
    max_connections: int = 20
    socket_timeout_seconds: float = 3.0

    def __post_init__(self):
        if (not isinstance(self.host, str) or not self.host.strip()
                or not isinstance(self.password, str) or not self.password
                or not isinstance(self.key_prefix, str) or not self.key_prefix.strip()):
            raise ValueError("Redis 地址、密码及键前缀不能为空。")
        if not 0 < self.port <= 65535 or self.db < 0 or self.max_connections <= 0:
            raise ValueError("Redis 端口、数据库编号或连接池上限不合法。")


def load_redis_settings() -> RedisSettings:
    values = {**dotenv_values(PROJECT_ROOT / ".env", interpolate=False), **os.environ}

    def integer(key: str, default: int) -> int:
        try:
            return int(values.get(key, str(default)))
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须为整数。") from None

    return RedisSettings(
        host=values.get("REDIS_HOST", "localhost"),
        port=integer("REDIS_PORT", 6379), db=integer("REDIS_DB", 0),
        password=values.get("REDIS_PASSWORD", ""),
        key_prefix=values.get("REDIS_KEY_PREFIX", "middleware-service"),
        max_connections=integer("REDIS_MAX_CONNECTIONS", 20),
    )
