# 中间件接入服务

基于 FastAPI 和 RabbitMQ Streams 的中间件后端，为子平台与注册中心（registry）提供统一的身份校验、消息发布、消费确认及临时附件读取入口。接入平台通过 HTTP 访问本服务，无需直接持有 RabbitMQ 连接凭据。

首次使用可从下方的本地启动步骤开始；接口对接参阅 [API 导航](description/api/readme.md)，完整设计与运行说明参阅[项目文档导航](description/readme.md)。开发前须阅读 [AGENTS.md](AGENTS.md) 和[项目目标说明](description/project.md)。

---

## 当前能力

- **平台认证**：启动时将 YAML 平台白名单加载至内存，通过平台代号和密钥登录；后续接口统一使用 Bearer Token 识别调用平台。
- **会话保活**：心跳延长会话有效期，定期清理失活平台，并释放其 Stream 客户端资源；同一平台仅允许一个有效登录会话。
- **消息流转**：支持入库申请、入库审核结果、删除申请和删除审批结果四个流，每个流提供 `publish`、`pull`、`ack` 接口。
- **消费确认**：有界批量预取、逐条交付；处理完成后显式确认，由 RabbitMQ 保存消费进度，结果流按目标平台过滤。
- **发布去重**：依据业务标识和实际接收方的消费进度，拒绝尚未确认消息的重复发布。
- **附件转存**：入库文件保存在本地临时目录，消息仅保存文件引用；registry 可下载文件，确认入库请求后清理附件。新删除申请无需上传文件。

项目目标文档包含后续建设方向；已实现能力与边界以代码和对应专题文档为准。

## 技术与目录

运行环境为 **Python 3.12**，主要依赖 FastAPI、Uvicorn、异步 Stream 客户端 `rstream` 和 PyYAML，版本固定在 [requirement.txt](requirement.txt)。当前发布去重实现依赖 **RabbitMQ 4.3 及以上版本**的 Stream 协议能力。

```text
middleware-service/
├── app/
│   ├── main.py                  # 应用装配、生命周期与开发启动入口
│   ├── router/                  # HTTP 路由与认证依赖
│   ├── service/                 # 会话、发布、消费和确认逻辑
│   ├── infrastructure/          # RabbitMQ Stream 客户端工厂
│   └── config/                  # 配置读取与校验
├── data/
│   ├── platform-config/         # 本机平台 YAML 配置
│   ├── ingestion-request/       # file/ 保存入库临时附件
│   ├── deletion-request/        # file/ 仅兼容历史删除附件
│   └── rabbitmq-dev/            # 本机自行维护的 RabbitMQ 开发配置
├── description/                 # 接口、设计与运行文档
├── .env.example                 # 本机环境配置模板
├── Dockerfile                   # 后端生产镜像
└── requirement.txt              # Python 依赖
```

`data/` 仅将直接子目录的 `.gitkeep` 纳入版本控制；实际配置、附件和本机 RabbitMQ 开发文件不随仓库分发。实际 `.env` 也不纳入 Git。

---

## 消息流与调用顺序

下表中的生产者和消费者是业务角色，底层连接由本服务管理。流名称可通过环境变量修改，HTTP 路由前缀保持不变。

| 默认流名称 | 生产者 | 消费者 | HTTP 路由前缀 |
| --- | --- | --- | --- |
| `ingestion-requests` | 子平台 | registry | `/middleware-service/api/ingestion-requests` |
| `ingestion-results` | registry | 目标子平台 | `/middleware-service/api/ingestion-results` |
| `deletion-requests` | 子平台 | registry | `/middleware-service/api/deletion-requests` |
| `deletion-results` | registry | 目标子平台 | `/middleware-service/api/deletion-results` |

典型接入流程：

1. 平台调用登录接口，保存 `access_token`，按返回的心跳间隔续期。
2. 子平台发布入库或删除申请，registry 拉取申请并完成业务处理。
3. registry 发布对应审核结果，并确认已处理的申请。
4. 子平台拉取属于自己的结果，完成本平台业务处理后确认结果。

