# 文件权限调整消息契约

本流用于注册中心向子平台通知一个文件的新权限等级。默认流名 `file-permission-adjustments`，registry 为生产方，所有其他配置平台为潜在接收方。HTTP 契约见[文件权限调整 API](../../api/file-permission.md)。

## 消息字段

消息为 UTF-8 JSON 对象，无文件附件：

| 字段 | 类型 | 来源与含义 |
| --- | --- | --- |
| `message_id` | string | 中间件生成的 UUID 十六进制标识，用于消费确认。 |
| `target_platform_code` | string | 已配置子平台代码，不能为注册中心。 |
| `source_id` | string | 文件在目标子平台内的唯一 ID，与目标平台共同定位文件。 |
| `file_level` | integer | 调整后的文件等级，不限制等级范围；严格整数，不接受布尔或浮点值。 |
| `reviewer` | string | 注册中心操作人姓名或代号。 |
| `note` | string | 调整备注，允许空字符串。 |
| `created_at` | string | 中间件生成的带时区 UTC ISO 8601 时间。 |

不携带 request_id、passport 或文件内容。文件当前等级与存在性由目标平台检查，中间件不存储文件权限状态。

---

## 发布、消费与幂等

`app/service/file_permission.py` 明确组装业务字段，复用 `ResultMessagePublisher` 的注册中心身份校验、目标校验、消息 ID/时间生成与 RabbitMQ 发布确认；公共发布器通过 reference_field 选择 source_id，不改变其他结果流的 request_id 契约。

发布去重键为 `(target_platform_code, source_id)`，使用目标平台在本流的 RabbitMQ offset。未确认的同文件调整拒绝重复写入；目标 ack 后可再次调整。索引按流恢复，发布确认不确定时保留占位，避免自动重发；机制见[发布去重服务](../../capability/application/stream-publish-deduplication.md)。

子平台消费复用 `ReviewConsumerService`，按 target_platform_code 过滤，使用独立有界缓存和 `<platform_code>:<stream>` 消费名称。未 ack 前重复 pull 返回同一条，失活释放缓存，重新登录后从 RabbitMQ 确认进度恢复。中间件负责可靠交付，子平台负责实际修改与业务幂等；不承诺端到端恰好一次。

---

## 配置与装配

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `RABBITMQ_FILE_PERMISSION_STREAM` | `file-permission-adjustments` | 后端与 RabbitMQ 容器使用同一个流名。 |
| `FILE_PERMISSION_CACHE_MAX_MESSAGES` | `100` | 每个子平台在该流的一批预取消息上限。 |
| `FILE_PERMISSION_CACHE_MAX_BYTES` | `4194304` | 每个子平台的一批原始消息字节上限。 |

`app/main.py` 装配 file_permission 发布服务与 file_permission_consumer 消费实例；平台登录后由 `PlatformStreamManager` 授予 registry 发布权限、子平台消费权限。沿用已有客户端，同一平台不额外创建一组连接。失活时消费缓存及客户端一同释放。

本机配置写在项目 `.env`，示例见 `.env.example`。生产配置在同级 deploy，RabbitMQ 初始化脚本幂等声明五个流，后端只读校验，不创建流。升级需先重建并启动 deploy 的 RabbitMQ 镜像，等待健康后再启动后端；已有消息及 offset 保留。更改流名不会迁移历史消息或 offset，见[流初始化](../../capability/infrastructure/stream-initialization.md)。


---

## 验证

单元测试覆盖严格整数、字段缺失、发布角色、目标校验、同文件未确认去重、不同平台进度隔离、确认不确定、并发发布、消费屏障与失活恢复。容器声明测试覆盖五个流及自定义名称、重复名称拒绝、后端只读检查缺失流失败；Compose 配置验证后端与 broker 收到同一流名。

本机真实 RabbitMQ 联调使用独立临时流，验证 HTTP 发布、双平台过滤、重复 pull、跨平台 ack 拒绝、确认后再次调整，以及发布/消费服务重建后的恢复。联调完成后删除临时流；本机正式新流只做幂等创建，保留原有业务流、消息和 offset。
