"""登录会话配置：进程环境优先于项目根目录的 .env。"""

import os
from dataclasses import dataclass

from dotenv import dotenv_values

from app.config.platform import PROJECT_ROOT


@dataclass(frozen=True)
class SessionSettings:
    ttl_seconds: int = 90
    cleanup_interval_seconds: int = 10
    heartbeat_interval_seconds: int = 30

    def __post_init__(self):
        # 建议接续时间必须早于失效时间，为网络延迟留出余量。
        if not 0 < self.heartbeat_interval_seconds < self.ttl_seconds:
            raise ValueError("心跳间隔必须大于 0 且小于会话有效期。")


def load_session_settings() -> SessionSettings:
    # 不修改进程环境，避免不同应用实例和测试相互污染。
    values = {**dotenv_values(PROJECT_ROOT / ".env", interpolate=False), **os.environ}

    def positive_integer(key: str, default: int) -> int:
        try:
            value = int(values.get(key, str(default)))
        except (ValueError, TypeError):
            raise ValueError(f"{key} 必须为正整数秒数。") from None
        if value <= 0:
            raise ValueError(f"{key} 必须为正整数秒数。")
        return value

    return SessionSettings(
        heartbeat_interval_seconds=positive_integer("PLATFORM_HEARTBEAT_INTERVAL_SECONDS", 30),
        ttl_seconds=positive_integer("PLATFORM_SESSION_TTL_SECONDS", 90),
        cleanup_interval_seconds=positive_integer("PLATFORM_SESSION_CLEANUP_INTERVAL_SECONDS", 10),
    )
