"""助理任务附件落盘、内容指纹与确认后的幂等清理。"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.config.platform import PROJECT_ROOT
from app.service.file_message import FILE_EXTENSION_PATTERN


class AssistantFileReference(BaseModel):
    file_id: str = Field(description="中间件生成的文件唯一标识，用于 resource/assistant-tasks 下载接口。")
    file_extension: str = Field(description="显式上传的文件后缀，小写、不带点，例如 pdf、png；不根据文件名推断。")


@dataclass(frozen=True)
class SavedAssistantFile:
    reference: AssistantFileReference
    sha256: str


class AssistantFileTooLargeError(Exception):
    """单个附件超过允许字节数。"""


class AssistantFileStorageError(Exception):
    """附件写入或清理失败。"""


class AssistantFileStore:
    def __init__(self, directory: Path | None = None, max_files: int = 10,
                 max_file_bytes: int = 20971520):
        self.directory = directory if directory is not None else PROJECT_ROOT / 'data/assistant-task/file'
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes

    def validate(self, files: list[UploadFile], extensions: list[str]) -> list[str]:
        if len(files) != len(extensions):
            raise ValueError('files 与 file_extensions 数量必须一致，并按相同顺序对应')
        if len(files) > self.max_files:
            raise ValueError(f'单次最多上传 {self.max_files} 个附件')
        if any(re.fullmatch(FILE_EXTENSION_PATTERN, extension) is None for extension in extensions):
            raise ValueError('file_extensions 必须为不带点的 1–16 位英文字母或数字')
        return [extension.lower() for extension in extensions]

    async def save(self, files: list[UploadFile], extensions: list[str]) -> list[SavedAssistantFile]:
        extensions = self.validate(files, extensions)
        if not files:
            return []

        def write_all():
            self.directory.mkdir(parents=True, exist_ok=True)
            created = []
            saved = []
            try:
                for upload, extension in zip(files, extensions, strict=True):
                    reference = AssistantFileReference(file_id=uuid4().hex, file_extension=extension)
                    path = self.directory / f'{reference.file_id}.{extension}'
                    digest = hashlib.sha256()
                    size = 0
                    with path.open('xb') as output:
                        created.append(path)
                        upload.file.seek(0)
                        while chunk := upload.file.read(1024 * 1024):
                            size += len(chunk)
                            if size > self.max_file_bytes:
                                raise AssistantFileTooLargeError()
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    saved.append(SavedAssistantFile(reference, digest.hexdigest()))
                return saved
            except BaseException:
                for path in created:
                    path.unlink(missing_ok=True)
                raise

        try:
            return await run_in_threadpool(write_all)
        except OSError:
            raise AssistantFileStorageError() from None

    async def delete(self, files: list[AssistantFileReference]) -> None:
        if not files:
            return
        def remove_all():
            for reference in files:
                # 只接受服务端生成的引用，防止异常缓存把删除目标引出附件目录。
                if (re.fullmatch(r'[a-f0-9]{32}', reference.file_id) is None
                        or re.fullmatch(FILE_EXTENSION_PATTERN, reference.file_extension) is None):
                    raise AssistantFileStorageError()
                (self.directory / f'{reference.file_id}.{reference.file_extension}').unlink(missing_ok=True)
        try:
            await run_in_threadpool(remove_all)
        except OSError:
            raise AssistantFileStorageError() from None
