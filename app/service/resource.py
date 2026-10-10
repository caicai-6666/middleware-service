"""读取两类审核附件，限制角色、目录和文件路径。"""

import errno
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from starlette.concurrency import run_in_threadpool
from app.config.platform import PROJECT_ROOT

FILE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
FILE_EXTENSION_PATTERN = r"^[A-Za-z0-9]{1,16}$"


class ResourceForbiddenError(Exception):
    """当前平台不是注册中心。"""


class ResourceNotFoundError(Exception):
    """审核附件不存在或不是允许读取的普通文件。"""


class ResourceReadError(Exception):
    """文件无法读取。"""


@dataclass(frozen=True)
class OpenedResource:
    file: BinaryIO = field(repr=False)
    filename: str
    size: int


class ResourceService:
    def __init__(self, registry_code: str, ingestion_directory: Path | None = None,
                 deletion_directory: Path | None = None):
        self._registry_code = registry_code
        self._directories = {
            "ingestion": ingestion_directory if ingestion_directory is not None
                         else PROJECT_ROOT / "data/ingestion-request/file",
            "deletion": deletion_directory if deletion_directory is not None
                        else PROJECT_ROOT / "data/deletion-request/file",
        }

    async def open(self, code: str, kind: str, file_id: str, file_extension: str) -> OpenedResource:
        if code != self._registry_code:
            raise ResourceForbiddenError()
        if (re.fullmatch(FILE_ID_PATTERN, file_id) is None
                or re.fullmatch(FILE_EXTENSION_PATTERN, file_extension) is None):
            raise ValueError("文件标识或后缀不合法")
        filename = f"{file_id}.{file_extension.lower()}"
        directory = self._directories[kind]

        def open_file():
            # 不跟随文件符号链接，防止读出审核附件目录之外的文件。
            descriptor = None
            try:
                descriptor = os.open(directory / filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise ResourceNotFoundError()
                file = os.fdopen(descriptor, 'rb')
                descriptor = None
                return OpenedResource(file, filename, info.st_size)
            except (FileNotFoundError, NotADirectoryError):
                raise ResourceNotFoundError() from None
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise ResourceNotFoundError() from None
                raise ResourceReadError() from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)

        # 请求时打开文件，后续 ack 移除路径时，已开始的下载仍持有完整文件句柄。
        return await run_in_threadpool(open_file)
