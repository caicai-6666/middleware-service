# FastAPI 应用基础结构

本模块提供 HTTP 应用入口、路由装配与开发启动方式。当前实现平台配置启动加载、平台登录、心跳续期与存活检查；已接入[业务流启动校验](../infrastructure/stream-initialization.md)，已实现[入库请求提交](../../api/ingestion.md)，注册中心拉取和确认接口也已实现，见[入库请求消费服务](ingestion-consumer.md)。会话缓存及后台清理见[平台会话服务](platform-session.md)。

## 代码职责

| 文件 | 职责 |
| --- | --- |
| [应用入口](../../../app/main.py) | `create_app()` 创建独立应用实例，lifespan 加载平台配置，导出 `app` 并提供模块启动入口。 |
| [路由装配](../../../app/router/__init__.py) | 汇总并注册各业务模块的 `APIRouter`。 |
| [健康检查路由](../../../app/router/health.py) | 定义存活接口和响应模型。 |

后续代码统一放在 `app/`，路由统一放在 `app/router/`，通过路由装配文件汇总；`main.py` 负责应用装配，不直接定义业务接口。采用显式注册，避免自动扫描带来的隐式依赖。统一前缀 `/middleware-service/api` 定义在 `app/main.py` 的 `API_PREFIX` 中，通过 `include_router` 应用到总路由；业务模块只定义相对路径。自动文档与 OpenAPI 使用 FastAPI 默认地址，不添加业务前缀。

[助理任务接口](../../api/assistant-task.md)在 `app/router/assistant.py` 定义，使用共享 Redis 连接池及独立的任务服务，支持 publish、pull、ack 和 status；不新增 RabbitMQ 流。

[平台 A2A 接口](../../api/platform-a2a.md)在 `app/router/platform_a2a.py` 定义，通过共同前缀 `/platform-a2a` 注册七个接口。请求 publish/pull 共用原队列，replies/publish 自动分流并完成原请求；replies/pull、replies/ack 用于首次广播或首次单聊的聚合与关闭；direct-replies/pull、direct-replies/ack 用于顶层按独立窗口持续领取单聊反馈和逐条确认。lifespan 复用连接池装配 `application.state.platform_a2a`；配置与实现见[平台 A2A 请求队列](platform-a2a.md)。

---

## 安装与启动

依赖版本由 [requirement.txt](../../../requirement.txt) 管理：FastAPI 提供 ASGI 应用，Uvicorn 负责 HTTP 服务，Pydantic 负责响应模型校验。

先按[平台身份配置](platform-config.md)填写本地 `data/platform-config/platform.yaml`，再从项目根目录执行：

```bash
conda activate middleware-service
python -m pip install -r requirement.txt
python -m app.main
```

模块启动默认监听 `127.0.0.1:22000`，以单 worker 运行，关闭热重载，修改代码后需手动重启。`app/main.py` 向 Uvicorn 传入应用导入字符串 `app.main:app`。

需要热重载时，可显式通过 Uvicorn 启动，并仅监听 `app/` 下的 Python 代码变化：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 22000 --reload --reload-dir app
```

使用模块方式启动，不使用 `python app/main.py`。服务启动需要有效的平台配置、RabbitMQ 和 Redis 连接配置，Redis 必须通过启动 PING，RabbitMQ 容器须已准备五个业务流，后端仅检查存在性。默认读取项目根目录的 `data/platform-config/platform.yaml`，密钥直接填写在 YAML 中；可通过 `PLATFORM_CONFIG_PATH` 覆盖路径。

热重载会重启服务进程，重新执行 lifespan，内存登录会话和待确认缓存会重建；子平台需要重新登录，未确认消息从 RabbitMQ 已保存进度恢复。平台 YAML 与 `.env` 不在 Python 代码监听范围内，修改后需要手动重启。容器仍使用 Dockerfile 的生产启动命令，不启用热重载。

启动后访问 `/docs` 查看 Swagger UI，`/redoc` 查看 ReDoc，`/openapi.json` 获取 OpenAPI 定义。存活检查契约见 [健康检查 API](../../api/health.md)。容器启动见[应用容器镜像](container-image.md)。


## 业务接口认证约定

后续新增业务接口默认使用 `app/router/dependencies.py` 中的 `require_platform`，通过 `Depends` 注入 `AuthenticatedPlatform`，从令牌缓存获取平台身份，不接受调用方提交的 platform_code 作为身份依据。登录接口使用平台代号和密钥；健康检查保持匿名用于容器探活。当前心跳、平台列表及入库提交均已采用统一认证。身份认证与业务权限分开，具体流访问权限继续由服务层校验。
