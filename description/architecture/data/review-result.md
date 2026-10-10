# 审核反馈消息契约

本文定义 `ingestion-results` 流的消息结构。HTTP 调用方式见[审核反馈接口](../../api/ingestion-review.md)。

## 消息结构

消息为 UTF-8 编码的 JSON 对象，不包含文件二进制或身份凭证。

| 字段 | 类型 | 来源与含义 |
| --- | --- | --- |
| `message_id` | string | 中间件生成的 UUID 十六进制字符串，标识本次反馈。 |
| `request_id` | string | 注册中心提交的原入库请求 message_id。 |
| `target_platform_code` | string | 接收反馈的子平台代号，必须存在于配置中且不能为注册中心。 |
| `note` | string | 注册中心审核意见，允许空字符串。 |
| `reviewer` | string | 注册中心审核员姓名或代号。 |
| `passport` | string | 必填最终审核结果字符串，允许为空；中间件原样转发，不解释其业务含义。 |
| `created_at` | string | 中间件生成的带 UTC 时区的 ISO 8601 时间。 |

```json
{
  "message_id": "a04f31c6f02544669bd6700d32c36a33",
  "request_id": "原入库请求标识",
  "target_platform_code": "contract-pheno",
  "note": "审核通过",
  "reviewer": "张三",
  "passport": "最终审核结果",
  "created_at": "2026-10-09T08:00:00+00:00"
}
```

---

## 关联与消费边界

`message_id` 与 `request_id` 各自标识反馈和原请求。当前没有持久化原请求索引，因此不验证请求存在性或目标归属；注册中心负责正确关联。

发布去重使用 `(target_platform_code, request_id)`，仅比较接收子平台在本流的已确认 offset；其尚未确认旧反馈时拒绝重发，确认后允许生成新反馈。其他子平台和 registry 的进度不能解除限制。具体机制见[流发布去重服务](../../capability/application/stream-publish-deduplication.md)。消息结构保持不变，不新增去重字段。

接收平台不要求在线。消费接口根据认证身份过滤 `target_platform_code`，并按平台独立维护消费进度；详见[审核反馈消费服务](../../capability/application/review-consumer.md)。

消息发送不改变原入库请求的确认状态，不执行文件清理。发布失败、确认不确定及重复提交的处理边界见[提交审核反馈](../../api/ingestion-review.md#提交审核反馈)。

新增发布请求必须提供 `passport`，缺失或非字符串返回 422。历史流消息可能不含该字段，消费时保持原始消息结构，不补默认值。

发布契约已由 `passport` 替代 `result`，新请求不再接受 `result`（传入返回 422），新消息也不再写入该字段。已存入流的历史消息保持原样，不自动迁移。
