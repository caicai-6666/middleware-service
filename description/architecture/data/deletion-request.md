# 删除申请消息契约

`app/service/deletion.py` 组装业务字段，`app/service/file_message.py` 保存附件并发布 UTF-8 JSON，目标流由 `RABBITMQ_DELETION_REQUEST_STREAM` 指定，默认 `deletion-requests`。HTTP 调用方式见[删除申请 API](../../api/deletion.md)。

## 字段

| 字段 | 类型 | 来源与含义 |
| --- | --- | --- |
| `message_id` | string | 中间件生成的 UUID 十六进制字符串，标识本次删除申请。 |
| `platform_code` | string | 从 token 对应会话注入的申请平台代号。 |
| `source_id` | string | 原数据在子平台内的唯一 ID，与 platform_code 组成发布去重键。 |
| `passport` | string | 原文件此前审核通过后获得的非空批准标识。 |
| `name` | string | 待删除文件的业务名称，不作为磁盘路径。 |
| `abstract` | string | 待删除文件摘要。 |
| `uploader` | string | 原文件上传人姓名或代号。 |
| `applicant` | string | 删除申请人姓名或代号，与原上传人分别记录。 |
| `note` | string | 删除申请人的备注或原因，允许为空字符串；表单省略或传空时仍保存 `""`。 |
| `file.file_id` | string | 与本次 message_id 相同的附件标识。 |
| `file.file_extension` | string | 客户端声明的后缀，标准化为小写，不带点。 |
| `created_at` | string | 中间件生成的带 UTC 时区的 ISO 8601 时间。 |

```json
{
  "message_id": "25cab2a4f66e45378f52c1547c6b5062",
  "platform_code": "contract-pheno",
  "source_id": "contract-001",
  "passport": "原文件批准标识",
  "name": "设备采购合同.pdf",
  "abstract": "合同摘要",
  "uploader": "张三",
  "applicant": "李四",
  "note": "申请删除重复合同",
  "file": {"file_id": "25cab2a4f66e45378f52c1547c6b5062", "file_extension": "pdf"},
  "created_at": "2026-10-10T08:00:00+00:00"
}
```

---

## 文件及关联边界

二进制不进入消息流，附件保存为 `data/deletion-request/file/<file_id>.<file_extension>`。file_id、source_id 和 passport 各自表示审核附件、子平台原数据和此前批准标识；其中 file_id 由服务生成，不接受用户指定。

发布成功仅表示审核附件落盘和申请入流，不表示批准删除。后续审批结果的 request_id 应引用本次 message_id；registry 发布结果，由目标子平台拉取、完成业务处理后确认，见[删除审批结果消息契约](deletion-result.md)。消息不包含密钥或 token。

失败清理、发布不确定性及确认清理边界见[提交删除申请](../../api/deletion.md#提交删除申请)。

相同 platform_code 与 source_id 已有申请且其 offset 大于 registry 在删除申请流的确认进度时，拒绝新发布；registry ack 到原消息后允许重提。去重不使用 passport，也不跨流共享索引或消费进度，详见[流发布去重服务](../../capability/application/stream-publish-deduplication.md)。
