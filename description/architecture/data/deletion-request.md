# 删除申请消息契约

`app/service/deletion.py` 组装业务字段并直接发布 UTF-8 JSON，不接收附件、不写临时文件，目标流由 `RABBITMQ_DELETION_REQUEST_STREAM` 指定，默认 `deletion-requests`。HTTP 调用方式见[删除申请 API](../../api/deletion.md)。

## 字段

| 字段 | 类型 | 来源与含义 |
| --- | --- | --- |
| `message_id` | string | 中间件生成的 UUID 十六进制字符串，标识本次删除申请。 |
| `platform_code` | string | 从 token 对应会话注入的申请平台代号。 |
| `source_id` | string | 原数据在子平台内的唯一 ID，与 platform_code 组成发布去重键。 |
| `passport` | string[] | 原数据此前审核通过后获得的通行证集合，至少一个非空白字符串。 |
| `name` | string | 待删除文件的业务名称，不作为磁盘路径。 |
| `abstract` | string | 待删除文件摘要。 |
| `uploader` | string | 原文件上传人姓名或代号。 |
| `applicant` | string | 删除申请人姓名或代号，与原上传人分别记录。 |
| `note` | string | 删除申请人的备注或原因，允许为空字符串；表单省略或传空时仍保存 `""`。 |
| `created_at` | string | 中间件生成的带 UTC 时区的 ISO 8601 时间。 |

```json
{
  "message_id": "25cab2a4f66e45378f52c1547c6b5062",
  "platform_code": "contract-pheno",
  "source_id": "contract-001",
  "passport": ["原文件批准标识-001", "原文件批准标识-002"],
  "name": "设备采购合同.pdf",
  "abstract": "合同摘要",
  "uploader": "张三",
  "applicant": "李四",
  "note": "申请删除重复合同",
  "created_at": "2026-10-10T08:00:00+00:00"
}
```

---

## 关联与历史兼容

新消息不包含 `file`、`file_id` 或 `file_extension`，也不创建 `data/deletion-request/file` 下的临时文件。source_id 表示子平台原数据标识，passport 表示此前批准标识集合；message_id 只标识删除申请。

发布成功仅表示申请消息已获 RabbitMQ 写入确认，不表示批准删除。后续审批结果的 request_id 应引用本次 message_id；registry 发布结果，由目标子平台拉取、完成业务处理后确认，见[删除审批结果消息契约](deletion-result.md)。消息不包含密钥或 token。

发布不确定性及确认边界见[提交删除申请](../../api/deletion.md#提交删除申请)。

相同 platform_code 与 source_id 已有申请且其 offset 大于 registry 在删除申请流的确认进度时，拒绝新发布；registry ack 到原消息后允许重提。去重不使用 passport，也不跨流共享索引或消费进度，详见[流发布去重服务](../../capability/application/stream-publish-deduplication.md)。

升级前已入流的单字符串 passport 在 pull 时规范为数组，缺失该字段的历史消息不补值，详见[通行证集合契约](passport.md)。历史 file 引用保持原样交付；registry 仍可下载历史审核附件，ack 时先验证旧引用、提交并回查 offset，再清理旧文件。新消息无需附件校验或文件清理。旧流不迁移或重建，已有消费进度及去重记录保留。
