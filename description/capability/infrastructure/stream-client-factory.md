# RabbitMQ Stream 异步客户端工厂

`app/infrastructure/rabbitmq_stream.py` 提供 `RabbitMQStreamFactory`，统一创建 `rstream.Producer` 和 `rstream.Consumer`，管理连接建立与关闭。配置使用 `app/config/stream.py` 中的 `StreamSettings`，字段说明见[业务流启动初始化](stream-initialization.md)。

## 使用方式

```python
from app.config.stream import load_stream_settings
from app.infrastructure.rabbitmq_stream import RabbitMQStreamFactory

factory = RabbitMQStreamFactory(load_stream_settings())

async def example():
    async with factory.producer() as producer:
        await producer.stream_exists("ingestion-requests")

    async with factory.consumer() as consumer:
        # 在此上下文内注册订阅、处理消息及管理消费进度。
        pass
```

工厂构造不连接网络；进入异步上下文时创建并启动客户端，退出时关闭连接。操作失败、任务取消或客户端启动部分失败时也会执行关闭逻辑，异常向上传播。

每次进入上下文获得一个独立客户端，工厂不实现单例或连接池。调用方应按服务或消费任务生命周期持有上下文，在内部复用客户端处理多条消息；不要为每条消息重新连接，也不要在退出后使用返回的对象。

---

## 职责与集成

`app/service/stream.py` 已通过工厂获得 Producer，完成启动只读检查后释放。工厂本身不创建业务流、不改变保留配置、不自动订阅、不决定平台权限，也不自动提交 offset。消费者身份、拉取和确认流程在业务服务层实现；入库流已接入逐条拉取和确认，见[消费服务](../application/ingestion-consumer.md)。

启动校验持有短期连接；平台登录后由[平台会话服务](../application/platform-session.md)长期持有独立 Producer 和 Consumer 上下文，失活或应用关闭时释放。入库请求消费服务在预取缓存耗尽时短期订阅，保留平台 Consumer 客户端供后续请求使用。

---

## 验证

`tests/test_stream.py` 覆盖连接参数、启动只读校验、异常传播以及启动或业务操作失败时的关闭行为。运行 `python -m unittest discover -s tests -v`。

本机已实测通过工厂声明现有业务流，并成功建立与关闭 Consumer 连接；端到端发布/消费确认的验证另见对应业务模块。
