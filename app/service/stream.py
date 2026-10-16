"""后端启动时校验容器已准备业务流，不负责创建或修改流。"""

import asyncio

from app.infrastructure.rabbitmq_stream import RabbitMQStreamFactory

from app.config.stream import StreamSettings


async def verify_streams(settings: StreamSettings) -> None:
    # 容器负责初始化；流缺失或无法连接时阻止后端进入就绪状态，不隐式补建。
    async with asyncio.timeout(30):
        async with RabbitMQStreamFactory(settings).producer() as producer:
            missing = []
            for name in (settings.request_stream, settings.result_stream,
                         settings.deletion_request_stream, settings.deletion_result_stream):
                if not await producer.stream_exists(name):
                    missing.append(name)
            if missing:
                raise RuntimeError(f"RabbitMQ 业务流尚未初始化：{', '.join(missing)}")
