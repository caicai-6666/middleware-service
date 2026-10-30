"""平台 A2A 请求队列容量、平台并发窗口与领取租约配置。"""

import os
from dataclasses import dataclass

from dotenv import dotenv_values

from app.config.platform import PROJECT_ROOT


@dataclass(frozen=True)
class PlatformA2ASettings:
    max_inflight_per_platform: int = 3
    max_tasks_per_platform: int = 1000
    lease_seconds: int = 300
    pull_receipt_ttl_seconds: int = 86400
    completion_receipt_ttl_seconds: int = 86400

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"平台 A2A 配置 {name} 必须为正整数。")
        # 限制每次原子操作需要遍历的执行记录，避免 Lua 长时间阻塞共享 Redis。
        if self.max_inflight_per_platform > 100:
            raise ValueError("PLATFORM_A2A_MAX_INFLIGHT_PER_PLATFORM 不能大于 100。")
        if self.pull_receipt_ttl_seconds < self.lease_seconds:
            raise ValueError("平台 A2A 领取回执保留时间不能小于执行租约。")


def load_platform_a2a_settings() -> PlatformA2ASettings:
    values = {**dotenv_values(PROJECT_ROOT / ".env", interpolate=False), **os.environ}

    def positive(key: str, default: int) -> int:
        try:
            value = int(values.get(key, str(default)))
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须为正整数。") from None
        if value <= 0:
            raise ValueError(f"{key} 必须为正整数。")
        return value

    return PlatformA2ASettings(
        max_inflight_per_platform=positive("PLATFORM_A2A_MAX_INFLIGHT_PER_PLATFORM", 3),
        max_tasks_per_platform=positive("PLATFORM_A2A_MAX_TASKS_PER_PLATFORM", 1000),
        lease_seconds=positive("PLATFORM_A2A_LEASE_SECONDS", 300),
        pull_receipt_ttl_seconds=positive("PLATFORM_A2A_PULL_RECEIPT_TTL_SECONDS", 86400),
        completion_receipt_ttl_seconds=positive("PLATFORM_A2A_COMPLETION_RECEIPT_TTL_SECONDS", 86400),
    )
