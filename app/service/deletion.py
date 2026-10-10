"""子平台提交删除申请及审核附件，不执行业务文件删除。"""

import asyncio
import re
from pathlib import Path
from fastapi import UploadFile
from app.config.platform import PROJECT_ROOT
from app.service.file_message import (
    FILE_EXTENSION_PATTERN, FileMessagePublisher, InvalidFileExtensionError, MessagePublishError,
)
from app.service.platform_session import PlatformPublishForbiddenError


class InvalidDeletionRequestError(ValueError):
    """删除申请业务字段不符合契约。"""


class DeletionService:
    def __init__(self, sessions, stream: str, registry_code: str, directory: Path | None = None, *, deduplicator):
        self._sessions = sessions
        self._stream = stream
        self._registry_code = registry_code
        self._deduplicator = deduplicator
        self._directory = directory if directory is not None else PROJECT_ROOT / "data/deletion-request/file"

    async def submit(self, platform_code: str, access_token: str, *, source_id: str,
                     passport: str, name: str, abstract: str, uploader: str, applicant: str,
                     note: str, file: UploadFile, file_extension: str) -> str:
        if platform_code == self._registry_code:
            raise PlatformPublishForbiddenError()
        fields = {"source_id": source_id, "passport": passport, "name": name, "abstract": abstract,
                  "uploader": uploader, "applicant": applicant, "note": note}
        # 服务层复查核心约束，防止非 HTTP 调用写入无效消息。
        limits = {"source_id": 128, "passport": None, "name": 200,
                  "abstract": 10000, "uploader": 200, "applicant": 200}
        for key, maximum in limits.items():
            value = fields[key]
            if not isinstance(value, str) or not value.strip() or (maximum is not None and len(value) > maximum):
                raise InvalidDeletionRequestError(key)
        if not isinstance(note, str) or len(note) > 10000:
            raise InvalidDeletionRequestError("note")
        if re.fullmatch(FILE_EXTENSION_PATTERN, file_extension) is None:
            raise InvalidFileExtensionError()
        await self._sessions.check_publish_access(platform_code, access_token, self._stream)
        # 与入库申请同样在落盘前拒绝重复，两个流分别查询 registry 各自的进度。
        async with self._deduplicator.guard(platform_code, source_id):
            try:
                message_id = await FileMessagePublisher(self._sessions, self._stream, self._directory).submit(
                    platform_code, access_token, fields, file, file_extension,
                )
            except MessagePublishError as error:
                self._deduplicator.remember(platform_code, source_id, error.message_id, uncertain=True)
                raise
            except asyncio.CancelledError:
                self._deduplicator.remember(platform_code, source_id, None, uncertain=True)
                raise
            self._deduplicator.remember(platform_code, source_id, message_id)
            return message_id
