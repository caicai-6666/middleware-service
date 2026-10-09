# RabbitMQ Streams 本地开发环境

本文说明本项目单节点 RabbitMQ Streams 的部署、异步客户端和连接验证。当前已实现平台登录、心跳及两个业务流的启动初始化，消息收发和交付确认尚未实现。

## 配置与依赖

| 文件 | 职责 |
| --- | --- |
| [Compose 配置](../../../../middleware-service-deploy/docker-compose.yml) | 容器、固定节点名称、本机端口、持久卷与健康检查。 |
| [RabbitMQ 配置](../../../../middleware-service-deploy/rabbitmq/rabbitmq.conf) | Stream 监听地址与宿主机客户端使用的广播地址。 |
| [插件列表](../../../../middleware-service-deploy/rabbitmq/enabled_plugins) | 启用管理界面、Stream 协议和 Stream 管理插件。 |
| [Python 依赖](../../../requirement.txt) | 固定异步客户端 `rstream==1.1.0`。 |
| [环境变量模板](../../../../middleware-service-deploy/.env.example) | 本地账号配置示例，实际凭据保存在被忽略的 `.env`。 |
| [连接检查脚本](../../../scripts/check_rabbitmq_stream.py) | 创建临时流，验证发布确认与消费，最后清理临时流。 |

依赖 Docker Compose 与 Python 3.12。本地使用已有 Conda `middleware-service` 环境（Python 3.12.15），不再创建 `.venv`。镜像按 digest 固定，本次运行版本为 RabbitMQ 4.3.6；升级时需主动更新镜像并重新验证。

---

## 启动与连接

部署配置由独立的 `middleware-service-deploy` 项目维护，应用项目只维护 Dockerfile、依赖和代码。本地两个项目同级；生产环境计划将应用仓库拉取到部署仓库的 `middleware-service/` 子目录。

以下命令从应用项目根目录执行。首次配置时在部署项目中复制 `.env.example` 为 `.env` 并设置账号密码；本机凭据已迁移，不需要重新生成。

```bash
docker compose -f ../middleware-service-deploy/docker-compose.yml -f ../middleware-service-deploy/docker-compose.dev.yml up -d --wait rabbitmq
conda activate middleware-service
python -m pip install -r requirement.txt
```

| 用途 | 地址或配置 |
| --- | --- |
| Stream 协议 | `localhost:5552` |
| 管理界面 | `http://localhost:15672` |
| 用户名、密码 | 部署项目 `.env` 中的 `RABBITMQ_USER`、`RABBITMQ_PASSWORD` |
| Virtual host | `/` |
| Docker 命名卷 | `middleware-service-dev_rabbitmq-data` |
| 容器数据路径 | `/var/lib/rabbitmq` |

端口仅绑定 `127.0.0.1`，适用于宿主机上的开发程序；未发布 AMQP 5672 端口。开发账号同时用于管理与客户端验证，正式部署应另行设计最小权限账号与 TLS。

Stream 客户端会使用服务端返回的地址建立后续连接。开发覆盖文件挂载 `rabbitmq.dev.conf`，广播地址为 `localhost`；生产基础配置使用 `rabbitmq`，不发布 RabbitMQ 端口。统一账号密码注入及生产启动方式见[应用容器镜像](../application/container-image.md)。

默认账号环境变量只在空数据目录初始化时生效。已有持久卷时修改 `.env` 不会自动修改服务端密码，应通过管理界面或 RabbitMQ 用户管理命令同步变更。

---

## 异步客户端验证

在本地生成的 `.env` 或自行维护的 shell 兼容 `.env` 中加载配置，再执行检查：

```bash
conda activate middleware-service
set -a
source ../middleware-service-deploy/.env
set +a
python scripts/check_rabbitmq_stream.py
python -m pip check
```

脚本使用 `asyncio`、`rstream.Producer` 与 `rstream.Consumer`，通过 `send_wait` 等待发布确认，使用 AMQP 消息编码，并从临时流起点读取、匹配消息内容。它不会创建或修改业务流，也不验证业务幂等、平台隔离或断电安全。

客户端用法参考 [rstream 项目说明](https://pypi.org/project/rstream/)；专用协议与插件配置参考 [Stream 官方文档](https://www.rabbitmq.com/docs/stream)。

---

## 运行维护与持久化边界

以下维护命令在 `middleware-service-deploy` 目录中执行。

```bash
docker compose ps
docker compose logs --tail=100 rabbitmq
docker compose stop
docker compose start
```

`docker compose down` 删除容器和网络，但保留命名卷；后续使用相同 Compose 配置启动会复用数据。保持 Compose 项目名称与 RabbitMQ 节点名称稳定。不要执行 `docker compose down -v`，除非明确需要删除全部开发消息和账号数据。

持久卷用于容器重建后的数据保留，不提供单机突然断电时已确认消息零丢失的保证。Streams 不显式执行 `fsync`，具体边界参见[官方数据安全说明](https://www.rabbitmq.com/docs/streams#data-safety-when-using-streams)。

后续创建业务流时必须明确容量与保留期限；本次检查临时流设置一小时保留期限并在检查结束后删除。

---

## 本次验证

2026-10-08 本机验证：容器健康检查、Stream 插件启用、异步发布确认与消费、依赖一致性检查通过。测试消息在强制重建容器后仍可读取，验证完成后已删除临时流；本次不执行真实断电测试。

迁移后保留 Compose 项目名 `middleware-service-dev`，继续复用原命名卷；生产部署应使用独立环境配置与数据卷。应用镜像构建约定见[应用容器镜像](../application/container-image.md)。
