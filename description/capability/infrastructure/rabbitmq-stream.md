# RabbitMQ Streams 本地开发环境

本文说明本项目单节点 RabbitMQ Streams 的本机开发环境、异步客户端和连接验证。四个业务流的初始化、发布及消费确认均已实现；生产部署由独立 deploy 项目维护。

## 配置与依赖

| 文件 | 职责 |
| --- | --- |
| [本机 Compose 配置](../../../data/rabbitmq-dev/docker-compose.yml) | 开发容器、固定节点名称、回环端口、持久卷与健康检查。 |
| [本机 RabbitMQ 配置](../../../data/rabbitmq-dev/rabbitmq.conf) | Stream 监听地址与宿主机客户端使用的 localhost 广播地址。 |
| [本机插件列表](../../../data/rabbitmq-dev/enabled_plugins) | 启用管理界面、Stream 协议和 Stream 管理插件。 |
| [Python 依赖](../../../requirement.txt) | 固定异步客户端 `rstream==1.1.0`。 |
| [环境变量模板](../../../.env.example) | 本项目的开发连接配置示例，实际凭据保存在项目根 `.env`。 |
| [连接检查脚本](../../../scripts/check_rabbitmq_stream.py) | 创建临时流，验证发布确认与消费，最后清理临时流。 |

依赖 Docker Compose 与 Python 3.12。本地使用已有 Conda `middleware-service` 环境（Python 3.12.15），不再创建 `.venv`。镜像按 digest 固定，本次运行版本为 RabbitMQ 4.3.6；升级时需主动更新镜像并重新验证。

---

## 启动与连接

deploy 仅维护生产配置；本机开发配置位于应用项目 `data/rabbitmq-dev/`，目录内文件按现有 data 规则忽略，只有 `.gitkeep` 进入 Git，Docker 构建也排除此目录。因此拉取应用仓库后，本机开发人员需要自行准备该目录内的 Compose、RabbitMQ 配置和插件列表；生产部署不依赖这些开发文件。

以下命令从应用项目根目录执行。首次配置时复制本项目 `.env.example` 为 `.env` 并设置账号密码；Compose 通过 `--env-file .env` 读取凭据，不依赖 deploy 的环境文件。

```bash
docker compose --env-file .env -f data/rabbitmq-dev/docker-compose.yml up -d --wait rabbitmq
conda activate middleware-service
python -m pip install -r requirement.txt
```

| 用途 | 地址或配置 |
| --- | --- |
| Stream 协议 | `localhost:5552` |
| 管理界面 | `http://localhost:15672` |
| 用户名、密码 | 本项目根 `.env` 中的 `RABBITMQ_USER`、`RABBITMQ_PASSWORD` |
| Virtual host | `/` |
| Docker 命名卷 | `middleware-service-dev_rabbitmq-data` |
| 容器数据路径 | `/var/lib/rabbitmq` |

端口仅绑定 `127.0.0.1`，适用于宿主机上的开发程序；未发布 AMQP 5672 端口。开发账号同时用于管理与客户端验证，正式部署应另行设计最小权限账号与 TLS。

Stream 客户端会使用服务端返回的地址建立后续连接。本机 Compose 挂载 `data/rabbitmq-dev/rabbitmq.conf`，广播地址为 `localhost`。生产 deploy 使用独立 `rabbitmq/rabbitmq.conf`，广播地址为 `rabbitmq`，不发布 RabbitMQ 端口；生产项目名为 `middleware-service`，与开发命名卷独立。统一账号密码注入及生产启动方式见[应用容器镜像](../application/container-image.md)。

默认账号环境变量只在空数据目录初始化时生效。已有持久卷时修改 `.env` 不会自动修改服务端密码，应通过管理界面或 RabbitMQ 用户管理命令同步变更。

---

## 异步客户端验证

在本地生成的 `.env` 或自行维护的 shell 兼容 `.env` 中加载配置，再执行检查：

```bash
conda activate middleware-service
set -a
source .env
set +a
python scripts/check_rabbitmq_stream.py
python -m pip check
```

脚本使用 `asyncio`、`rstream.Producer` 与 `rstream.Consumer`，通过 `send_wait` 等待发布确认，使用 AMQP 消息编码，并从临时流起点读取、匹配消息内容。它不会创建或修改业务流，也不验证业务幂等、平台隔离或断电安全。

客户端用法参考 [rstream 项目说明](https://pypi.org/project/rstream/)；专用协议与插件配置参考 [Stream 官方文档](https://www.rabbitmq.com/docs/stream)。

---

## 运行维护与持久化边界

以下维护命令在应用项目根目录中执行。

```bash
docker compose --env-file .env -f data/rabbitmq-dev/docker-compose.yml ps
docker compose --env-file .env -f data/rabbitmq-dev/docker-compose.yml logs --tail=100 rabbitmq
docker compose --env-file .env -f data/rabbitmq-dev/docker-compose.yml stop
docker compose --env-file .env -f data/rabbitmq-dev/docker-compose.yml start
```

在上述命令前缀下执行 `down` 会删除容器和网络，但保留命名卷；后续使用相同 Compose 配置启动会复用数据。保持 Compose 项目名称与 RabbitMQ 节点名称稳定。不要使用 `down -v`，除非明确需要删除全部开发消息和账号数据。

持久卷用于容器重建后的数据保留，不提供单机突然断电时已确认消息零丢失的保证。Streams 不显式执行 `fsync`，具体边界参见[官方数据安全说明](https://www.rabbitmq.com/docs/streams#data-safety-when-using-streams)。

业务流容量与保留配置的实现状态见[流启动初始化](stream-initialization.md)；开发连接检查脚本的临时流设置一小时保留期限并在检查结束后删除。

---

## 本次验证

2026-10-08 本机验证：容器健康检查、Stream 插件启用、异步发布确认与消费、依赖一致性检查通过。测试消息在强制重建容器后仍可读取，验证完成后已删除临时流；本次不执行真实断电测试。

开发配置移到应用项目后，继续保留 Compose 项目名 `middleware-service-dev`、节点名及原开发卷名称。生产部署使用独立配置与数据卷，生产应用镜像只依赖 Dockerfile、代码及生产注入参数；构建约定见[应用容器镜像](../application/container-image.md)。
