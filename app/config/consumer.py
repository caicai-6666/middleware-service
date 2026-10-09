"""业务流批量预取的有界缓存配置。"""

import os
from dataclasses import dataclass
from dotenv import dotenv_values
from app.config.platform import PROJECT_ROOT


@dataclass(frozen=True)
class ConsumerCacheSettings:
    max_messages: int = 100
    max_bytes: int = 4 * 1024 * 1024

    def __post_init__(self):
        if self.max_messages <= 0 or self.max_bytes <= 0:
            raise ValueError("消费缓存消息数和字节数上限必须为正整数。")


def load_consumer_cache_settings(prefix: str = "INGESTION") -> ConsumerCacheSettings:
    values = {**dotenv_values(PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    def positive(key: str, default: int) -> int:
        try:
            result = int(values.get(key, str(default)))
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须为正整数。") from None
        if result <= 0:
            raise ValueError(f"{key} 必须为正整数。")
        return result
    return ConsumerCacheSettings(
        max_messages=positive(f"{prefix}_CACHE_MAX_MESSAGES", 100),
        max_bytes=positive(f"{prefix}_CACHE_MAX_BYTES", 4194304),
    )
