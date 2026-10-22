# 应用容器镜像

本项目负责应用代码、[Python 依赖](../../../requirement.txt)和 [Dockerfile](../../../Dockerfile)；独立的 `middleware-service-deploy` 项目负责 `docker-compose.yml`、`.env.example`、实际 `.env` 及 RabbitMQ 配置。

## 构建与验证

部署项目也维护 Redis 镜像与持久化配置，连接与运行边界见 [Redis 容器部署](../infrastructure/redis.md)。后端通过 asyncio Redis 客户端保存用户资料和登录缓存，启动时校验连接，退出仅关闭连接池。

从应用仓库根目录执行：

```bash
docker build -t middleware-service:dev .
docker run --rm middleware-service:dev python -m pip check
docker run --rm middleware-service:dev python -c "import rstream; print('rstream import OK')"
```

镜像使用 Python 3.12 slim，工作目录及运行用户的主目录均为 `/workspace`。先安装依赖以复用构建缓存，再复制代码，并以非 root 用户运行。[构建忽略文件](../../../.dockerignore)采用默认排除策略，只放行 app 源码、依赖声明和构建描述文件；本地凭据、data、测试、脚本与文档均不进入镜像。所需附件目录由 Dockerfile 创建。

默认通过 `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` 启动，容器监听 8000 端口。镜像健康检查请求 `/middleware-service/api/health`，只检查应用存活。启动需要挂载包含平台信息与密钥的 YAML，并通过 `PLATFORM_CONFIG_PATH` 指定路径，具体命令见[平台身份配置](platform-config.md)。

HTTP 服务使用 `fastapi==0.142.4` 与 `uvicorn==0.54.0`，本地统一使用 Conda `middleware-service` 的 Python 3.12，与容器 Python 主次版本一致。应用结构与开发命令见 [FastAPI 应用基础结构](fastapi.md)。镜像构建时从 `requirement.txt` 安装依赖。

---

## 与部署项目的协作

生产部署以 deploy 仓库的 README.md 为操作入口。应用仓库拉取到部署项目下，预期目录结构为：

```text
middleware-service-deploy/
├── README.md
├── docker-compose.yml
├── .env.example
├── .env
├── platform.yaml.example
├── platform.yaml
├── rabbitmq/
│   ├── Dockerfile
│   └── bootstrap.py
├── redis/
│   ├── Dockerfile
│   ├── redis.conf
│   └── start-redis.sh
└── middleware-service/
    ├── Dockerfile
    ├── .dockerignore
    └── requirement.txt
```

部署项目仅维护生产配置，应用服务使用 `build.context: ./middleware-service` 和 `dockerfile: Dockerfile`。生产部署前将应用仓库拉取到部署目录的 `middleware-service/` 子目录，不设置本机同级目录覆盖。

Compose 默认启动后端、RabbitMQ 和 Redis，后端等待两个中间件健康后启动。平台配置只读挂载；deploy 的 `.env` 中的 `RABBITMQ_USER`、`RABBITMQ_PASSWORD` 同时注入 RabbitMQ 和后端容器。后端通过 `rabbitmq:5552` 访问服务，Stream advertised host 为 `rabbitmq`；RabbitMQ 镜像由 deploy/rabbitmq/Dockerfile 构建，容器声明五个流后才通过健康检查，后端随后启动。生产配置不发布 RabbitMQ 端口，项目名称为 `middleware-service`，与开发卷命名独立。

部署 `.env.example` 与 Compose 已同步五个流名称、`RABBITMQ_REGISTRY_PLATFORM_CODE`、`RABBITMQ_VHOST`、会话有效期/心跳/清理周期，用户登录 `USER_CACHE_TTL_SECONDS`、Redis 数据库/键前缀/连接池上限，以及 `INGESTION_CACHE_*`、`REVIEW_CACHE_*`、`DELETION_CACHE_*`、`DELETION_REVIEW_CACHE_*`、`FILE_PERMISSION_CACHE_*` 五组缓存上限。Compose 的 `.env` 用于变量插值，只有在 backend.environment 中显式声明的参数才会进入后端；应用镜像不依赖本地开发 `.env`。具体约束见[会话配置](platform-session.md)、[流配置](../infrastructure/stream-initialization.md)和[结果消费缓存配置](review-consumer.md)。

在 deploy 目录准备 `.env` 和 `platform.yaml` 后启动：

```bash
docker compose up -d --build --wait
```

RabbitMQ 的 `RABBITMQ_DEFAULT_USER/PASS` 用于空数据目录的首次初始化；已有数据卷中的用户密码不会因修改 `.env` 自动更新，轮换密码需同步修改 RabbitMQ 已有账号。不要为更改密码删除数据卷。

`RABBITMQ_VHOST` 同时注入后端和 RabbitMQ 的 `RABBITMQ_DEFAULT_VHOST`，默认 `/`；后者仅在空数据目录首次初始化时创建默认 vhost。已有卷若更换 vhost，需先创建目标 vhost 并授予现有账号权限。去重、平台会话和待确认缓存目前只支持一个后端进程，保持镜像默认的单 worker 启动方式。

本机开发配置在应用项目 `data/rabbitmq-dev/`，不放在 deploy，也不进入镜像；使用方式见[RabbitMQ 本地开发环境](../infrastructure/rabbitmq-stream.md)。


## RabbitMQ 启动依赖

五个业务流由 RabbitMQ 容器初始化；应用启动连接 RabbitMQ，只校验流是否存在。容器必须注入连接配置，具体字段见[业务流启动初始化](../infrastructure/stream-initialization.md)。根目录 `.env` 不会打包进镜像；部署项目已完成统一凭据注入与容器内地址配置。


## 上传文件持久化

后端将入库请求附件保存至 `/workspace/data/ingestion-request/file`。Dockerfile 准备可写目录，Compose 挂载 `backend-files` 命名卷；不要删除该卷，否则流中消息引用的文件将丢失。注册中心 ack 后删除对应文件，当前没有定时清理或异常遗留文件的后台补偿。

历史删除申请附件保存在 `/workspace/data/deletion-request/file`，Dockerfile 准备独立可写目录，Compose 挂载 `deletion-files` 卷。删除附件内容不进入镜像；新删除申请不再保存附件；registry 确认历史带附件申请后清理对应旧附件；当前没有定时清理或异常遗留文件补偿。


---

## 提交前验证

2026-10-22 以当前 Git 白名单内的提交候选文件导出独立构建上下文，后端镜像构建、依赖一致性检查、非 root 用户、模块导入与 OpenAPI 检查通过。镜像不包含实际配置、项目文档、本机测试、脚本或数据文件，入库与历史删除附件目录由 Dockerfile 创建。

随后在临时目录构建 RabbitMQ、Redis 与后端镜像，使用独立 Compose 项目与新卷验证首次启动、五流初始化、健康检查、用户 token/姓名查询、平台管理员同步和文件权限调整交付确认；验证通过，临时容器与数据卷已清理。生产升级步骤与完整部署记录由 deploy 仓库 README.md 维护。
