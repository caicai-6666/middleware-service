# 入库请求 API

子平台上传文件及审核沟通信息，中间件先保存文件，再向入库请求流发布消息。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [提交入库请求](#提交入库请求) | `POST` | `/middleware-service/api/ingestion-requests/publish` |
| [拉取一条入库请求](#拉取一条入库请求) | `POST` | `/middleware-service/api/ingestion-requests/pull` |
| [确认处理完成](#确认处理完成) | `POST` | `/middleware-service/api/ingestion-requests/ack` |

## 提交入库请求

`POST /middleware-service/api/ingestion-requests/publish`，转存单个文件并发布入库请求。

### 认证方式

`Authorization: Bearer <access_token>`。通过 `Depends(require_platform)` 验证令牌并从会话缓存取得平台代号，接口不再接收 platform_code；即使额外提交该字段也不参与身份判定。要求存在有效平台会话，且该平台持有入库请求流的生产权限；注册中心不能调用此接口提交请求。

### 请求参数

媒体类型为 `multipart/form-data`，无 path、query 参数。

| 位置 | 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| header | `Authorization` | string | 是 | 登录返回的 Bearer 令牌。 |
| form | `name` | string | 是 | 入库资料名称，1–200 字符，不得仅为空白。 |
| form | `abstract` | string | 是 | 入库资料摘要，1–10000 字符，不得仅为空白。 |
| form | `source_id` | string | 是 | 数据在子平台内的唯一 ID，1–128 字符，不得仅为空白；与认证所得平台代码组成去重键。 |
| form | `note` | string | 否 | 审核沟通说明，允许空字符串；省略时默认为 `""`，最多 10000 字符。 |
| form | `reviewer` | string | 是 | 发起请求的平台审核员姓名或代号，1–200 字符，不得仅为空白。 |
| form | `file_extension` | string | 是 | 不带点的后缀，例如 `pdf`、`docx`；1–16 位英文字母或数字，统一保存为小写。 |
| form | `file` | binary | 是 | 单个上传文件。 |

显式提交 `note=` 或省略 `note` 均表示没有备注，写入消息时仍保留 `"note": ""`。FastAPI 会将空表单字符串按缺省值处理，因此接口为该字段设置空字符串默认值；其他字段仍必填且没有默认值。

后缀不从原始文件名推断，拒绝点、斜杠、空白等字符；当前不限制具体后缀种类，文件内容按字节原样保存；请求中的媒体类型不写入消息，也不视为已验证的文件格式。

### 成功响应

状态码 `201`，媒体类型 `application/json`：

```json
{
  "message_id": "服务生成的唯一标识",
  "status": "published",
  "message": "入库请求提交成功"
}
```

成功表示文件已落盘并获得 RabbitMQ 发布确认，不代表注册中心已消费、审核或入库。`message_id` 同时作为文件标识。

### 错误响应

| 状态码 | detail | 条件 |
| --- | --- | --- |
| `401` | 令牌缺失、无效、已过期或与平台不匹配 | 身份验证失败，包括文件保存后会话已过期。 |
| `403` | 该平台无权提交入库请求 | 平台不具备此流生产权限。 |
| `409` | 下列重复申请对象 | 同平台、同 source_id 已有消息，其 offset 尚未被 registry 确认。 |
| `422` | 校验错误列表或字段错误说明 | 缺少字段、字段格式或长度不合法。 |
| `500` | 文件保存失败 | 文件目录、磁盘写入或刷盘失败。 |
| `503` | 消息服务暂不可用，请稍后重试 | 平台消息资源不可用。 |
| `503` | 无法确认入库请求是否重复，请稍后重试或核对此前投递结果 | 去重读取失败、消息格式异常，或此前投递结果不确定且尚未在流中找到。 |
| `503` | 下列对象 | 发布失败或未获得确认，文件保留。 |

重复申请响应（`offset` 尚未读到时为 `null`）：

```json
{
  "detail": {
    "message": "该平台的 source_id 已有尚未确认的入库请求",
    "message_id": "此前申请的消息标识",
    "offset": 10
  }
}
```

发布结果不确定时的响应：

```json
{
  "detail": {
    "message": "消息发布未获确认，文件已保留，需核对投递结果",
    "message_id": "服务生成的唯一标识"
  }
}
```

### 请求示例

替换令牌及文件路径：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/ingestion-requests/publish' \
  -H 'Authorization: Bearer replace-with-access-token' \
  -F 'name=采购合同' \
  -F 'abstract=本合同约定设备采购及交付事项' \
  -F 'note=请核对合同条款' \
  -F 'source_id=contract-001' \
  -F 'reviewer=张三' \
  -F 'file_extension=pdf' \
  -F 'file=@/path/to/contract.pdf;type=application/pdf'
```

### 行为与边界

文件保存在项目根目录 `data/ingestion-request/file/<message_id>.<file_extension>`，不使用上传文件名作为磁盘路径。原始文件名可用 `blob` 等占位名称；消息的 `file.file_extension` 保存标准化后的后缀，供注册中心选择读取方式。此字段仅表示调用方声明的类型，不验证实际文件内容。文件保存后再验证会话，并使用该平台长期持有的 Producer 发布至配置指定的入库请求流。消息结构见[入库请求消息契约](../architecture/data/ingestion-request.md)。

上传和发布不延长登录会话，平台仍需定期心跳。发布确认期间持有本平台会话锁，避免清理任务关闭正在使用的客户端；其他平台不受该锁影响。

按照 `(platform_code, source_id)` 精确比较，区分大小写、不裁剪前后空白。若已有申请的消息 offset 大于 registry 已持久化的确认 offset，则在保存新临时文件前拒绝写入；没有确认记录时，流中所有可识别的历史申请均视为未确认。pull 不解除限制，ack 确认 offset 等于或超过原消息位置后，允许再次提交并生成新的 message_id；不同平台使用相同 source_id 不冲突。是否批准不直接影响去重，确认进度才是判断依据。

文件与 RabbitMQ 之间没有跨系统事务。发布确认丢失时保留文件及投递不确定的占位：后续找到消息则按其真实 offset 判断；仍找不到时返回 503，不盲目重新写入。当前没有自动补偿或发布结果查询接口。去重服务可在重启后从流恢复未确认消息，但不承诺跨进程并发发布或故障期间端到端恰好一次；完整规则和依赖见[入库发布去重服务](../capability/application/ingestion-deduplication.md)。文件在注册中心 ack 确认后删除；文件读取方式见[审核附件读取 API](resource.md)，审核反馈见[入库审核 API](ingestion-review.md)。当前未设置应用级上传文件大小上限。

---

## 拉取一条入库请求

`POST /middleware-service/api/ingestion-requests/pull`，注册中心每次取得一条待处理消息。

### 认证方式

必须携带 `Authorization: Bearer <access_token>`，通过统一 `Depends` 从会话缓存取得平台身份。仅持有入库请求流消费权限的注册中心可调用，默认平台代号为 `registry`，可配置。

### 请求参数

无 path、query 参数，无请求体。不提交 platform_code 或 offset。

### 成功响应

有消息返回 `200 application/json`，响应头 `Cache-Control: no-store`：

```json
{
  "offset": 10,
  "message": {
    "message_id": "请求唯一标识",
    "platform_code": "contract-pheno",
    "name": "采购合同",
    "abstract": "设备采购合同摘要",
    "source_id": "contract-001",
    "note": "请审核",
    "reviewer": "张三",
    "file": {"file_id": "请求唯一标识", "file_extension": "pdf"},
    "created_at": "2026-10-08T10:00:00+00:00"
  }
}
```

`offset` 仅供观察，确认时不能用它指定消费进度；`message` 为生产端写入的 JSON 对象，完整字段见[入库请求消息契约](../architecture/data/ingestion-request.md)。历史消息按原结构返回。

缓存为空时预取最多约 2 秒，仍无消息时返回 `204 No Content`，无响应体。此等待时间不含连接与元数据操作耗时。

### 错误响应

| 状态码 | detail | 触发条件 |
| --- | --- | --- |
| `401` | 令牌缺失、无效或已过期 | 认证失败或操作开始时会话已经失效。 |
| `403` | 该平台无权消费入库请求 | 子平台等无此流消费权限。 |
| `502` | 流消息格式异常，未推进消费进度 | 无法解析 JSON 对象、缺少有效 message_id，或队首单条消息超过缓存字节上限；不自动跳过。 |
| `503` | 消费服务暂不可用，请重试 | RabbitMQ 查询、订阅、取消订阅失败或超时。 |

响应媒体类型为 `application/json`，形如 `{"detail":"该平台无权消费入库请求"}`。

### 请求示例

替换为注册中心登录后获取的令牌：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/ingestion-requests/pull' \
  -H 'Authorization: Bearer replace-with-registry-access-token'
```

### 行为与边界

内部按有上限的批次预取，外部仍每次交付一条。重复或并发拉取返回当前待确认消息；ack 成功后移除缓存队首，缓存耗尽才预取下一批。拉取不提交 offset。处理完成后调用[确认处理完成](#确认处理完成)。拉取不续期，注册中心仍需发送心跳。

文件字段为引用，不包含文件字节；registry 可通过[审核附件读取接口](resource.md#读取入库审核附件)下载。平台失活会清空缓存，中间件重启后缓存也会丢失；未确认消息可重新投递，业务处理必须按 message_id 幂等。单进程消费管理及失败边界见[入库请求消费服务](../capability/application/ingestion-consumer.md)。

---

## 确认处理完成

`POST /middleware-service/api/ingestion-requests/ack`，注册中心处理成功后提交确认，推进 RabbitMQ 消费进度。

### 认证方式

必须携带 `Authorization: Bearer <access_token>`，由统一认证依赖识别平台；仅注册中心具有此流消费权限。

### 请求参数

媒体类型为 `application/json`，无 path、query 参数。

| body 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `message_id` | string | 是 | 当前已拉取并处理成功的消息标识，1–128 字符，无默认值。 |

禁止额外字段，不允许提交 offset 或 platform_code。

### 成功响应

返回 `200 application/json`：

```json
{
  "message_id": "请求唯一标识",
  "status": "acknowledged",
  "offset": 10
}
```

表示对应消费位置已在 RabbitMQ 保存并回查确认。随后删除对应本地文件（已不存在视为成功），不删除 Stream 中的消息。最近一次成功确认可重复调用，返回同一结果，不影响下一条待确认消息。

### 错误响应

| 状态码 | detail | 触发条件 |
| --- | --- | --- |
| `401` | 令牌缺失、无效或已过期 | 无有效会话。 |
| `403` | 该平台无权消费入库请求 | 无消费权限。 |
| `409` | 消息不是当前待确认消息，请先拉取 | 尚未拉取、确认错误消息，或内存确认记录已丢失。 |
| `422` | FastAPI 标准校验错误列表 | 请求字段缺失、长度非法或包含额外字段。 |
| `502` | 流消息格式异常，未推进消费进度 | 文件引用非法，确认前拒绝处理。 |
| `503` | 消费服务暂不可用，请重试 | 消费进度查询、存储或回查失败。 |
| `503` | 消费进度已确认，但文件清理失败，请重试同一 ack | offset 已保存，删除文件失败；不要重新执行业务处理。 |

除校验错误外，响应为 `{"detail":"错误说明"}`。

### 请求示例

替换注册中心令牌和拉取到的 message_id：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/ingestion-requests/ack' \
  -H 'Authorization: Bearer replace-with-registry-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"message_id":"replace-with-pulled-message-id"}'
```

### 行为与边界

服务根据待确认消息查找 offset，不接受任意进度。确认失败保留待确认状态；RabbitMQ 可能实际已经存储进度，重试会回查后完成确认。

确认重试的内存记录仅保留每个平台最近一次成功确认，中间件重启后该记录不保留，此时旧确认可能返回 409；重新拉取会依据 RabbitMQ 中的已保存进度继续，若确认未保存则重投旧消息。注册中心应对已处理的重复消息直接确认，避免重复业务副作用。


ack 的文件清理在 offset 保存并回查之后执行，成功清理后才移除缓存队首。删除失败保留待确认消息，同一进程、会话中可重试 ack。若在进度保存后、文件删除前崩溃或失活清空待确认记录，可能遗留文件；目前没有持久化清理任务或后台补偿，需人工核对清理。历史消息没有后缀时按无后缀路径清理；没有文件引用时跳过文件删除。

## 来源数据标识

发布请求新增必填 `source_id`，缺失、纯空白或超过 128 字符返回 422。中间件原样写入消息顶层，注册中心 pull 时随消息返回。该字段与认证注入的 `platform_code` 一起定位子平台数据；唯一性由子平台保证，中间件目前不建立唯一索引、不据此去重，重复提交仍生成不同 `message_id`。

`source_id` 不替代中间件的消息标识或文件标识；入库 ack 继续使用 `message_id`，审核反馈的 `request_id` 仍指向入库消息的 `message_id`。历史消息不自动补充 `source_id`，消费者应兼容缺失字段。已有发布客户端需增加此表单字段后调用。
