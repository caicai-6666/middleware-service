"""启动时确保业务流存在，不删除或重建已有数据。"""

import asyncio

from app.infrastructure.rabbitmq_stream import RabbitMQStreamFactory

from app.config.stream import StreamSettings


async def initialize_streams(settings: StreamSettings) -> None:
    # 限制启动等待时间；连接、权限和声明异常必须阻止应用进入就绪状态。
    async with asyncio.timeout(30):
        async with RabbitMQStreamFactory(settings).producer() as producer:
            # exists_ok 同时处理重复启动及多个初始化请求之间的创建竞争。
            # 不声明新的保留参数，避免暗中改变已有流的数据保留约定。
            await producer.create_stream(settings.request_stream, exists_ok=True)
            await producer.create_stream(settings.result_stream, exists_ok=True)
            await producer.create_stream(settings.deletion_request_stream, exists_ok=True)
            await producer.create_stream(settings.deletion_result_stream, exists_ok=True)
