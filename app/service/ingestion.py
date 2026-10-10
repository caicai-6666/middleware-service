"""入库请求字段组装，共用附件保存与发布确认流程。"""

import asyncio
import re
from pathlib import Path
from fastapi import UploadFile
from app.config.platform import PROJECT_ROOT
from app.service.file_message import (
    FILE_EXTENSION_PATTERN, FileMessagePublisher, FileStorageError,
    MessagePublishError, InvalidFileExtensionError,
)


class InvalidSourceIdError(ValueError):
    """子平台数据标识为空或超过长度限制。"""


class IngestionService:
    def __init__(self, sessions, stream: str, directory: Path | None = None, *, deduplicator):
        self._sessions = sessions
        self._stream = stream
        self._directory = directory if directory is not None else PROJECT_ROOT / "data/ingestion-request/file"
        self._deduplicator = deduplicator

    async def submit(self, platform_code: str, access_token: str, note: str,
                     reviewer: str, file: UploadFile, file_extension: str, *, name: str, abstract: str, source_id: str) -> str:
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 128:
            raise InvalidSourceIdError()
        if re.fullmatch(FILE_EXTENSION_PATTERN, file_extension) is None:
            raise InvalidFileExtensionError()
        # 先认证与授权，再查询业务索引；重复请求不保存新附件。
        await self._sessions.check_publish_access(platform_code, access_token, self._stream)
        async with self._deduplicator.guard(platform_code, source_id):
            try:
                message_id = await FileMessagePublisher(self._sessions, self._stream, self._directory).submit(
                    platform_code, access_token,
                    {"source_id": source_id, "name": name, "abstract": abstract, "note": note, "reviewer": reviewer},
                    file, file_extension,
                )
            except MessagePublishError as error:
                self._deduplicator.remember(platform_code, source_id, error.message_id, uncertain=True)
                raise
            except asyncio.CancelledError:
                # 请求取消可能发生在等待发布确认时，不允许立即重试产生第二份申请。
                self._deduplicator.remember(platform_code, source_id, None, uncertain=True)
                raise
            self._deduplicator.remember(platform_code, source_id, message_id)
            return message_id
