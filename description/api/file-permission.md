# 文件权限调整 API

registry 发布文件级别调整，子平台按自身身份拉取，完成本地修改后确认。基础路径为 `/middleware-service/api/file-permission-adjustments`，请求与响应均使用 `application/json`。消息字段及职责边界见[消息契约](../architecture/data/file-permission-adjustment.md)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [发布文件权限调整](#发布文件权限调整) | `POST` | `/middleware-service/api/file-permission-adjustments/publish` |
| [拉取文件权限调整](#拉取文件权限调整) | `POST` | `/middleware-service/api/file-permission-adjustments/pull` |
| [确认文件权限调整](#确认文件权限调整) | `POST` | `/middleware-service/api/file-permission-adjustments/ack` |

---

## 发布文件权限调整

`POST /middleware-service/api/file-permission-adjustments/publish`，向目标子平台发送一个文件的新等级。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，必须属于 `RABBITMQ_REGISTRY_PLATFORM_CODE` 指定的注册中心，默认 registry。调用方身份只从有效平台令牌解析。

### 请求参数

无 path、query 参数，必填 JSON body；禁止额外字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `target_platform_code` | string | 是 | YAML 中存在的子平台代码，不能为注册中心。允许目标离线。 |
| `source_id` | string | 是 | 文件在目标平台内的唯一 ID，1–128 字符，不全为空白。 |
| `file_level` | integer | 是 | 调整后的等级，不限制范围；拒绝布尔、字符串、小数及 null。等级越大权限越高。 |
| `reviewer` | string | 是 | 注册中心操作人姓名或代号，1–200 字符，不全为空白。 |
| `note` | string | 是 | 调整备注，最多 10000 字符，允许空字符串。 |

不上传文件，不传旧等级、消息 ID 或生成时间。

### 成功响应

状态码 `201`，媒体类型 `application/json`：

```json
{"message_id":"adjustment-001","status":"published","message":"文件权限调整提交成功"}
```

message_id 为中间件生成的 UUID 十六进制字符串。成功只表示 RabbitMQ 已确认写入，不表示子平台已修改文件。

### 错误响应

| 状态码 | 响应及触发条件 |
| --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}`，无有效平台会话，携带 `WWW-Authenticate: Bearer`。 |
| `403` | `{"detail":"仅注册中心可以提交文件权限调整"}`，调用方不是注册中心或没有该流的发布权限。 |
| `409` | `{"detail":{"message":"该子平台的 source_id 已有尚未确认的文件权限调整","message_id":"adjustment-001","offset":12}}`，目标平台尚未确认同一文件的上一条调整；位置未获知时 offset 为 null。 |
| `422` | FastAPI 校验错误列表；目标非法时为 `{"detail":"target_platform_code 必须为配置中的子平台代号"}`。 |
| `503` | 去重、会话资源或消息服务不可用。发布确认不确定时返回 `{"detail":{"message":"消息发布未获确认，需核对投递结果","message_id":"adjustment-001"}}`；不能据此认定未写入。 |

### 请求示例

替换令牌和目标平台为实际配置值：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/file-permission-adjustments/publish' \
  -H 'Authorization: Bearer replace-with-registry-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"target_platform_code":"platform-a","source_id":"contract-001","file_level":3,"reviewer":"审核员甲","note":"调整文件等级"}'
```

### 行为与边界

按 `(target_platform_code, source_id)` 去重，依据该目标平台在当前流的确认 offset 判断。上一条调整未确认时，即使新等级不同也返回 409；确认后可再次发布。同一 source_id 在不同平台互不影响，其他流或其他平台的 offset 不释放本流的去重状态。

发布不修改用户权限、平台管理员或其他审核消息，不操作临时文件。中间件不能确认子平台中的文件是否存在，也不执行实际权限变更。

---

## 拉取文件权限调整

`POST /middleware-service/api/file-permission-adjustments/pull`，每次返回当前平台一条待处理调整。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，允许有效的子平台令牌，注册中心不可消费此流。目标平台从令牌解析，不接受指定其他平台身份。

### 请求参数

无 path、query 参数，无需请求体。

### 成功响应

状态码 `200`，媒体类型 `application/json`，携带 `Cache-Control: no-store`：

```json
{
  "offset": 12,
  "message": {
    "message_id": "adjustment-001",
    "target_platform_code": "platform-a",
    "source_id": "contract-001",
    "file_level": 3,
    "reviewer": "审核员甲",
    "note": "调整文件等级",
    "created_at": "2026-10-21T03:00:00+00:00"
  }
}
```

本次有界扫描没有本平台消息时返回 `204`、无响应体，携带 `Cache-Control: no-store`。完整消息字段见[消息契约](../architecture/data/file-permission-adjustment.md)。

### 错误响应

| 状态码 | 响应及触发条件 |
| --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}`，携带 `WWW-Authenticate: Bearer`。 |
| `403` | `{"detail":"仅子平台可以消费文件权限调整"}`，注册中心或无消费权限。 |
| `502` | `{"detail":"流消息格式异常或超过缓存上限，未跳过异常消息"}`，异常消息不跳过。 |
| `503` | `{"detail":"消费服务暂不可用，请重试"}` 或资源暂不可用提示，读取或无关前缀确认失败。 |

### 请求示例

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/file-permission-adjustments/pull' \
  -H 'Authorization: Bearer replace-with-child-access-token'
```

### 行为与边界

中间件过滤其他目标平台的消息，连续无关前缀会推进当前平台进度，但不会越过本平台尚未确认的消息。重复 pull 会返回同一待确认消息，直到 ack 成功。204 只表示本次扫描未找到，子平台需继续轮询。缓存有界、失活释放及恢复规则见[消费服务](../capability/application/review-consumer.md)。

---

## 确认文件权限调整

`POST /middleware-service/api/file-permission-adjustments/ack`，子平台完成本地文件级别更新后，确认当前待处理消息。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，允许有效子平台令牌，注册中心不可调用。

### 请求参数

无 path、query 参数，必填 JSON body，禁止额外字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `message_id` | string | 是 | 当前 pull 得到的待确认消息标识，1–128 字符。不能通过 offset 指定确认位置。 |

### 成功响应

状态码 `200`，媒体类型 `application/json`：

```json
{"message_id":"adjustment-001","status":"acknowledged","offset":12}
```

### 错误响应

| 状态码 | 响应及触发条件 |
| --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}`，携带 `WWW-Authenticate: Bearer`。 |
| `403` | `{"detail":"仅子平台可以消费文件权限调整"}`，注册中心或无消费权限。 |
| `409` | `{"detail":"消息不是当前待确认文件权限调整，请先拉取"}`，未 pull、消息不匹配或确认其他平台的消息。 |
| `422` | FastAPI 校验错误列表，message_id 缺失、类型非法、长度越界或携带额外字段。 |
| `503` | `{"detail":"消费服务暂不可用，请重试"}` 或资源暂不可用提示，消费进度未得到确认。 |

### 请求示例

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/file-permission-adjustments/ack' \
  -H 'Authorization: Bearer replace-with-child-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"message_id":"replace-with-pulled-message-id"}'
```

### 行为与边界

进度写入 RabbitMQ 并回查成功后才清除待确认缓存；失败保留缓存供重试。重复确认最近一条已确认消息可幂等成功，该记录只在当前会话内存中保留。消费名称为 `<platform_code>:<stream>`，各流、各平台独立；ack 不删除共享消息，也不影响其他平台的进度。

子平台应先持久化业务修改，再 ack，并按 message_id 幂等处理重复交付。中间件不验证子平台实际修改结果；重启恢复后未确认消息可能再次收到。
