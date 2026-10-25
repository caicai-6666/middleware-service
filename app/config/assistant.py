"""个人助理任务容量、执行租约与消息合并配置。"""

import os
from dataclasses import dataclass

from dotenv import dotenv_values

from app.config.platform import PROJECT_ROOT


@dataclass(frozen=True)
class AssistantTaskSettings:
    max_tasks_per_user: int = 1000
    lease_seconds: int = 300
    receipt_ttl_seconds: int = 86400
    merge_max_messages: int = 20
    merge_max_bytes: int = 65536
    merge_interval_seconds: int = 30
    max_files_per_task: int = 10
    max_file_bytes: int = 20971520

    def __post_init__(self):
        for key, value in [("ASSISTANT_TASK_MAX_PER_USER", self.max_tasks_per_user),
                           ("ASSISTANT_TASK_LEASE_SECONDS", self.lease_seconds),
                           ("ASSISTANT_TASK_RECEIPT_TTL_SECONDS", self.receipt_ttl_seconds),
                           ("ASSISTANT_USER_MESSAGE_MERGE_MAX_MESSAGES", self.merge_max_messages),
                           ("ASSISTANT_USER_MESSAGE_MERGE_MAX_BYTES", self.merge_max_bytes),
                           ("ASSISTANT_USER_MESSAGE_MERGE_INTERVAL_SECONDS", self.merge_interval_seconds),
                           ("ASSISTANT_TASK_MAX_FILES", self.max_files_per_task),
                           ("ASSISTANT_TASK_MAX_FILE_BYTES", self.max_file_bytes)]:
            if type(value) is not int or value <= 0:
                raise ValueError(f"{key} 必须为正整数。")


def load_assistant_task_settings() -> AssistantTaskSettings:
    values = {**dotenv_values(PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    def positive(key: str, default: int) -> int:
        try:
            value = int(values.get(key, str(default)))
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须为正整数。") from None
        if value <= 0:
            raise ValueError(f"{key} 必须为正整数。")
        return value

    return AssistantTaskSettings(
        max_tasks_per_user=positive("ASSISTANT_TASK_MAX_PER_USER", 1000),
        lease_seconds=positive("ASSISTANT_TASK_LEASE_SECONDS", 300),
        receipt_ttl_seconds=positive("ASSISTANT_TASK_RECEIPT_TTL_SECONDS", 86400),
        merge_max_messages=positive("ASSISTANT_USER_MESSAGE_MERGE_MAX_MESSAGES", 20),
        merge_max_bytes=positive("ASSISTANT_USER_MESSAGE_MERGE_MAX_BYTES", 65536),
        merge_interval_seconds=positive("ASSISTANT_USER_MESSAGE_MERGE_INTERVAL_SECONDS", 30),
        max_files_per_task=positive("ASSISTANT_TASK_MAX_FILES", 10),
        max_file_bytes=positive("ASSISTANT_TASK_MAX_FILE_BYTES", 20971520),
    )
