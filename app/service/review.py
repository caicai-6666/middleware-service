"""注册中心发布审核反馈，等待 RabbitMQ 确认写入。"""

from collections.abc import Mapping

from app.config.platform import Platform
from app.service.platform_session import PlatformSessionService
from app.service.passport import normalize_passports
# 保留原异常导入入口，路由及调用方继续使用相同的异常类型。
from app.service.result_message import InvalidReviewTargetError, ReviewPublishError, ResultMessagePublisher


class InvalidFileLevelError(ValueError):
    """入库审核结果的文件级别必须是整数。"""


class ReviewService:
    def __init__(self, sessions: PlatformSessionService, stream: str,
                 platforms: Mapping[str, Platform], registry_code: str, *, deduplicator):
        self._publisher = ResultMessagePublisher(sessions, stream, platforms, registry_code,
                                                deduplicator=deduplicator)

    async def submit(self, platform_code: str, access_token: str, *, request_id: str,
                     target_platform_code: str, note: str, reviewer: str, passport: str | list[str],
                     file_level: int) -> str:
        # 服务层也校验，避免内部调用绕过 HTTP 模型；bool 在 Python 中是 int 子类，需排除。
        if not isinstance(file_level, int) or isinstance(file_level, bool):
            raise InvalidFileLevelError("file_level 必须为整数")
        return await self._publisher.submit(
            platform_code, access_token, request_id=request_id, target_platform_code=target_platform_code,
            fields={"note": note, "reviewer": reviewer, "passport": normalize_passports(passport),
                    "file_level": file_level},
        )
