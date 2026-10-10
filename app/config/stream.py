"""RabbitMQ Stream 启动配置，环境变量优先于项目 .env。"""

import os
from dataclasses import dataclass, field

from dotenv import dotenv_values

from app.config.platform import PROJECT_ROOT


@dataclass(frozen=True)
class StreamSettings:
    username: str
    password: str = field(repr=False)
    host: str = "localhost"
    port: int = 5552
    virtual_host: str = "/"
    request_stream: str = "ingestion-requests"
    result_stream: str = "ingestion-results"
    registry_platform_code: str = "registry"
    deletion_request_stream: str = "deletion-requests"
    deletion_result_stream: str = "deletion-results"


def load_stream_settings() -> StreamSettings:
    values = {**dotenv_values(PROJECT_ROOT / ".env", interpolate=False), **os.environ}

    def value(key: str, default: str = "") -> str:
        result = values.get(key, default)
        if not isinstance(result, str) or not result.strip():
            raise ValueError(f"{key} 必须配置为非空字符串。")
        return result

    try:
        port = int(value("RABBITMQ_PORT", "5552"))
    except ValueError:
        raise ValueError("RABBITMQ_PORT 必须为有效端口。") from None
    if not 1 <= port <= 65535:
        raise ValueError("RABBITMQ_PORT 必须为有效端口。")
    settings = StreamSettings(
        registry_platform_code=value("RABBITMQ_REGISTRY_PLATFORM_CODE", "registry"),
        username=value("RABBITMQ_USER"), password=value("RABBITMQ_PASSWORD"),
        host=value("RABBITMQ_HOST", "localhost"), port=port,
        virtual_host=value("RABBITMQ_VHOST", "/"),
        request_stream=value("RABBITMQ_REQUEST_STREAM", "ingestion-requests"),
        result_stream=value("RABBITMQ_RESULT_STREAM", "ingestion-results"),
        deletion_request_stream=value("RABBITMQ_DELETION_REQUEST_STREAM", "deletion-requests"),
        deletion_result_stream=value("RABBITMQ_DELETION_RESULT_STREAM", "deletion-results"),
    )
    names = (settings.request_stream, settings.result_stream,
             settings.deletion_request_stream, settings.deletion_result_stream)
    if len(set(names)) != len(names):
        raise ValueError("四个业务流名称不能相同。")
    return settings
