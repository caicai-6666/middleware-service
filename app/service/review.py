"""注册中心发布审核反馈，等待 RabbitMQ 确认写入。"""

from collections.abc import Mapping

from app.config.platform import Platform
from app.service.platform_session import PlatformSessionService
# 保留原异常导入入口，路由及调用方继续使用相同的异常类型。
from app.service.result_message import InvalidReviewTargetError, ReviewPublishError, ResultMessagePublisher


class ReviewService:
    def __init__(self, sessions: PlatformSessionService, stream: str,
                 platforms: Mapping[str, Platform], registry_code: str, *, deduplicator):
        self._publisher = ResultMessagePublisher(sessions, stream, platforms, registry_code,
                                                deduplicator=deduplicator)

    async def submit(self, platform_code: str, access_token: str, *, request_id: str,
                     target_platform_code: str, note: str, reviewer: str, passport: str) -> str:
        return await self._publisher.submit(
            platform_code, access_token, request_id=request_id, target_platform_code=target_platform_code,
            fields={"note": note, "reviewer": reviewer, "passport": passport},
        )
