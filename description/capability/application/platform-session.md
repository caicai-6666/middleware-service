# 平台会话服务

本模块根据[平台身份配置](platform-config.md)验证平台代号与密钥，创建短期内存会话；HTTP 契约见[平台认证 API](../../api/platform.md)。

## 职责与生命周期

- `app/router/platform.py`：请求校验、响应模型及 HTTP 错误映射。
- `app/service/platform_session.py`：密钥校验、随机令牌签发、心跳续期、缓存查询和失效清理。
- `app/config/session.py`：读取并校验本项目会话配置。
- `app/main.py`：平台配置加载后创建服务，启动清理任务；关闭时取消任务并清空会话。

服务通过 `app.state.platform_sessions` 提供给路由，生命周期内复用同一实例。主缓存为 `access_token -> code`，另有平台到令牌、令牌到过期时刻两个索引。按平台使用独立的 `asyncio.Lock` 保护状态修改，确保检查活跃状态与创建会话原子执行。

令牌使用 `secrets.token_urlsafe(32)` 生成。时间判断采用单调时钟，不受系统时间校准影响。`resolve_platform()` 查询返回平台代号或 `None`，不续期；查询和登录均立即剔除过期会话，因此后台清理周期不会延长有效期。后台任务负责无人访问时的过期记录回收。`heartbeat()` 在对应平台的临界区内校验令牌并将失效时刻更新为当前时间加有效期，保留原令牌；拒绝未知、已过期或与请求平台代号不匹配的令牌，频繁心跳不累积有效期。

---

## 本项目配置

从本项目根目录 `.env` 读取以下字段，示例保存在根目录 `.env.example`；进程环境变量优先，其次 `.env`，最后代码默认值。不会把 `.env` 写入进程环境，也不展开变量引用。配置只在启动时读取，修改后需重启。

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `PLATFORM_HEARTBEAT_INTERVAL_SECONDS` | `30` | 登录响应中的建议心跳间隔秒数，必须小于会话有效期。 |
| `PLATFORM_SESSION_TTL_SECONDS` | `90` | 会话有效秒数。 |
| `PLATFORM_SESSION_CLEANUP_INTERVAL_SECONDS` | `10` | 后台清理周期秒数。 |

三个值均须为正整数，空值、非法值或心跳间隔大于等于会话有效期会阻止应用启动。可从 `.env.example` 复制为 `.env` 后修改。本项目 `.env` 已被 Git 和 Docker 构建上下文排除。容器可通过进程环境变量传入这些字段，当前未修改部署项目。

依赖 `python-dotenv`，由 `requirement.txt` 管理。此处 `.env` 读取仅针对会话设置；平台 YAML 的路径仍使用 `PLATFORM_CONFIG_PATH` 进程环境变量或默认路径。

---

## 运行边界与验证

仅支持一个后端进程、一个服务副本；不能用多个 Uvicorn workers 共享此缓存。缓存不持久化，服务重启需重新登录。当前已实现心跳续期，尚无角色授权和消息收发功能。

执行 `python -m unittest discover -s tests -v`，覆盖并发重复登录、精确过期边界、重新签发、错误密钥、定期清理、配置覆盖与非法配置、HTTP 状态、心跳续期与过期拒绝、无效心跳不延长会话及应用关闭释放缓存。


---

## 活跃平台的 Stream 资源

`app/service/platform_stream.py` 提供 `PlatformStreamManager`，登录时通过基础设施工厂建立独立 Producer、Consumer，并由中间件持有至会话失效。`app/main.py` 将该管理器注入会话服务。

| 平台 | Producer 目标 | Consumer 目标 |
| --- | --- | --- |
| 注册中心（默认 `registry`） | `review-results` | `ingestion-requests` |
| 其他白名单平台 | `ingestion-requests` | `review-results` |

注册中心代号通过 `RABBITMQ_REGISTRY_PLATFORM_CODE` 配置，默认与本地 YAML 的 `registry` 一致。流名沿用 Stream 配置。Consumer 的稳定名称为 `平台代号:流名`，不随令牌变化，供后续订阅和 offset 管理使用。

两个客户端全部建立成功后才签发令牌并开始计算有效期，建立过程整体限时 30 秒；失败会回滚已打开的资源，返回 503，平台可以重试。重复登录不会重新创建客户端，心跳仅续期。不同平台的网络连接过程不会占用同一把全局锁。

过期检查会先撤销令牌，再关闭对应客户端；后台按配置周期检测无请求的平台，因此无请求时资源回收最多会在下一个清理周期开始，连接关闭还需耗时。令牌本身仍在有效期结束时立即失效。重新登录获取新客户端；应用关闭时回收全部平台资源。关闭异常记录不含凭据的日志，且不会恢复旧会话。

当前消费者对象由中间件代平台持有，不额外启动一个独立的全局消费者。已准备消费目标和消费者名称，入库流已通过[消费服务](ingestion-consumer.md)实现有界批量预取、逐条交付与显式 offset 确认，平台失活时同步释放预取缓存；审核结果流尚未接入消费。


## 平台存活查询

`alive_snapshot()` 按单次单调时间采样返回全部配置平台的存活状态，供[查询所有平台接口](../../api/platform.md#查询所有平台)使用。此方法在事件循环内同步读取，不等待连接锁或执行网络操作；查询既不续期也不触发资源回收。过期记录即使尚未清理，也按失活返回。


## 业务接口认证依赖

`app/router/dependencies.py` 的 `require_platform` 通过 FastAPI `Depends` 解析 Bearer 令牌，从会话缓存获得平台代号，返回 `AuthenticatedPlatform`。缺少、无效或过期令牌返回 401。入库生产、心跳和平台列表接口已使用此依赖，不再接收调用方声明的 platform_code；服务层仍在落盘前和发布前复核身份与生产权限，避免长请求期间会话过期。登录仍通过 platform_code 与 secret 认证；健康检查用于容器探活，保持匿名。
