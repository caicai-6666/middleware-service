"""RabbitMQ Stream 异步客户端工厂，统一连接参数与资源释放。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from rstream import Consumer, Producer

from app.config.stream import StreamSettings


class RabbitMQStreamFactory:
    def __init__(self, settings: StreamSettings):
        self._settings = settings

    @asynccontextmanager
    async def producer(self) -> AsyncIterator[Producer]:
        """进入上下文时连接，退出或操作失败时关闭；上下文内可连续发布。"""
        settings = self._settings
        client = Producer(
            host=settings.host, port=settings.port, vhost=settings.virtual_host,
            username=settings.username, password=settings.password,
        )
        try:
            await client.start()
            yield client
        finally:
            # start 部分成功后抛错时，也尝试回收已经创建的连接。
            await client.close()

    @asynccontextmanager
    async def consumer(self) -> AsyncIterator[Consumer]:
        """为调用方提供独立消费者；订阅、确认和 offset 由上层负责。"""
        settings = self._settings
        client = Consumer(
            host=settings.host, port=settings.port, vhost=settings.virtual_host,
            username=settings.username, password=settings.password,
        )
        try:
            await client.start()
            yield client
        finally:
            await client.close()
