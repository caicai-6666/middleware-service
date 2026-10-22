"""注册中心发布平台文件级别调整，实际业务修改由目标子平台完成。"""

from collections.abc import Mapping

from app.config.platform import Platform
from app.service.platform_session import PlatformSessionService
from app.service.result_message import ResultMessagePublisher


class FilePermissionService:
    def __init__(self, sessions: PlatformSessionService, stream: str,
                 platforms: Mapping[str, Platform], registry_code: str, *, deduplicator):
        # 该流关联子平台文件 source_id，不携带审核请求的 request_id。
        self._publisher = ResultMessagePublisher(
            sessions, stream, platforms, registry_code,
            deduplicator=deduplicator, reference_field="source_id",
        )

    async def submit(self, platform_code: str, access_token: str, *, source_id: str,
                     target_platform_code: str, file_level: int, note: str, reviewer: str) -> str:
        if not isinstance(file_level, int) or isinstance(file_level, bool):
            raise ValueError("file_level 必须为整数")
        return await self._publisher.submit(
            platform_code, access_token, request_id=source_id, target_platform_code=target_platform_code,
            fields={"file_level": file_level, "note": note, "reviewer": reviewer},
        )
