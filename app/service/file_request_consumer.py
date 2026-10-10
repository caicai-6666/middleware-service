"""申请流共用的有界预取、逐条交付、确认及附件清理。"""

import asyncio
import json
import re
from pathlib import Path
from starlette.concurrency import run_in_threadpool
from collections import deque
from app.config.consumer import ConsumerCacheSettings

from rstream import exceptions
from app.service.stream_consumer import (
    StreamBatchReader, Delivery, ConsumptionUnavailableError,
    InvalidStreamMessageError, AcknowledgementConflictError,
)

from app.service.platform_session import (
    InvalidAccessTokenError, PlatformConsumeForbiddenError, PlatformResourcesUnavailableError,
)


class AcknowledgedFileCleanupError(Exception):
    """offset 已确认但文件清理失败，可重试同一 ack。"""


class FileRequestConsumerService:
    def __init__(self, sessions, stream: str, *, file_directory: Path,
                 wait_seconds: float = 2, cache_settings: ConsumerCacheSettings | None = None,
                 registry_code: str | None = None):
        self._file_directory = file_directory
        self._registry_code = registry_code
        self._sessions = sessions
        self._stream = stream
        self._wait_seconds = wait_seconds
        self._cache_settings = cache_settings or ConsumerCacheSettings()
        # 缓存原始字节以精确约束负载大小；只解码当前交付消息。
        self._buffers: dict[str, deque[tuple[int, bytes]]] = {}
        self._sessions.add_release_listener(self.clear_platform)
        self._pending: dict[str, Delivery] = {}
        self._last_ack: dict[str, tuple[str, int]] = {}

    async def _query_offset(self, consumer, name: str) -> int | None:
        try:
            return await consumer.query_offset(self._stream, name)
        except exceptions.OffsetNotFound:
            return None

    def clear_platform(self, code: str) -> None:
        """失活或停机时释放所有预取数据，重新登录从已提交进度恢复。"""
        self._buffers.pop(code, None)
        self._pending.pop(code, None)
        self._last_ack.pop(code, None)

    async def _prefetch(self, resources) -> deque[tuple[int, bytes]]:
        reader = StreamBatchReader(self._stream, self._cache_settings, self._wait_seconds)
        return await reader.read(resources)

    async def pull(self, code: str, token: str) -> Delivery | None:
        try:
            async with self._sessions.consumer_resources(code, token, self._stream) as resources:
                if self._registry_code is not None and code != self._registry_code:
                    raise PlatformConsumeForbiddenError()
                if code in self._pending:
                    return self._pending[code]
                if not self._buffers.get(code):
                    async with asyncio.timeout(15):
                        self._buffers[code] = await self._prefetch(resources)
                batch = self._buffers[code]
                if not batch:
                    return None
                offset, raw = batch[0]
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict) or not isinstance(message.get("message_id"), str) or not message["message_id"]:
                        raise ValueError()
                except (ValueError, TypeError, UnicodeError):
                    raise InvalidStreamMessageError() from None
                delivery = Delivery(offset, message)
                self._pending[code] = delivery
                return delivery
        except (InvalidAccessTokenError, PlatformConsumeForbiddenError,
                PlatformResourcesUnavailableError, InvalidStreamMessageError):
            raise
        except Exception:
            raise ConsumptionUnavailableError() from None

    def _file_path(self, message: dict) -> Path | None:
        file = message.get("file")
        if file is None:
            # 兼容没有附件引用的历史消息。
            return None
        if not isinstance(file, dict):
            raise InvalidStreamMessageError()
        file_id = file.get("file_id")
        if (not isinstance(file_id, str) or file_id != message["message_id"]
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", file_id) is None):
            raise InvalidStreamMessageError()
        extension = file.get("file_extension")
        if extension is None:
            return self._file_directory / file_id
        if not isinstance(extension, str) or re.fullmatch(r"[a-z0-9]{1,16}", extension) is None:
            raise InvalidStreamMessageError()
        return self._file_directory / f"{file_id}.{extension}"

    async def acknowledge(self, code: str, token: str, message_id: str) -> int:
        try:
            async with self._sessions.consumer_resources(code, token, self._stream) as resources:
                if self._registry_code is not None and code != self._registry_code:
                    raise PlatformConsumeForbiddenError()
                async with asyncio.timeout(10):
                    # 最近一次确认允许重试，即使下一条已经被拉取，也不会推进其 offset。
                    last = self._last_ack.get(code)
                    if last is not None and last[0] == message_id:
                        return last[1]
                    pending = self._pending.get(code)
                    if pending is None or pending.message["message_id"] != message_id:
                        raise AcknowledgementConflictError()
                    file_path = self._file_path(pending.message)
                    consumer = resources.consumer
                    committed = await self._query_offset(consumer, resources.consumer_name)
                    if committed is None or committed < pending.offset:
                        await consumer.store_offset(self._stream, resources.consumer_name, pending.offset)
                        # store_offset 无业务响应帧；回查后才能向 HTTP 调用方报告成功。
                        committed = await self._query_offset(consumer, resources.consumer_name)
                        if committed is None or committed < pending.offset:
                            raise ConsumptionUnavailableError()
                    if file_path is not None:
                        try:
                            # 只有 offset 确认成功后才删文件；不存在视为已完成，支持重复请求。
                            await run_in_threadpool(file_path.unlink, missing_ok=True)
                        except OSError:
                            raise AcknowledgedFileCleanupError() from None
                    self._last_ack[code] = (message_id, pending.offset)
                    self._buffers[code].popleft()
                    del self._pending[code]
                    return pending.offset
        except (InvalidAccessTokenError, PlatformConsumeForbiddenError,
                PlatformResourcesUnavailableError, AcknowledgementConflictError,
                InvalidStreamMessageError, AcknowledgedFileCleanupError):
            raise
        except Exception:
            raise ConsumptionUnavailableError() from None
