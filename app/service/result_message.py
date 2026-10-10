"""结果流共用的权限校验、目标校验、去重与发布确认流程。"""

import asyncio
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


class ResultMessagePublisher:
    def __init__(self, sessions: PlatformSessionService, stream: str,
                 platforms: Mapping[str, Platform], registry_code: str, *, deduplicator):
        self._sessions = sessions
        self._stream = stream
        self._platforms = platforms
        self._registry_code = registry_code
        self._deduplicator = deduplicator

    async def submit(self, platform_code: str, access_token: str, *, request_id: str,
                     target_platform_code: str, fields: Mapping[str, object]) -> str:
        if platform_code != self._registry_code:
            raise PlatformPublishForbiddenError()
        await self._sessions.check_publish_access(platform_code, access_token, self._stream)
        if target_platform_code not in self._platforms or target_platform_code == self._registry_code:
            raise InvalidReviewTargetError()
        # 结果流依据接收子平台的确认进度去重，不能用 registry 或其他平台的 offset。
        async with self._deduplicator.guard(target_platform_code, request_id):
            message_id = uuid4().hex
            # 业务字段由各服务明确组装；公共标识最后赋值，避免业务字段覆盖它们。
            message = {
                **fields,
                "message_id": message_id,
                "request_id": request_id,
                "target_platform_code": target_platform_code,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await self._sessions.publish(
                    platform_code, access_token, self._stream,
                    json.dumps(message, ensure_ascii=False).encode("utf-8"),
                )
            except (InvalidAccessTokenError, PlatformPublishForbiddenError, PlatformResourcesUnavailableError):
                raise
            except asyncio.CancelledError:
                self._deduplicator.remember(target_platform_code, request_id, message_id, uncertain=True)
                raise
            except Exception:
                # 确认丢失不等于未写入；保留不确定占位，后续扫描核对，避免自动重发。
                self._deduplicator.remember(target_platform_code, request_id, message_id, uncertain=True)
                raise ReviewPublishError(message_id) from None
            self._deduplicator.remember(target_platform_code, request_id, message_id)
            return message_id
