"""根据流中业务消息的位置及实际接收方确认进度，保护未完成消息不被重复发布。"""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass

from rstream import ConsumerOffsetSpecification, OffsetType, exceptions

from app.infrastructure.rabbitmq_stream import RabbitMQStreamFactory
from app.infrastructure.stream_stats import stream_stats_client


class DuplicateStreamMessageError(Exception):
    def __init__(self, message_id: str, offset: int | None):
        self.message_id = message_id
        self.offset = offset


class StreamDeduplicationUnavailableError(Exception):
    """无法确认消息历史或存在投递结果不确定的申请，暂不允许再次写入。"""


@dataclass(frozen=True)
class _Pending:
    message_id: str
    offset: int


class StreamPublishDeduplicator:
    def __init__(self, settings, stream: str, *, key_fields: tuple[str, str],
                 consumer_code: str | None = None, factory=None, stats_client=stream_stats_client):
        self._settings = settings
        self._factory = factory or RabbitMQStreamFactory(settings)
        self._stats_client = stats_client
        self._stream = stream
        self._key_fields = key_fields
        # 申请流固定由 registry 确认；结果流由去重键中的目标子平台各自确认。
        self._consumer_code = consumer_code
        self._pending: dict[tuple[str, str], _Pending] = {}
        self._reservations: dict[tuple[str, str], tuple[str | None, bool]] = {}
        self._inflight: dict[tuple[str, str], set[str]] = {}
        self._cursor: int | None = None
        self._refresh_lock = asyncio.Lock()
        # 固定数量的锁避免为每个历史 source_id 永久保存锁对象；同键覆盖检查及发布全程。
        self._key_locks = [asyncio.Lock() for _ in range(64)]

    async def _scan(self, consumer, start: int, tail: int):
        found = {}
        observed = set()
        finished = asyncio.Event()
        failure = None

        async def receive(raw, context):
            nonlocal failure
            if finished.is_set():
                return
            try:
                if context.offset <= tail:
                    message = json.loads(bytes(raw))
                    if not isinstance(message, dict):
                        raise ValueError("流消息不是对象")
                    # 历史消息可能缺少业务标识，不能猜测其身份，也不改写消息。
                    if self._key_fields[1] in message:
                        fields = [message.get(name) for name in (*self._key_fields, "message_id")]
                        if not all(isinstance(value, str) and value.strip() for value in fields):
                            raise ValueError("发布去重字段无效")
                        code, source_id, message_id = fields
                        found[(code, source_id)] = _Pending(message_id, context.offset)
                        observed.add(message_id)
            except Exception as error:
                # rstream 会记录并吞掉回调异常，必须显式通知调用协程失败。
                failure = error
                finished.set()
            if context.offset >= tail:
                finished.set()

        subscription = await consumer.subscribe(
            self._stream, receive, subscriber_name="middleware:publish-deduplication",
            offset_specification=ConsumerOffsetSpecification(OffsetType.OFFSET, start),
            initial_credit=1,
        )
        try:
            await asyncio.wait_for(finished.wait(), 15)
            if failure is not None:
                raise failure
        finally:
            finished.set()
            await consumer.unsubscribe(subscription)
        return found, observed

    async def _query_offset(self, consumer, code: str) -> int:
        try:
            return await consumer.query_offset(self._stream, f"{code}:{self._stream}")
        except exceptions.OffsetNotFound:
            return -1

    async def _refresh(self, owner: str | None = None):
        async with self._refresh_lock:
            try:
                async with asyncio.timeout(30), self._factory.consumer() as consumer:
                    progress = {}
                    owner = self._consumer_code or owner
                    if owner is not None:
                        progress[owner] = await self._query_offset(consumer, owner)
                    async with self._stats_client(self._settings) as stats:
                        tail = await stats.last_message_offset(self._stream)
                    # 结果流不能从本次目标的平台进度开始全局扫描，否则会漏掉其他目标消息。
                    initial = progress[self._consumer_code] if self._consumer_code is not None else -1
                    cursor = initial if self._cursor is None else self._cursor
                    if self._cursor is not None and tail < cursor:
                        raise ValueError("业务流被重建或回退，需要重启服务恢复索引")
                    found, observed = ({}, set())
                    if tail > cursor:
                        found, observed = await self._scan(consumer, cursor + 1, tail)
                    candidate = {**self._pending, **found}
                    if self._consumer_code is None:
                        # 每个目标只查询自己的持久化进度；绝不以其他目标的较大 offset 清除其消息。
                        for target in sorted({key[0] for key in candidate}):
                            if target not in progress:
                                progress[target] = await self._query_offset(consumer, target)
                # 读取完整快照及确认进度后才更新索引，失败不留下半份可信状态。
                for key, item in found.items():
                    if key in self._inflight:
                        self._inflight[key].add(item.message_id)
                self._pending = {
                    key: item for key, item in candidate.items()
                    if item.offset > progress[self._consumer_code or key[0]]
                }
                for key, (message_id, _) in list(self._reservations.items()):
                    if message_id in observed or (message_id is None and key in found):
                        del self._reservations[key]
                self._cursor = max(cursor, tail)
            except Exception:
                raise StreamDeduplicationUnavailableError() from None

    @asynccontextmanager
    async def guard(self, platform_code: str, source_id: str):
        key = (platform_code, source_id)
        async with self._key_locks[hash(key) % len(self._key_locks)]:
            await self._refresh(platform_code)
            if key in self._pending:
                item = self._pending[key]
                raise DuplicateStreamMessageError(item.message_id, item.offset)
            if key in self._reservations:
                message_id, uncertain = self._reservations[key]
                if uncertain:
                    raise StreamDeduplicationUnavailableError()
                raise DuplicateStreamMessageError(message_id, None)
            self._inflight[key] = set()
            try:
                yield
            finally:
                del self._inflight[key]

    def remember(self, platform_code: str, source_id: str, message_id: str | None, *, uncertain=False):
        # 发布确认不是流 offset；保留短期占位，下一次扫描取得真实位置后替换。
        key = (platform_code, source_id)
        observed = self._inflight.get(key, set())
        # 其他键的并发刷新可能先于发布响应看到此消息，避免重复占位永久阻塞已 ack 的申请。
        if message_id in observed or (message_id is None and observed):
            return
        self._reservations[key] = (message_id, uncertain)
