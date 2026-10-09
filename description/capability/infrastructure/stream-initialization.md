# 业务流启动初始化

应用在开始接受 HTTP 请求前，连接 RabbitMQ 并确保入库请求流和审核结果流存在。此模块仅负责声明流，尚未实现业务消息发布、拉取、确认或 offset 管理。

## 实现与生命周期

`app/config/stream.py` 读取配置，`app/service/stream.py` 通过[异步客户端工厂](stream-client-factory.md)获得 `rstream.Producer` 并声明流，`app/main.py` 在 lifespan 中调用初始化。初始化连接在完成后关闭；应用关闭时不删除流。

通过 `create_stream(..., exists_ok=True)` 创建：不存在则新建，已存在则复用，不删除消息，不更新或校验已有流的保留参数。两个声明不是事务：第一个成功、第二个失败时保留第一个，下一次启动可继续完成。

初始化整体超时为 30 秒。连接、认证、权限和声明异常向上传播，阻止应用进入就绪状态。后台会话清理任务在流初始化成功后才启动。

---

## 配置

配置来自本项目根目录 `.env`，示例见 `.env.example`；进程环境变量优先于文件，不展开变量引用。密码无默认值，不记录到配置对象的 repr 中。

| 配置项 | 默认值或要求 |
| --- | --- |
| `RABBITMQ_HOST` | `localhost` |
| `RABBITMQ_PORT` | `5552`，范围 1–65535 |
| `RABBITMQ_VHOST` | `/` |
| `RABBITMQ_USER` | 必填 |
| `RABBITMQ_PASSWORD` | 必填 |
| `RABBITMQ_REQUEST_STREAM` | `ingestion-requests`，入库请求流 |
| `RABBITMQ_REGISTRY_PLATFORM_CODE` | `registry`，注册中心代号，决定登录后的读写方向 |
| `RABBITMQ_RESULT_STREAM` | `review-results`，审核结果流 |

字段不能为空，两个流名不能相同。需要修改配置时重启应用。生产容器需注入这些环境变量，并配置可从应用容器访问的 RabbitMQ 地址和 advertised host；本项目 `.env` 不会打包进镜像。部署项目已从同一份 `.env` 向 RabbitMQ 和后端注入账号密码，后端连接地址及 Stream advertised host 均为 `rabbitmq`；后端等待 RabbitMQ 健康后启动。

本项目 `.env` 和 `.env.example` 已预留以下保留配置：

| 配置项 | 示例值 | 用途 |
| --- | --- | --- |
| `RABBITMQ_STREAM_MAX_AGE` | `7D` | 消息历史保留时间，约 7 天。 |
| `RABBITMQ_STREAM_MAX_LENGTH_BYTES` | `1073741824` | 每个流约 1 GiB 的容量限制。 |

这两个变量尚未由初始化代码读取或应用到 RabbitMQ，当前仅作为配置项准备；修改或重启不会使其生效。后续接入时，已创建流需要通过保留策略更新，不能依赖 `exists_ok=True` 修改参数。

当前不设置保留时间或容量限制。新建流使用 RabbitMQ 默认值及适用的服务端策略；没有保留限制时数据会持续增长，生产使用前应确定保留策略。已有流的策略调整单独运维处理，不通过删除重建实现。

---

## 验证

执行 `python -m unittest discover -s tests -v`，验证声明参数、配置错误和启动失败传播。已在本机实际 RabbitMQ 连续执行两次应用启动生命周期，均成功，并确认两个流的类型均为 `stream`。

RabbitMQ 容器与持久化配置见[RabbitMQ Streams 本地开发环境](rabbitmq-stream.md)。
