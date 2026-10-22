"""Redis 用户登录 TTL，进程环境优先于本项目 .env。"""

import os
from dataclasses import dataclass

from dotenv import dotenv_values

from app.config.platform import PROJECT_ROOT


@dataclass(frozen=True)
class UserCacheSettings:
    ttl_seconds: int = 90

    def __post_init__(self):
        if self.ttl_seconds <= 0:
            raise ValueError("用户缓存 TTL 必须为正整数秒数。")


def load_user_cache_settings() -> UserCacheSettings:
    values = {**dotenv_values(PROJECT_ROOT / ".env", interpolate=False), **os.environ}

    def positive(key: str, default: int) -> int:
        try:
            value = int(values.get(key, str(default)))
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须为正整数秒数。") from None
        if value <= 0:
            raise ValueError(f"{key} 必须为正整数秒数。")
        return value

    return UserCacheSettings(
        ttl_seconds=positive("USER_CACHE_TTL_SECONDS", 90),
    )