每次 `pull` 最多交付一条消息；当前消息未 `ack` 时，再次拉取会返回该消息。`ack` 提交 `message_id`，客户端无需自行提交 offset。发布成功仅表示消息获得 RabbitMQ 发布确认，不代表审核或删除业务已经完成。

请求流按 `(platform_code, source_id)` 去重，结果流按 `(target_platform_code, request_id)` 去重；对应接收方尚未确认时重复发布返回 `409`。接收方确认后，同一业务标识可以再次发布。

入库审核结果使用 `passport` 数组支持多个通行证；删除申请也支持多个通行证，删除审批结果则使用 `approved` 表示批准或拒绝。字段和兼容规则参阅[通行证集合契约](description/architecture/data/passport.md)。

---

## 本地启动

以下命令均在本项目根目录执行。

### 1. 安装依赖

使用 Python 3.12；已有 `middleware-service` Conda 环境时直接激活，否则先创建：

```bash
conda create -n middleware-service python=3.12
conda activate middleware-service
python -m pip install -r requirement.txt
```

### 2. 准备配置

```bash
cp .env.example .env
mkdir -p data/platform-config
```

修改 `.env` 中的 RabbitMQ 地址、账号密码和流名称，使其与实际部署一致。在 `data/platform-config/platform.yaml` 创建平台白名单，例如：

```yaml
platforms:
  - code: registry
    name: 注册中心
    description: 处理入库与删除申请并发布审核结果。
    secret: "replace-with-registry-secret"
  - code: platform-a
    name: 子平台甲
    description: 提交申请并消费审核结果。
    secret: "replace-with-platform-a-secret"
```

示例密钥必须替换，各平台代号和密钥均须唯一。`RABBITMQ_REGISTRY_PLATFORM_CODE` 必须与注册中心的 `code` 一致。若配置位于其他位置，在启动进程前设置环境变量 `PLATFORM_CONFIG_PATH`，指定 YAML 路径。

常用配置见下表，完整模板以 [.env.example](.env.example) 为准。

| 配置 | 默认值或用途 |
| --- | --- |
| `PLATFORM_SESSION_TTL_SECONDS` | `90`，会话有效期，单位秒 |
| `PLATFORM_HEARTBEAT_INTERVAL_SECONDS` | `30`，建议心跳间隔，必须小于会话有效期 |
| `PLATFORM_SESSION_CLEANUP_INTERVAL_SECONDS` | `10`，失活资源清理周期，单位秒 |
| `RABBITMQ_HOST` / `RABBITMQ_PORT` | 本机默认 `localhost:5552` |
| `RABBITMQ_USER` / `RABBITMQ_PASSWORD` / `RABBITMQ_VHOST` | RabbitMQ 连接身份及虚拟主机 |
| `RABBITMQ_REGISTRY_PLATFORM_CODE` | 默认 `registry`，决定注册中心角色 |
| `*_CACHE_MAX_MESSAGES` / `*_CACHE_MAX_BYTES` | 四类消费缓存分别配置，默认每平台每流 100 条、4 MiB 原始消息 |

`RABBITMQ_STREAM_MAX_AGE` 和 `RABBITMQ_STREAM_MAX_LENGTH_BYTES` 当前仅预留配置，尚未接入流保留策略，不会自动限制消息保存期限或容量。

平台 YAML 在启动时读取一次。修改 YAML 或 `.env` 后需重启服务，具体规则见[平台身份配置](description/capability/application/platform-config.md)。

### 3. 准备 RabbitMQ

启动后端前，RabbitMQ 必须可连接，已启用 Stream 插件，且上述四个流已经存在。**流由 RabbitMQ 容器初始化，后端启动只做校验，不负责创建。**

当前工作区的本机开发配置若已准备在 `data/rabbitmq-dev/`，可执行：

