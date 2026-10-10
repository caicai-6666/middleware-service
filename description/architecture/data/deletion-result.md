# 删除审批结果消息契约

本文定义 `deletion-results` 流的消息结构。调用方式、权限及错误响应见[删除审批结果 API](../../api/deletion-review.md)。

## 消息结构

消息为 UTF-8 编码的 JSON 对象，不包含附件、密钥或 access_token。

| 字段 | 类型 | 来源与含义 |
| --- | --- | --- |
| `message_id` | string | 中间件生成的 UUID 十六进制字符串，标识本次审批结果。 |
| `request_id` | string | registry 提供的原删除申请 message_id。 |
| `target_platform_code` | string | 接收结果的子平台代码，必须存在于配置中且不能为 registry。 |
| `approved` | boolean | registry 是否批准删除，true 为批准，false 为拒绝，不表示删除已完成。 |
| `note` | string | registry 的审批意见，允许空字符串并原样保留。 |
| `reviewer` | string | registry 审核员姓名或代号。 |
| `created_at` | string | 中间件生成的带 UTC 时区的 ISO 8601 时间。 |

```json
{
  "message_id": "a04f31c6f02544669bd6700d32c36a33",
  "request_id": "原删除申请消息标识",
  "target_platform_code": "contract-pheno",
  "approved": false,
  "note": "该文件仍需保留",
  "reviewer": "注册中心审核员",
  "created_at": "2026-10-10T08:00:00+00:00"
}
```

---

## 关联与生命周期

`DeletionReviewService` 明确组装 approved、note、reviewer，通过 `ResultMessagePublisher` 发布公共标识及时间字段；与入库审核结果共用发布流程，业务字段和流名称分别指定。服务在 `app/main.py` 的 lifespan 中装配，使用已有平台配置、会话资源及 `RABBITMQ_DELETION_RESULT_STREAM`，不新增配置。

registry 负责正确关联原删除申请及接收平台；当前未建立原申请持久化索引，不验证其存在性或归属。发布不会推进任何消费进度，不删除中间件审核附件或子平台原文件。发布批准消息只表示允许子平台后续删除，不表示已经执行删除。

发布去重键为 `(target_platform_code, request_id)`，独立比较目标子平台在删除结果流的确认进度；规则及恢复边界见[流发布去重服务](../../capability/application/stream-publish-deduplication.md)。目标可以离线，消息写入流后保留；HTTP pull 只交付属于认证子平台的结果，未 ack 时重复返回同一条。ack 持久化并回查确认后才释放待确认状态并允许同键再次发布，消费实现见[审核结果消费服务](../../capability/application/review-consumer.md)。
