"""删除审批结果字段组装，发布本身不执行子平台的删除操作。"""

from collections.abc import Mapping

from app.config.platform import Platform
from app.service.platform_session import PlatformSessionService
from app.service.result_message import ResultMessagePublisher


class DeletionReviewService:
    def __init__(self, sessions: PlatformSessionService, stream: str,
                 platforms: Mapping[str, Platform], registry_code: str, *, deduplicator):
        self._publisher = ResultMessagePublisher(sessions, stream, platforms, registry_code,
                                                deduplicator=deduplicator)

    async def submit(self, platform_code: str, access_token: str, *, request_id: str,
                     target_platform_code: str, approved: bool, note: str, reviewer: str) -> str:
        return await self._publisher.submit(
            platform_code, access_token, request_id=request_id, target_platform_code=target_platform_code,
            fields={"approved": approved, "note": note, "reviewer": reviewer},
        )