```bash
docker compose --env-file .env -f data/rabbitmq-dev/docker-compose.yml up -d --build --wait rabbitmq
```

首次拉取仓库不会获得该目录内的开发配置，需要另行准备或连接已初始化的 RabbitMQ。配置组成及地址要求见 [RabbitMQ Streams 本地开发环境](description/capability/infrastructure/rabbitmq-stream.md)，容器初始化规则见[业务流启动初始化](description/capability/infrastructure/stream-initialization.md)。

### 4. 启动与验证

```bash
python -m app.main
```

本地监听 `127.0.0.1:22000`，支持 `app/` 下 Python 代码热重载。重载会清空内存会话，调用平台需要重新登录。

```bash
curl http://127.0.0.1:22000/middleware-service/api/health
```

健康接口返回 `{"status":"ok"}`。该接口用于应用存活检查，不代表对 RabbitMQ 的持续健康检查。

| 入口 | 地址 |
| --- | --- |
| 业务 API 基础路径 | `http://127.0.0.1:22000/middleware-service/api` |
| Swagger UI | `http://127.0.0.1:22000/docs` |
| ReDoc | `http://127.0.0.1:22000/redoc` |
| OpenAPI Schema | `http://127.0.0.1:22000/openapi.json` |

登录入口为 `POST /middleware-service/api/platform/login`，请求 JSON 包含 `platform_code` 和 `secret`。除登录及健康检查外，接口均通过 `Authorization: Bearer <access_token>` 认证，调用平台身份从会话中注入。完整请求示例见 [API 导航](description/api/readme.md)。

---

## 容器部署

本项目只维护后端 `Dockerfile`；生产 Compose、环境模板、平台配置模板及 RabbitMQ 镜像由独立的 `middleware-service-deploy` 项目维护。生产目录约定如下：

```text
middleware-service-deploy/
├── docker-compose.yml
├── .env.example
├── .env
├── platform.yaml.example
├── platform.yaml
├── rabbitmq/                    # RabbitMQ Dockerfile、初始化及配置
└── middleware-service/          # 将本仓库拉取到这里
    ├── Dockerfile
    └── app/
```

在部署项目目录中准备配置并启动：

```bash
cp .env.example .env
cp platform.yaml.example platform.yaml
# 编辑上述文件，填写实际平台密钥、RabbitMQ 凭据及部署参数。
docker compose up -d --build --wait
```

后端容器工作目录为 `/workspace`，监听 `8000`，默认单 worker、无热重载。平台 YAML 只读挂载至 `/data/platform-config/platform.yaml`，由 `PLATFORM_CONFIG_PATH` 指定。Compose 统一向 RabbitMQ 与后端注入凭据，后端通过内部地址 `rabbitmq:5552` 连接；生产配置不向宿主机发布 RabbitMQ 端口。

RabbitMQ 容器幂等初始化四个流并通过健康检查后，后端再启动。RabbitMQ 数据、入库临时文件和历史删除附件分别使用持久卷。完整构建、挂载与账号维护说明见[应用容器镜像](description/capability/application/container-image.md)。

## 运行边界

- 会话、预取缓存和发布并发控制均在单进程内维护，当前应保持一个后端实例、一个 worker；重启后需重新登录，消费进度从 RabbitMQ 恢复。
- 消费可能在未确认、断线或重启后再次交付，业务处理方仍需实现幂等。文件、消息与业务处理之间没有跨系统事务，不承诺端到端恰好一次处理。
- 发布确认超时可能存在结果不确定性，应结合响应中的消息标识排查，避免盲目重复提交。
- 入库附件在 registry 确认后删除，当前没有定时清理异常遗留附件的后台补偿；消息和附件均不承担永久归档职责。
- 持久卷用于容器重建后的数据保留，不等同于单节点断电零丢失；具体边界见 [RabbitMQ 持久化说明](description/capability/infrastructure/rabbitmq-stream.md#运行维护与持久化边界)。

更多接口、配置及设计说明统一从[项目文档导航](description/readme.md)进入。
