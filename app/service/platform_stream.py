"""由中间件为每个活跃平台持有独立的 Stream 客户端。"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from collections.abc import AsyncIterator

from rstream import Consumer, Producer

from app.config.stream import StreamSettings
from app.infrastructure.rabbitmq_stream import RabbitMQStreamFactory


@dataclass(frozen=True)
class PlatformStreamResources:
    producer: Producer = field(repr=False)
    consumer: Consumer = field(repr=False)
    publish_stream: str
    consume_stream: str
    consumer_name: str
    publish_streams: frozenset[str] = field(default_factory=frozenset)
    consume_streams: frozenset[str] = field(default_factory=frozenset)


class PlatformStreamManager:
    def __init__(self, settings: StreamSettings):
        self._settings = settings
        self._factory = RabbitMQStreamFactory(settings)

    @asynccontextmanager
    async def open(self, code: str) -> AsyncIterator[PlatformStreamResources]:
        settings = self._settings
        registry = code == settings.registry_platform_code
        # 第二个客户端建立失败时，自动回收第一个；所有对象由中间件实际持有。
        async with AsyncExitStack() as stack:
            producer = await stack.enter_async_context(self._factory.producer())
            consumer = await stack.enter_async_context(self._factory.consumer())
            consume_stream = settings.request_stream if registry else settings.result_stream
            yield PlatformStreamResources(
                producer=producer, consumer=consumer,
                publish_stream=settings.result_stream if registry else settings.request_stream,
                consume_stream=consume_stream,
                consumer_name=f"{code}:{consume_stream}",
                # 一个客户端支持多个流，业务流权限按平台角色明确列举。
                publish_streams=frozenset((settings.result_stream, settings.deletion_result_stream)
                                         if registry else (settings.request_stream, settings.deletion_request_stream)),
                consume_streams=frozenset((settings.request_stream, settings.deletion_request_stream)
                                         if registry else (settings.result_stream, settings.deletion_result_stream)),
            )
