"""两个业务流共用的有界批量读取与消费契约。"""

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any

from rstream import ConsumerOffsetSpecification, OffsetType, exceptions
from app.config.consumer import ConsumerCacheSettings


class ConsumptionUnavailableError(Exception):
    """读取或提交消费进度失败，可稍后重试。"""


class InvalidStreamMessageError(Exception):
    """当前消息无法解析，不自动跳过。"""


class AcknowledgementConflictError(Exception):
    """请求确认的消息不是当前待确认消息。"""


@dataclass(frozen=True)
class Delivery:
    offset: int
    message: dict[str, Any]


class StreamBatchReader:
    def __init__(self, stream: str, settings: ConsumerCacheSettings, wait_seconds: float):
        self._stream = stream
        self._cache_settings = settings
        self._wait_seconds = wait_seconds

    async def _query_offset(self, consumer, name: str) -> int | None:
        try:
            return await consumer.query_offset(self._stream, name)
        except exceptions.OffsetNotFound:
            return None

    async def read(self, resources) -> deque[tuple[int, bytes]]:
        consumer = resources.consumer
        committed = await self._query_offset(consumer, resources.consumer_name)
        position = (ConsumerOffsetSpecification(OffsetType.FIRST) if committed is None
                    else ConsumerOffsetSpecification(OffsetType.OFFSET, committed + 1))
        batch: deque[tuple[int, bytes]] = deque()
        size = 0
        finished = asyncio.Event()
        oversized = False

        async def receive(message, context):
            nonlocal size, oversized
            # 一个 credit 是一个块，可能包含多条；客户端缓存必须自行限制。
            if finished.is_set():
                return
            raw = bytes(message)
            if size + len(raw) > self._cache_settings.max_bytes:
                oversized = not batch
                finished.set()
                return
            batch.append((context.offset, raw))
            size += len(raw)
            if len(batch) >= self._cache_settings.max_messages or size >= self._cache_settings.max_bytes:
                finished.set()

        subscription = await consumer.subscribe(
            self._stream, receive, subscriber_name=resources.consumer_name,
            offset_specification=position, initial_credit=1,
        )
        try:
            try:
                await asyncio.wait_for(finished.wait(), self._wait_seconds)
            except TimeoutError:
                pass
        finally:
            # 冻结缓存后停止订阅，迟到的数据不缓存、不确认，留待下批重读。
            finished.set()
            await consumer.unsubscribe(subscription)
        if oversized:
            raise InvalidStreamMessageError()
        return batch

