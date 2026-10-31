# 平台会话服务

本模块根据[平台身份配置](platform-config.md)验证平台代号与密钥，创建短期内存会话；HTTP 契约见[平台认证 API](../../api/platform.md)。

## 职责与生命周期

- `app/router/platform.py`：请求校验、响应模型及 HTTP 错误映射。
- `app/service/platform_session.py`：密钥校验、随机令牌签发、心跳续期、普通认证查询、只读身份校验和失效清理。
- `app/config/session.py`：读取并校验本项目会话配置。
- `app/main.py`：平台配置加载后创建服务，启动清理任务；关闭时取消任务并清空会话。

服务通过 `app.state.platform_sessions` 提供给路由，生命周期内复用同一实例。主缓存为 `access_token -> code`，另有平台到令牌、令牌到过期时刻两个索引。按平台使用独立的 `asyncio.Lock` 保护状态修改，确保检查活跃状态与创建会话原子执行。

令牌使用 `secrets.token_urlsafe(32)` 生成。时间判断采用单调时钟，不受系统时间校准影响。`resolve_platform()` 查询返回平台代号或 `None`，不续期；查询和登录均立即剔除过期会话，因此后台清理周期不会延长有效期。后台任务负责无人访问时的过期记录回收。`heartbeat()` 在对应平台的临界区内校验令牌并将失效时刻更新为当前时间加有效期，保留原令牌；拒绝未知、已过期或与请求平台代号不匹配的令牌，频繁心跳不累积有效期。

---

## 平台令牌只读校验

