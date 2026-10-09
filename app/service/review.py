"""注册中心发布审核反馈，等待 RabbitMQ 确认写入。"""

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import uuid4

from app.config.platform import Platform
from app.service.platform_session import (
    InvalidAccessTokenError, PlatformPublishForbiddenError,
    PlatformResourcesUnavailableError, PlatformSessionService,
)


class InvalidReviewTargetError(ValueError):
    """接收方不是配置中的子平台。"""


class ReviewPublishError(Exception):
    def __init__(self, message_id: str):
        self.message_id = message_id


class ReviewService:
    def __init__(self, sessions: PlatformSessionService, stream: str,
                 platforms: Mapping[str, Platform], registry_code: str):
        self._sessions = sessions
        self._stream = stream
        self._platforms = platforms
        self._registry_code = registry_code

    async def submit(self, platform_code: str, access_token: str, *, request_id: str,
                     target_platform_code: str, note: str, reviewer: str, passport: str) -> str:
        # 注册中心代号来自统一配置，不将请求中的目标平台当成发送方身份。
        if platform_code != self._registry_code:
            raise PlatformPublishForbiddenError()
        await self._sessions.check_publish_access(platform_code, access_token, self._stream)
        if target_platform_code not in self._platforms or target_platform_code == self._registry_code:
            raise InvalidReviewTargetError()
        # 接收方可以离线；消息保存在流中，等待其重新登录后消费。
        message_id = uuid4().hex
        message = {
            "message_id": message_id,
            "request_id": request_id,
            "target_platform_code": target_platform_code,
            "note": note,
            "reviewer": reviewer,
            "passport": passport,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            # 会话服务在平台锁内再次验证身份和流权限，并等待发布确认。
            await self._sessions.publish(
                platform_code, access_token, self._stream,
                json.dumps(message, ensure_ascii=False).encode("utf-8"),
            )
        except (InvalidAccessTokenError, PlatformPublishForbiddenError, PlatformResourcesUnavailableError):
            raise
        except Exception:
            # 超时不等于未写入，不自动重发，返回标识供调用方核对。
            raise ReviewPublishError(message_id) from None
        return message_id
