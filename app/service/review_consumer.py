"""入库及删除结果共用的接收平台过滤、逐条交付及消费确认流程。"""

import asyncio
import json
from collections import deque

from rstream import exceptions

from app.config.consumer import ConsumerCacheSettings
from app.service.platform_session import (
    InvalidAccessTokenError, PlatformConsumeForbiddenError, PlatformResourcesUnavailableError,
)
from app.service.stream_consumer import (
    StreamBatchReader, Delivery, ConsumptionUnavailableError,
    InvalidStreamMessageError, AcknowledgementConflictError,
)


class ReviewConsumerService:
    def __init__(self, sessions, stream: str, registry_code: str,
                 cache_settings: ConsumerCacheSettings | None = None, wait_seconds: float = 2):
        self._sessions = sessions
        self._stream = stream
        self._registry_code = registry_code
        self._reader = StreamBatchReader(stream, cache_settings or ConsumerCacheSettings(), wait_seconds)
        self._buffers: dict[str, deque[tuple[int, bytes]]] = {}
        self._pending: dict[str, Delivery] = {}
        self._last_ack: dict[str, tuple[str, int]] = {}
        sessions.add_release_listener(self.clear_platform)

    def clear_platform(self, code: str) -> None:
        """失活时释放该平台缓存；未确认消息由 broker 进度恢复。"""
        self._buffers.pop(code, None)
        self._pending.pop(code, None)
        self._last_ack.pop(code, None)

    async def _commit(self, resources, offset: int) -> None:
        consumer = resources.consumer
        try:
            current = await consumer.query_offset(self._stream, resources.consumer_name)
        except exceptions.OffsetNotFound:
            current = None
        if current is None or current < offset:
            await consumer.store_offset(self._stream, resources.consumer_name, offset)
            current = await consumer.query_offset(self._stream, resources.consumer_name)
            if current < offset:
                raise ConsumptionUnavailableError()

    @staticmethod
    def _decode(raw: bytes) -> dict:
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError()
            for field in ("message_id", "target_platform_code"):
                if not isinstance(message.get(field), str) or not message[field].strip():
                    raise ValueError()
            if len(message["message_id"]) > 128:
                raise ValueError()
            return message
        except (ValueError, TypeError, UnicodeError):
            # 无法确定接收方的坏消息不能猜测归属后跳过。
            raise InvalidStreamMessageError() from None

    async def pull(self, code: str, token: str) -> Delivery | None:
        try:
            async with self._sessions.consumer_resources(code, token, self._stream) as resources:
                if code == self._registry_code:
                    raise PlatformConsumeForbiddenError()
                async with asyncio.timeout(15):
                    if code in self._pending:
                        return self._pending[code]
                    if not self._buffers.get(code):
                        self._buffers[code] = await self._reader.read(resources)
                    batch = self._buffers[code]
                    skipped = 0
                    skipped_offset = None
                    delivery = None
                    for offset, raw in batch:
                        message = self._decode(raw)
                        if message["target_platform_code"] == code:
                            delivery = Delivery(offset, message)
                            break
                        skipped += 1
                        skipped_offset = offset
                    if skipped_offset is not None:
                        # 只提交第一条本平台消息之前的连续无关前缀，绝不跨过待确认反馈。
                        # 回查成功后才移除，提交失败时保持原缓存供重试。
                        await self._commit(resources, skipped_offset)
                        for _ in range(skipped):
                            batch.popleft()
                    if delivery is not None:
                        self._pending[code] = delivery
                    # 一次最多扫描一个有界批次；全为其他平台时返回空，下次继续。
                    return delivery
        except (InvalidAccessTokenError, PlatformConsumeForbiddenError,
                PlatformResourcesUnavailableError, InvalidStreamMessageError):
            raise
        except Exception:
            raise ConsumptionUnavailableError() from None

    async def acknowledge(self, code: str, token: str, message_id: str) -> int:
        try:
            async with self._sessions.consumer_resources(code, token, self._stream) as resources:
                if code == self._registry_code:
                    raise PlatformConsumeForbiddenError()
                async with asyncio.timeout(10):
                    last = self._last_ack.get(code)
                    if last is not None and last[0] == message_id:
                        return last[1]
                    pending = self._pending.get(code)
                    if pending is None or pending.message["message_id"] != message_id:
                        raise AcknowledgementConflictError()
                    await self._commit(resources, pending.offset)
                    self._last_ack[code] = (message_id, pending.offset)
                    self._buffers[code].popleft()
                    del self._pending[code]
                    # 结果 ack 只确认本平台在当前流的进度，不删除流消息或审核附件。
                    return pending.offset
        except (InvalidAccessTokenError, PlatformConsumeForbiddenError,
                PlatformResourcesUnavailableError, AcknowledgementConflictError):
            raise
        except Exception:
            raise ConsumptionUnavailableError() from None
