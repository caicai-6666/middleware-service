"""共用的附件保存、消息发布确认及失败清理流程。"""

import json
import re
import shutil
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool
from app.service.platform_session import InvalidAccessTokenError, PlatformPublishForbiddenError


FILE_EXTENSION_PATTERN = r"^[A-Za-z0-9]{1,16}$"


class InvalidFileExtensionError(ValueError):
    """文件后缀不符合安全路径约束。"""


class FileStorageError(Exception):
    """上传文件保存失败。"""


class MessagePublishError(Exception):
    def __init__(self, message_id: str):
        self.message_id = message_id


class FileMessagePublisher:
    def __init__(self, sessions, stream: str, directory: Path):
        self._sessions = sessions
        self._stream = stream
        self._directory = directory

    async def submit(self, platform_code: str, access_token: str, fields: dict,
                     file: UploadFile, file_extension: str) -> str:
        # 后缀显式传入，不从文件名或媒体类型推断；服务层也校验以保护磁盘路径。
        if re.fullmatch(FILE_EXTENSION_PATTERN, file_extension) is None:
            raise InvalidFileExtensionError()
        file_extension = file_extension.lower()
        await self._sessions.check_publish_access(platform_code, access_token, self._stream)
        message_id = uuid4().hex
        path = self._directory / f"{message_id}.{file_extension}"

        def save():
            self._directory.mkdir(parents=True, exist_ok=True)
            # 服务器生成文件名，不使用客户端路径；独占创建避免覆盖已有文件。
            output = path.open("xb")
            try:
                with output:
                    shutil.copyfileobj(file.file, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise

        try:
            await run_in_threadpool(save)
        except OSError:
            raise FileStorageError() from None
        message = {
            **fields,
            "message_id": message_id,
            "platform_code": platform_code,
            "file": {"file_id": message_id, "file_extension": file_extension},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            # 保存期间可能已过期，因此发送前再次校验；锁覆盖发布确认，防止中途关闭客户端。
            await self._sessions.publish(platform_code, access_token, self._stream,
                                         json.dumps(message, ensure_ascii=False).encode("utf-8"))
        except (InvalidAccessTokenError, PlatformPublishForbiddenError):
            await run_in_threadpool(path.unlink, missing_ok=True)
            raise
        except Exception:
            # 超时可能是确认丢失，消息实际已写入；保留文件，避免产生悬空引用。
            raise MessagePublishError(message_id) from None
        return message_id
