"""补充 rstream 1.1.0 尚未公开的 StreamStats，只使用 Stream 原生端口。

RabbitMQ 4.3 提供末尾统计及 offset 解析，供只读扫描确定最后一条业务消息。
协议适配仅作用于此连接，不修改 rstream 全局解码器或第三方安装文件。
"""

import io
import struct
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum

from rstream import schema
from rstream.client import Client
from rstream.connection import Connection
from rstream.constants import T, OffsetType
from rstream.encoding import decode_frame


class _StatsKey(Enum):
    StreamStats = 28
    ResolveOffsetSpec = 31


@dataclass
class _StatsRequest(schema.Frame):
    key = _StatsKey.StreamStats
    correlation_id: int = field(metadata={"type": T.uint32})
    stream: str = field(metadata={"type": T.string})


@dataclass
class _StatsResponse(schema.Frame, is_response=True):
    key = _StatsKey.StreamStats
    correlation_id: int
    response_code: int
    statistics: dict[str, int]


_StatsRequest.prepare()


@dataclass
class _LastOffsetRequest(schema.Frame):
    key = _StatsKey.ResolveOffsetSpec
    correlation_id: int = field(metadata={"type": T.uint32})
    stream: str = field(metadata={"type": T.string})
    offset_spec: schema.OffsetSpec
    properties: list[schema.Property]


@dataclass
class _LastOffsetResponse(schema.Frame, is_response=True):
    key = _StatsKey.ResolveOffsetSpec
    correlation_id: int
    response_code: int
    offset_type: int
    offset: int


_LastOffsetRequest.prepare()


def _decode_last_offset(data: bytes):
    key, version, correlation, code, offset_type, offset = struct.unpack(">HHIHHQ", data)
    if key != 0x801F or version != 1 or offset_type != OffsetType.OFFSET:
        raise ValueError("ResolveOffsetSpec 响应不支持")
    return _LastOffsetResponse(correlation, code, offset_type, offset)


def _decode_stats(data: bytes) -> _StatsResponse:
    buffer = io.BytesIO(data)

    def read(size):
        value = buffer.read(size)
        if len(value) != size:
            raise ValueError("StreamStats 响应不完整")
        return value

    key, version, correlation, code, count = struct.unpack(">HHIHI", read(14))
    if key != 0x801C or version != 1:
        raise ValueError("StreamStats 响应版本不支持")
    statistics = {}
    for _ in range(count):
        length = struct.unpack(">H", read(2))[0]
        name = read(length).decode("utf-8")
        statistics[name] = struct.unpack(">q", read(8))[0]
    if buffer.read():
        raise ValueError("StreamStats 响应含多余数据")
    return _StatsResponse(correlation, code, statistics)


class _StatsConnection(Connection):
    async def read_frame(self):
        data = await self._read_frame_raw()
        if int.from_bytes(data[:2], "big") == 0x801C:
            return _decode_stats(data)
        if int.from_bytes(data[:2], "big") == 0x801F:
            return _decode_last_offset(data)
        return decode_frame(data)


class _StatsClient(Client):
    async def start(self):
        # 保留 Client 的认证、关联 ID、心跳和关闭机制，只替换本连接的解码。
        self._conn = _StatsConnection(self.host, self.port, self._ssl_context)
        await self._conn.open()
        self.start_task("listener", self._listener())
        self.add_handler(schema.Heartbeat, self._on_heartbeat)
        self.add_handler(schema.Close, self._on_close)

    async def committed_offset(self, stream: str) -> int:
        response = await self.sync_request(
            _StatsRequest(self._corr_id_seq.next(), stream), _StatsResponse,
        )
        # 精确日志末尾可能包含确认追踪记录，业务末尾由 last_message_offset 另行确定。
        if "committed_offset" not in response.statistics:
            raise ValueError("流发布去重要求 RabbitMQ 4.3 或以上提供 committed_offset")
        return response.statistics["committed_offset"]

    async def last_message_offset(self, stream: str) -> int:
        response = await self.sync_request(
            _LastOffsetRequest(self._corr_id_seq.next(), stream,
                               schema.OffsetSpec.from_params(OffsetType.LAST, None), []),
            _LastOffsetResponse,
        )
        committed = await self.committed_offset(stream)
        # LAST 无业务块时解析为 NEXT；确认记录也占用 offset，却不会交给普通消费者。
        if response.offset > committed:
            return -1
        waiter = self.wait_frame(schema.Deliver, timeout=15)
        await self.subscribe(stream=stream, subscription_id=1,
                             offset_spec=schema.OffsetSpec.from_params(OffsetType.OFFSET, response.offset),
                             initial_credit=1, properties={})
        try:
            chunk = await waiter
            if chunk.chunk_first_offset != response.offset or chunk.num_records < 1:
                raise ValueError("最后一个业务块发生变化，无法确定扫描边界")
            # LAST 是块首位置，必须读取整块头部的记录数，不能只比较其第一条消息。
            return chunk.chunk_first_offset + chunk.num_records - 1
        finally:
            await self.unsubscribe(1)


@asynccontextmanager
async def stream_stats_client(settings):
    client = _StatsClient(settings.host, settings.port, frame_max=1048576,
                          heartbeat=60, connection_name="stream-publish-deduplication")
    try:
        await client.start()
        await client.authenticate(settings.virtual_host, settings.username, settings.password)
        yield client
    finally:
        await client.close()
