# RabbitMQ Streams 本地开发环境

本文说明本项目单节点 RabbitMQ Streams 的本机开发环境、异步客户端和连接验证。五个业务流的初始化、发布及消费确认均已实现；生产部署由独立 deploy 项目维护。

## 配置与依赖

| 文件 | 职责 |
| --- | --- |
| `middleware-service-deploy/docker-compose.dev.yml` | 开发容器、固定节点名称、回环端口与独立持久卷。 |
| `middleware-service-deploy/rabbitmq/rabbitmq.dev.conf` | 开发只读挂载配置，Stream 广播地址为宿主机可达的 127.0.0.1。 |
| `middleware-service-deploy/rabbitmq/enabled_plugins` | 启用管理界面、Stream 协议和 Stream 管理插件，生产与开发共用。 |
| [Python 依赖](../../../requirement.txt) | 固定异步客户端 `rstream==1.1.0`。 |
| [环境变量模板](../../../.env.example) | 本项目的开发连接配置示例，实际凭据保存在项目根 `.env`。 |
| 本机连接检查工具（不随仓库分发） | 使用独立临时流验证发布确认与消费，最后清理临时流。 |

依赖 Docker Compose 与 Python 3.12。本地使用已有 Conda `middleware-service` 环境（Python 3.12.15），不再创建 `.venv`。镜像按 digest 固定，历史验证运行版本为 RabbitMQ 4.3.6；升级时需主动更新镜像并重新验证。

---

## 启动与连接

当前本机 IDE 开发由同级 deploy 的 `docker-compose.dev.yml` 管理，只运行 RabbitMQ 与 Redis，后端继续在宿主机启动。生产和开发共用 RabbitMQ 镜像及幂等初始化入口，开发通过只读挂载覆盖广播地址，无需复制 Dockerfile 或 bootstrap.py。

以下命令从应用项目根目录执行。先根据两仓库 `.env.example` 准备各自 `.env`，将本项目连接配置与 deploy 中的凭据、vhost 和五个流名保持一致。容器读取 deploy 的 `.env`，宿主机后端读取本项目 `.env`：

```bash
docker compose --env-file ../middleware-service-deploy/.env \
  -f ../middleware-service-deploy/docker-compose.dev.yml up -d --build --wait rabbitmq
conda activate middleware-service
python -m pip install -r requirement.txt
```

原 `data/rabbitmq-dev/` 仅保留历史占位，不作为当前启动入口；新克隆应取得 deploy 仓库或连接已初始化的 RabbitMQ。历史项目的卷不会自动迁移到新的开发项目。

| 用途 | 地址或配置 |
| --- | --- |
| Stream 协议 | `localhost:5552` |
| 管理界面 | `http://localhost:15672` |
| 用户名、密码 | 本项目根 `.env` 中的 `RABBITMQ_USER`、`RABBITMQ_PASSWORD` |
| Virtual host | 两份 .env 中一致的 RABBITMQ_VHOST，默认 `/` |
| Docker 命名卷 | `phenosolar-middleware-dev_rabbitmq-data` |
| 容器数据路径 | `/var/lib/rabbitmq` |

端口仅绑定 `127.0.0.1`，适用于宿主机上的开发程序；未发布 AMQP 5672 端口。开发账号同时用于管理与客户端验证，正式部署应另行设计最小权限账号与 TLS。

Stream 客户端会使用服务端返回的地址建立后续连接。开发 Compose 挂载 `rabbitmq/rabbitmq.dev.conf`，广播地址为 `127.0.0.1`。生产 deploy 在 `rabbitmq/Dockerfile` 中复制独立 `rabbitmq/rabbitmq.conf`，广播地址为 `rabbitmq`，不发布 RabbitMQ 端口；生产项目名为 `middleware-service`，与开发命名卷独立。统一账号密码注入及生产启动方式见[应用容器镜像](../application/container-image.md)。

默认账号环境变量只在空数据目录初始化时生效。已有持久卷时修改 `.env` 不会自动修改服务端密码，应通过管理界面或 RabbitMQ 用户管理命令同步变更。

---

## 异步客户端验证

