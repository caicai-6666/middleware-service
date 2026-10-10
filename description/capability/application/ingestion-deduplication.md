# 入库发布去重服务

用于阻止同一平台在原入库申请尚未确认时再次发布相同 source_id。完整算法、并发、恢复和失败边界统一见[流发布去重服务](stream-publish-deduplication.md)，对外响应见[提交入库请求](../../api/ingestion.md#提交入库请求)。

---

## 入库申请配置

`app/service/ingestion_deduplication.py` 的 `IngestionDeduplicator` 是共用 `StreamPublishDeduplicator` 的入库配置适配：

| 项目 | 配置 |
| --- | --- |
| 目标流 | StreamSettings.request_stream，默认 ingestion-requests。 |
| 去重键 | platform_code 与 source_id；平台代码来自认证会话。 |
| 消费进度 | `<registry_platform_code>:<request_stream>`，由入库申请 ack 持久化。 |
| 接入位置 | app/service/ingestion.py 的 submit，在保存新附件前检查。 |
| 应用装配 | app/main.py，进程共享实例。 |

同键旧消息 offset 大于 registry 已确认进度时返回 409；仅 pull 不解除限制，ack 到原消息后允许重新提交。该进度仅作用于入库申请，不用于删除申请或子平台审核反馈。

保留 `DuplicateIngestionError` 和 `IngestionDeduplicationUnavailableError` 原导入名称，内部复用共用异常类型。历史消息缺少 source_id 时无法参与此键判断，已有消息不改写；消息字段见[入库申请消息契约](../../architecture/data/ingestion-request.md)。