`inspect_platform(access_token)` 同步返回当前有效令牌所属的平台代码或 `None`，供注册中心通过[只读校验平台令牌接口](../../api/platform.md#只读校验平台令牌)验证跨服务调用方身份。完整 HTTP 契约以接口文档为准。

该方法在同一事件循环中无 `await` 地读取内存快照，检查服务未关闭、平台配置存在、令牌与平台双向映射一致，以及单调时钟尚未到达有效期截止时刻。到期即无效，不受后台清理是否执行的影响；旧令牌、未知令牌和不一致映射均返回 `None`。

校验不等待目标平台的资源锁，不续期、不删除过期记录、不调用释放监听器，也不创建或关闭 Stream 资源。因此即使目标平台正在进行耗时资源操作，身份校验也无需等待该操作结束。目标资源仍由既有后台清理、正常认证或重新登录流程负责回收。

接口有两个身份：header Bearer 是注册中心自己的平台令牌，body 中的 `access_token` 是需要验证的调用方令牌。只有配置的注册中心可调用；注册中心自身通过现有 `require_registry` 认证，该普通认证可能清理注册中心自己的过期会话，但不续期。目标令牌始终使用上述只读路径，成功响应仅包含 `valid`、`code`，并设置 `Cache-Control: no-store`。请求 DTO 使用 `SecretStr`，错误响应不回显请求体凭据。

身份有效只表示校验当时的会话有效，不代表用户或文件访问已获授权。注册中心仍需检查返回的平台代码、可信上下文中的用户标识及实际文件权限，不应长期缓存校验结果，也不能用心跳替代校验。

`tests/test_platform_token_verify.py` 的 9 项测试已通过，使用可控单调时钟、模拟 Stream 资源与真实 ASGI 路由，覆盖精确过期、无续期和清理副作用、不等待目标锁、令牌轮换、映射不一致、关闭状态、注册中心权限、参数保密及 OpenAPI 契约。测试不依赖真实平台登录或外部服务。

---

## 本项目配置

从本项目根目录 `.env` 读取以下字段，示例保存在根目录 `.env.example`；进程环境变量优先，其次 `.env`，最后代码默认值。不会把 `.env` 写入进程环境，也不展开变量引用。配置只在启动时读取，修改后需重启。

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `PLATFORM_HEARTBEAT_INTERVAL_SECONDS` | `30` | 登录响应中的建议心跳间隔秒数，必须小于会话有效期。 |
| `PLATFORM_SESSION_TTL_SECONDS` | `90` | 会话有效秒数。 |
| `PLATFORM_SESSION_CLEANUP_INTERVAL_SECONDS` | `10` | 后台清理周期秒数。 |

三个值均须为正整数，空值、非法值或心跳间隔大于等于会话有效期会阻止应用启动。可从 `.env.example` 复制为 `.env` 后修改。本项目 `.env` 已被 Git 和 Docker 构建上下文排除。部署 Compose 已显式注入这些字段，可在 deploy 的 `.env` 中覆盖。

依赖 `python-dotenv`，由 `requirement.txt` 管理。此处 `.env` 读取仅针对会话设置；平台 YAML 的路径仍使用 `PLATFORM_CONFIG_PATH` 进程环境变量或默认路径。

### 本机开发快速失效配置

本机开发采用以下配置，在减少心跳和清理频率的同时，保留停止心跳后较快重新登录的能力：

```dotenv
PLATFORM_SESSION_TTL_SECONDS=30
PLATFORM_SESSION_CLEANUP_INTERVAL_SECONDS=5
PLATFORM_HEARTBEAT_INTERVAL_SECONDS=5
```

本机 `.env` 已采用该组合；代码默认值仍为会话 90 秒、清理周期 10 秒、心跳间隔 30 秒，环境模板的注释提供上述开发参考值。相比原本机配置的 5 秒会话、1 秒心跳和清理周期，当前平台每 5 秒发送一次心跳，最近一次成功登录或心跳后满 30 秒即失效，为短时调度和网络延迟留出余量。普通业务请求和存活查询不续期；后台每 5 秒检查一次，无请求时在过期后的下一个周期开始回收，实际 Stream 连接关闭另需耗时。

配置在服务启动时读取，调整后需要重启一次中间件加载。后续调用平台停止心跳，等待有效期结束即可重新登录，不需要为清理旧会话重启中间件：登录会主动检查并回收过期会话，不必额外等待后台周期。有效会话期间重复登录仍返回 409；过期旧令牌不能通过心跳恢复，应重新登录取得新令牌。

这是平台登录会话的时效设置。A2A 请求和反馈租约使用 PLATFORM_A2A_LEASE_SECONDS，业务队列按各自的确认与恢复规则管理，详见[平台 A2A 请求队列](platform-a2a.md)。

---

## 运行边界与验证

仅支持一个后端进程、一个服务副本；不能用多个 Uvicorn workers 共享此缓存。缓存不持久化，服务重启需重新登录。平台登录、心跳续期和注册中心只读令牌校验均已实现；各业务接口另行校验角色和操作权限，消息能力见[API 导航](../../api/readme.md)。

执行 `python -m unittest discover -s tests -v`，覆盖并发重复登录、精确过期边界、重新签发、错误密钥、定期清理、配置覆盖与非法配置、HTTP 状态、心跳续期与过期拒绝、无效心跳不延长会话及应用关闭释放缓存。


---

## 活跃平台的 Stream 资源

`app/service/platform_stream.py` 提供 `PlatformStreamManager`，登录时通过基础设施工厂建立独立 Producer、Consumer，并由中间件持有至会话失效。`app/main.py` 将该管理器注入会话服务。

| 平台 | Producer 目标 | Consumer 目标 |
| --- | --- | --- |
| 注册中心（默认 `registry`） | `ingestion-results`、`deletion-results`、`file-permission-adjustments` | `ingestion-requests`、`deletion-requests` |
| 其他白名单平台 | `ingestion-requests`、`deletion-requests` | `ingestion-results`、`deletion-results`、`file-permission-adjustments` |

注册中心代号通过 `RABBITMQ_REGISTRY_PLATFORM_CODE` 配置，默认与本地 YAML 的 `registry` 一致。流名沿用 Stream 配置。Consumer 的稳定名称为 `平台代号:流名`，不随令牌变化，供后续订阅和 offset 管理使用。

两个客户端全部建立成功后才签发令牌并开始计算有效期，建立过程整体限时 30 秒；失败会回滚已打开的资源，返回 503，平台可以重试。重复登录不会重新创建客户端，心跳仅续期。不同平台的网络连接过程不会占用同一把全局锁。

过期检查会先撤销令牌，再关闭对应客户端；后台按配置周期检测无请求的平台，因此无请求时资源回收最多会在下一个清理周期开始，连接关闭还需耗时。令牌本身仍在有效期结束时立即失效。重新登录获取新客户端；应用关闭时回收全部平台资源。关闭异常记录不含凭据的日志，且不会恢复旧会话。

当前消费者对象由中间件代平台持有，不额外启动一个独立的全局消费者。五个业务流均已接入有界批量预取、逐条交付与显式 offset 确认，平台失活时同步释放预取缓存；入库与删除申请分别使用[入库消费服务](ingestion-consumer.md)、[删除消费服务](deletion-consumer.md)，两类审核结果及文件权限调整共用[结果消费服务](review-consumer.md)。


## 平台存活查询

`alive_snapshot()` 按单次单调时间采样返回全部配置平台的存活状态，供[查询所有平台接口](../../api/platform.md#查询所有平台)使用。路由返回全部配置平台的状态及配置中的 A2A、子管理平台与管理平台入口标记；三个标记不参与会话有效性判断。此方法在事件循环内同步读取，不等待连接锁或执行网络操作；查询既不续期也不触发资源回收。过期记录即使尚未清理，也按失活返回。


## 业务接口认证依赖

`app/router/dependencies.py` 的 `require_platform` 通过 FastAPI `Depends` 解析 Bearer 令牌，从会话缓存获得平台代号，返回 `AuthenticatedPlatform`。缺少、无效或过期令牌返回 401。入库生产、心跳和平台列表接口已使用此依赖，不再接收调用方声明的 platform_code；服务层仍在落盘前和发布前复核身份与生产权限，避免长请求期间会话过期。登录仍通过 platform_code 与 secret 认证；健康检查用于容器探活，保持匿名。

同一 Producer/Consumer 可访问多个授权流，资源对象中的 `publish_streams`/`consume_streams` 保存允许的流集合。消费时按目标流返回独立消费名称 `<code>:<stream>`，不同流的 offset 不混用。旧的单流字段保留入库方向，支持已有调用。五个业务流的 publish/pull/ack 均已提供 HTTP 入口。


## 与用户登录缓存的边界

平台心跳只续期调用平台会话，不再接收 users 或同步任何用户数据。用户资料通过独立接口维护，knowledge-accumulation 在用户实际登录成功后写入登录缓存，其他活跃平台查询有效缓存时续期，TTL 过期即退出。详见[用户 Redis 缓存](user-cache.md)。
