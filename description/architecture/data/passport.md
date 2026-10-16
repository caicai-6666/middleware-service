# 通行证集合契约

`passport` 字段统一表示通行证字符串数组，支持同一条入库审核结果或删除申请携带多个通行证。实现位于 `app/service/passport.py`，由发布校验与消费解码共同使用；不新增依赖或环境变量。

## 数据规则

| 场景 | 契约 |
| --- | --- |
| 入库审核结果 publish | passport 必填，推荐传字符串数组；允许 `[]` 表示本次未签发。 |
| 删除申请 publish | passport 必填，至少一个有效字符串；表单多次提交同名 passport 字段。 |
| 新 MQ 消息 | 两种消息均以数组保存 passport，包括单通行证场景。 |
| 历史消息 pull | 单字符串转换为单元素数组，历史空字符串转换为 `[]`；本来没有字段的历史消息不补字段。 |

每项必须为非空白字符串，不允许 null、数字、对象或嵌套数组。保留原始值、顺序及重复项，不裁剪前后空白、不解析通行证内容、不校验业务签发真实性。类型或内容校验失败返回 422；无法规范化的历史消息在拉取时返回 502，不跳过或推进该消息进度。

新发布也兼容旧单字符串：入库结果 `"passport":"pass-a"` 转换为 `"passport":["pass-a"]`，旧空字符串转换为 `[]`。删除申请不能使用空字符串或空集合。消费规范化只改变返回对象，不重写 broker 中的消息，也不改变 message_id、目标过滤、去重键或 ack 的消息粒度。

---

## 提交方式

入库审核结果采用 JSON：

```json
{"passport":["pass-a","pass-b"]}
```

删除申请仍采用表单，同一个字段可重复：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/deletion-requests/publish' \
  -H 'Authorization: Bearer <子平台 access_token>' \
  -d 'source_id=contract-001' -d 'passport=pass-a' -d 'passport=pass-b' \
  --data-urlencode 'name=采购合同' --data-urlencode 'abstract=申请删除重复记录' \
  --data-urlencode 'uploader=张三' --data-urlencode 'applicant=李四'
```

只提交一次 passport 表单字段仍有效，自动成为单元素数组。不把逗号拼接或 JSON 文本自动拆成多个通行证，多个值需重复字段。完整请求、认证和响应分别见[入库审核 API](../../api/ingestion-review.md)和[删除申请 API](../../api/deletion.md)。一条消息中的多个通行证随同一 message_id 一起确认，不为每个通行证单独推进 offset。
