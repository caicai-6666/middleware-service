# 业务流启动初始化

四个业务流由 RabbitMQ 容器负责声明，后端只检查是否已经准备完成。流名称和 vhost 从 deploy 的同一份 `.env` 分别注入 RabbitMQ 与后端，避免初始化和读写配置不一致。

## 初始化位置与顺序

生产实现位于 `middleware-service-deploy/rabbitmq/`：

| 文件 | 职责 |
| --- | --- |
| [Dockerfile](../../../../middleware-service-deploy/rabbitmq/Dockerfile) | 基于固定 digest 的 RabbitMQ 4.3 镜像，加入 Python 标准库运行时、配置、插件与初始化入口。 |
| [bootstrap.py](../../../../middleware-service-deploy/rabbitmq/bootstrap.py) | 生成四个 Stream 的 definitions，等待 broker 启动，通过 rabbitmqctl 导入，完成后才设置健康标记。 |
| [生产 Compose](../../../../middleware-service-deploy/docker-compose.yml) | 构建 RabbitMQ 镜像，统一注入四个流名称及 vhost，后端等待 RabbitMQ 健康。 |
| `app/service/stream.py` | `verify_streams()` 在后端启动时通过 Stream 协议检查四个流，不执行创建、删除或参数更新。 |

容器先使用官方入口启动 broker，由 RabbitMQ 正常处理空数据目录中的默认账号与 vhost 初始化。随后执行 `rabbitmqctl await_startup` 和 `rabbitmqctl import_definitions`；definitions 只包含 durable、非自动删除、`x-queue-type=stream` 的四个队列，不包含账号、密码、权限或消费进度。

导入每次启动均执行，以补齐缺失流；已存在且兼容的流直接复用，不删除重建，不清空消息、offset 或生产者追踪记录。类型或声明参数不兼容时初始化失败，不覆盖原流。RabbitMQ 官方支持通过 CLI 导入 definitions，详见[官方定义导入说明](https://www.rabbitmq.com/docs/definitions#definition-import)。

初始化完成后生成 `/tmp/middleware-streams-ready`；镜像健康检查同时要求该标记存在和 `rabbitmq-diagnostics check_running` 通过。初始化失败时移除标记、停止 broker 并返回非零状态，Compose 的后端依赖不会就绪。Python 入口转发停止信号并等待 broker 退出，关闭容器不会删除数据卷。

---

## 后端职责与失败边界

`app/main.py` 在创建会话及业务服务前调用 `verify_streams()`，只通过 `Producer.stream_exists()` 验证流存在。连接、认证、权限异常或任一流缺失会阻止后端进入就绪状态，不隐式补建。检查整体超时为 30 秒，结束或失败时关闭短期客户端。

容器等待节点启动最多 120 秒，definitions 导入最多 30 秒。四个声明不是业务事务；失败可能留下部分已创建流，下一次启动继续完成，已存在的数据保留。运行时主动删除或重建流仍属于管理操作，后端去重索引需要重启恢复，不由健康检查自动修复。

---

## 配置

生产配置来自 deploy `.env`，本机开发来自本项目根 `.env`。业务后端环境变量优先于应用目录的 `.env`；RabbitMQ 镜像不依赖后端代码或 Python 依赖包。

| 配置项 | 默认值或要求 |
| --- | --- |
| `RABBITMQ_HOST` | 后端开发为 `localhost`，生产 Compose 注入 `rabbitmq`。 |
| `RABBITMQ_PORT` | `5552`，范围 1–65535。 |
| `RABBITMQ_VHOST` | `/`，同时映射为容器的 `RABBITMQ_DEFAULT_VHOST`。 |
| `RABBITMQ_USER`、`RABBITMQ_PASSWORD` | 必填，同步注入 RabbitMQ 默认账号参数与后端连接。 |
| `RABBITMQ_REGISTRY_PLATFORM_CODE` | `registry`，对应平台 YAML 的注册中心代码，仅供后端角色判断。 |
| `RABBITMQ_REQUEST_STREAM` | `ingestion-requests`。 |
| `RABBITMQ_RESULT_STREAM` | `ingestion-results`。 |
| `RABBITMQ_DELETION_REQUEST_STREAM` | `deletion-requests`。 |
| `RABBITMQ_DELETION_RESULT_STREAM` | `deletion-results`。 |

四个流名称必须为非空且互不相同，按原值使用，不裁剪空白。更换流名称不会迁移旧流的消息和 offset；后端与 RabbitMQ 容器需同步重建或重启以加载新配置。

账号及默认 vhost 仍只在空数据目录首次初始化时自动创建；definitions 不修改已有账号。已有卷更换账号、密码或 vhost 时需先进行对应的账号/vhost 权限管理，不通过删除数据卷实现。

本项目 `.env` 和 `.env.example` 预留 `RABBITMQ_STREAM_MAX_AGE=7D` 及 `RABBITMQ_STREAM_MAX_LENGTH_BYTES=1073741824`；它们尚未接入容器初始化或后端检查，修改不会改变保留策略。新流只声明类型及持久化属性，保留策略由 RabbitMQ 适用配置决定，已有流不通过启动流程调整参数。

---

## 本机开发与验证

本机配置在 `data/rabbitmq-dev/`，包含生产初始化实现的本地副本及 localhost 广播配置，不放在 deploy。开发 Dockerfile 和 bootstrap.py 的主实现来自生产 rabbitmq 目录，本地副本需同步；目录内容按 data 规则忽略且不进入后端镜像，拉取代码后由开发人员准备。

```bash
docker compose --env-file .env -f data/rabbitmq-dev/docker-compose.yml up -d --build --wait rabbitmq
```

后端单元测试覆盖只检查、不创建、缺失流失败及资源关闭。容器验证应使用独立测试卷，覆盖空卷四流创建、已存在流重复启动、消息及 offset 保留、配置错误和初始化失败阻止健康检查；验证完成后清理测试容器与测试卷。日常开发环境与生产数据卷不用于破坏性验证。

流名称、HTTP 路径分别为入库 `ingestion-requests`/`ingestion-results`、删除 `deletion-requests`/`deletion-results`；旧结果流 `review-results` 不再参与当前初始化。容器构建与部署见[应用容器镜像](../application/container-image.md)，客户端生命周期见[异步客户端工厂](stream-client-factory.md)。
