# 审核结果消费服务

## 用途与职责

`app/service/review_consumer.py` 的 `ReviewConsumerService` 负责子平台结果过滤、有界缓存、逐条交付和消费确认。入库与删除结果共用该算法，使用独立服务实例、缓存及各自流中的消费进度。接口约定见[入库审核 API](../../api/ingestion-review.md)和[删除审批 API](../../api/deletion-review.md)，消息字段分别见[入库结果契约](../../architecture/data/review-result.md)和[删除结果契约](../../architecture/data/deletion-result.md)。

---

## 过滤与进度

每个平台通过会话资源获取独立 Consumer，稳定消费名称为 `<platform_code>:<result_stream>`，由 RabbitMQ 保存对应 offset。中间件依据已认证平台身份匹配消息的 `target_platform_code`，不将其他平台反馈交给调用方。消息含 passport 时共用 app/service/passport.py 规范为字符串数组，历史单字符串及空字符串兼容，缺失字段不补值；不改写 broker 原消息。

例如依次为 `B@10、A@12、B@14、A@16`：A 第一次 pull 自动提交无关前缀到 10，返回 12；未 ack 12 前重复 pull 仍返回 12。ack 12 后下一次 pull 才能跳过 14 并返回 16。B 的进度独立，不受 A 的操作影响。

任何跳过或 ack 都必须回查进度成功后才删除缓存。失败保留原缓存供重试；进度已写入但确认丢失时，重试可直接回查完成。不能解析或无法确定归属的消息不会被自动跳过。

---

## 缓存与生命周期

底层 `app/service/stream_consumer.py` 的 `StreamBatchReader` 与入库消费服务共享有界预取实现：从已提交 offset 的下一位置订阅，无历史进度时从 FIRST 开始；达到数量/字节限制或等待 2 秒后停止订阅。计数包含所有平台消息，字节数为原始负载，不包含 Python 对象开销。

每次 pull 最多扫描一个缓存批次；若全为其他平台消息，提交跳过进度并返回 204，下一次轮询继续。此设计限制单次扫描工作量；204 不意味着已扫描完整个流。

每个平台有独立缓存、待确认消息及最近确认记录。平台锁覆盖 pull/ack，防止并发越过待确认消息。失活或停机时通过会话释放回调清空内存；重登录后从 RabbitMQ 已提交进度恢复，未确认消息可能再次交付。

---

## 配置与依赖

在 `app/main.py` 启动时分别装配到 `app.state.review_consumer` 和 `app.state.deletion_review_consumer`，复用平台会话管理器、角色资源映射和启动时的 Stream 配置。同一平台的两个结果流可以复用 Consumer 客户端，但订阅、缓存、待确认记录及 `<platform_code>:<stream>` 消费名称各自独立；失活回调清理两个实例的内存状态。

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `REVIEW_CACHE_MAX_MESSAGES` | `100` | 每个平台在入库结果流一批缓存的消息数量上限。 |
| `REVIEW_CACHE_MAX_BYTES` | `4194304` | 每个平台在入库结果流一批缓存的原始消息字节上限。 |
| `DELETION_REVIEW_CACHE_MAX_MESSAGES` | `100` | 每个平台在删除结果流一批缓存的消息数量上限。 |
| `DELETION_REVIEW_CACHE_MAX_BYTES` | `4194304` | 每个平台在删除结果流一批缓存的原始消息字节上限。 |

配置由 `app/config/consumer.py` 读取，环境变量覆盖项目根 `.env`，只接受正整数。`.env.example` 提供示例；容器部署可通过环境变量注入。与 `INGESTION_CACHE_*` 独立配置。

单条消息超过字节上限会返回 502 并停留在原位置，需要调整配置或排查消息。pull 总超时为 15 秒，ack 为 10 秒；失败返回 503。

---

## 边界与验证

当前仅支持单进程会话/缓存；不支持多个服务实例使用同一平台消费名称并发提交进度。Stream 保留策略可能影响历史消息可读性，消费确认不提供端到端恰好一次保证。

结果 ack 不触碰文件，也不删除共享流消息。删除审批 approved 为 true 时由子平台完成实际删除再确认，为 false 时记录拒绝后确认；中间件不验证其业务动作。最近一次重复 ack 的成功记录只存在内存中，不是持久化业务去重。

测试覆盖双平台交错过滤、待确认屏障、无关批次推进、提交及回查失败、重启恢复、资源回收、坏消息及超限处理、HTTP 权限和错误码。真实 RabbitMQ 联调使用独立临时流，验证发布、独立 offset 与重建服务后的恢复，结束后删除临时流。