历史联调工具使用 `asyncio`、`rstream.Producer` 与 `rstream.Consumer`，通过 `send_wait` 等待发布确认，使用 AMQP 消息编码，并从独立临时流起点读取、匹配消息内容。`scripts/` 按 Git 白名单不随仓库分发，当前工作区也未提供 `check_rabbitmq_stream.py`，不能把它作为克隆后的启动步骤。

需要重新验证时，应准备隔离检查工具，使用本项目连接配置，只创建和清理自己的临时流，不创建或修改业务流。连接检查不验证业务幂等、平台隔离或断电安全。业务接口的请求示例和确认流程见[API 导航](../../api/readme.md)。依赖一致性可执行：

```bash
conda activate middleware-service
python -m pip check
```

客户端用法参考 [rstream 项目说明](https://pypi.org/project/rstream/)；专用协议与插件配置参考 [Stream 官方文档](https://www.rabbitmq.com/docs/stream)。

---

## 运行维护与持久化边界

以下维护命令在应用项目根目录中执行。

```bash
docker compose --env-file ../middleware-service-deploy/.env -f ../middleware-service-deploy/docker-compose.dev.yml ps
docker compose --env-file ../middleware-service-deploy/.env -f ../middleware-service-deploy/docker-compose.dev.yml logs --tail=100 rabbitmq
docker compose --env-file ../middleware-service-deploy/.env -f ../middleware-service-deploy/docker-compose.dev.yml stop
docker compose --env-file ../middleware-service-deploy/.env -f ../middleware-service-deploy/docker-compose.dev.yml start
```

在上述命令前缀下执行 `down` 会删除容器和网络，但保留命名卷；后续使用相同 Compose 配置启动会复用数据。保持 Compose 项目名称与 RabbitMQ 节点名称稳定。不要使用 `down -v`，除非明确需要删除全部开发消息和账号数据。

开发与生产 Compose 均为 RabbitMQ 启用 `init: true`，将孤儿进程回收交给 Docker init。健康检查每 10 秒调用 Erlang CLI，不能仅等待 broker 主进程而忽略其他后代进程；缺少回收时可能出现 PIDs 持续增长，最终健康检查、新容器或 docker exec 因资源不足而失败。Docker 的 PIDs 包含任务和线程，不能等同于 RabbitMQ 连接数或 Erlang 进程数。

已有容器需使用同一 Compose 项目重建才能加载 init；普通 restart 不会改变该选项。从应用项目根目录执行：

```bash
docker compose --env-file ../middleware-service-deploy/.env \
  -f ../middleware-service-deploy/docker-compose.dev.yml up -d --no-deps --wait rabbitmq
```

该操作短暂中断 RabbitMQ 连接，复用原数据卷和 hostname；调用方按现有规则重连或重新登录。保留数据卷，不通过 down -v 处理进程累积。

持久卷用于容器重建后的数据保留，不提供单机突然断电时已确认消息零丢失的保证。Streams 不显式执行 `fsync`，具体边界参见[官方数据安全说明](https://www.rabbitmq.com/docs/streams#data-safety-when-using-streams)。

业务流容量与保留配置的实现状态见[流启动初始化](stream-initialization.md)；历史开发连接检查使用的临时流设置一小时保留期限并在检查结束后删除。

---

## 本次验证

2026-10-08 本机验证：容器健康检查、Stream 插件启用、异步发布确认与消费、依赖一致性检查通过。测试消息在强制重建容器后仍可读取，验证完成后已删除临时流；本次不执行真实断电测试。

上述记录属于历史验证。当前开发 Compose 的项目名为 `phenosolar-middleware-dev`，节点名为 `phenosolar-middleware-dev-rabbitmq`，与原 `middleware-service-dev` 的开发数据卷独立；切换不会自动转移旧消息、账号或 offset。生产项目仍为 `middleware-service`，生产应用镜像只依赖 Dockerfile、代码及生产注入参数，构建约定见[应用容器镜像](../application/container-image.md)。


2026-11-02 启用容器 init 后复用原开发数据卷重建 RabbitMQ，健康检查恢复，PIDs 从 52774 降至约 40；五个流属性及总计 16 条消息与重建前一致。连续五次健康检查采样均无僵尸进程，PIDs 未逐次增长。独立临时容器的 25 个孤儿进程对照中，未启用 init 时残留 25 个僵尸，启用后为 0；测试容器已清理。该结果验证进程回收机制和本次数据保留，不代替长期稳定性或异常断电验证。
