# 删除申请接口

子平台提交删除申请及审核附件。发布、注册中心拉取与确认均已实现。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [提交删除申请](#提交删除申请) | `POST` | `/middleware-service/api/deletion-requests/publish` |
| [拉取删除申请](#拉取删除申请) | `POST` | `/middleware-service/api/deletion-requests/pull` |
| [确认删除申请](#确认删除申请) | `POST` | `/middleware-service/api/deletion-requests/ack` |

## 通用约定

路由模块为 `app/router/deletion.py`，由 `app/router/__init__.py` 注册。所有接口通过 `Depends(require_platform)` 验证当前会话，从 token 获取调用平台身份。publish 使用 `multipart/form-data`，ack 使用 JSON 并禁止未知字段，pull 无请求体。

发布接口通过 `app/service/deletion.py` 保存附件并等待 RabbitMQ 写入确认。`RABBITMQ_DELETION_REQUEST_STREAM` 默认 `deletion-requests`，`RABBITMQ_DELETION_RESULT_STREAM` 默认 `deletion-results`，启动时幂等创建。删除申请 pull/ack 通过 `app/service/deletion_consumer.py` 实现，只允许注册中心消费；[删除审批结果](deletion-review.md) publish/pull/ack 均已实现。消费机制见[删除申请消费服务](../capability/application/deletion-consumer.md)。

流程为子平台申请、注册中心审批、子平台正式删除。批准后子平台应在实际删除成功后确认结果，拒绝则记录拒绝后确认；中间件提供结果发布、过滤拉取及确认，实际删除由子平台自行实现。删除申请 ack 仅清理中间件的审核附件。

---

## 提交删除申请

`POST /middleware-service/api/deletion-requests/publish`，提交删除申请和供注册中心审核的文件。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效子平台会话；注册中心不能发布删除申请。

### 请求参数

请求体为 `multipart/form-data`，除 note 外以下 form 字段均为必填，无 path 或 query 参数。

| 字段 | 类型 | 约束与含义 |
| --- | --- | --- |
| `source_id` | string | 1–128 字符，不能仅为空白；文件在子平台内的唯一 ID，与认证所得平台代码组成去重键。 |
| `passport` | string | 非空且非纯空白；待删除文件此前审核通过后获得的 passport。 |
| `name` | string | 1–200 字符，不能仅为空白；文件的业务名称，不参与磁盘路径构建。 |
| `abstract` | string | 1–10000 字符，不能仅为空白；文件摘要。 |
| `uploader` | string | 1–200 字符，不能仅为空白；原文件上传人的姓名或代号。 |
| `applicant` | string | 1–200 字符，不能仅为空白；本次删除申请人的姓名或代号。 |
| `note` | string | 可选，默认 `""`，允许空字符串，最多 10000 字符；删除申请人的备注或删除原因。 |
| `file_extension` | string | 1–16 位英文字母或数字，不带点，显式声明后缀，例如 pdf。 |
| `file` | binary | 上传的单个文件，供注册中心核对删除申请。 |

调用平台身份从 token 缓存取得，不接受客户端以 `platform_code` 指定身份。`uploader` 与 `applicant` 为客户端声明的业务人员信息，当前不验证人员真实性。额外表单字段不参与业务；原 JSON 发布请求及旧 `reviewer` 字段已由上述契约替代。

显式提交 `note=` 或省略 note 均按空字符串处理，并在消息中保留 `"note":""`。FastAPI 会将空表单字符串视为缺省值，因此备注使用空字符串默认值；非空备注原样保存，其他必填业务字段仍不允许为空。

### 成功响应

`201 Created`，`application/json`，文件已保存且 RabbitMQ 已确认消息写入：

```json
{"message_id":"删除申请标识","status":"published","message":"删除申请提交成功"}
```

### 错误响应

- `401`：身份缺失、无效或过期，包括保存期间失活。
- `403`：注册中心或没有删除申请流的发布权限。
- `409`：同一平台、同一 source_id 已有尚未被 registry 确认的删除申请。
- `422`：字段或后缀不符合约束，未写入消息。
- `500`：附件保存失败。
- `503`：消息资源不可用、发布结果未获确认，或无法可靠完成去重判断。

重复申请响应中的 message_id 指向旧申请；尚未读到真实 offset 时为 null：

```json
{"detail":{"message":"该平台的 source_id 已有尚未确认的删除申请","message_id":"此前申请标识","offset":10}}
```

去重查询失败或此前投递不确定且未找到消息时返回 `503`，detail 为 `无法确认删除申请是否重复，请稍后重试或核对此前投递结果`。

发布结果不确定时保留附件，返回本次消息标识供核对：

```json
{"detail":{"message":"消息发布未获确认，文件已保留，需核对投递结果","message_id":"本次申请标识"}}
```

### 请求示例

替换令牌与业务字段后执行：

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/deletion-requests/publish" \
  -H "Authorization: Bearer <有效 access_token>" \
  -F 'source_id=contract-001' \
  -F 'passport=原文件批准标识' \
  -F 'name=设备采购合同.pdf' \
  -F 'abstract=本合同约定设备采购及交付事项' \
  -F 'uploader=张三' \
  -F 'applicant=李四' \
  -F 'note=申请删除重复上传的合同' \
  -F 'file_extension=pdf' \
  -F 'file=@/替换为真实路径/contract.pdf'
```

### 行为与边界

文件保存到 `data/deletion-request/file/<message_id>.<file_extension>`，后缀统一小写；不使用上传文件名或 `source_id` 构建路径。落盘后将[删除申请消息](../architecture/data/deletion-request.md)作为 UTF-8 JSON 写入流，消息仅包含业务字段和文件引用。

保存文件前按 `(platform_code, source_id)` 精确去重。若旧申请的 offset 大于 registry 在本删除申请流的已确认进度，返回 409，不保存新附件也不写入消息。pull 不解除限制，ack 到原消息后允许重提；不同平台之间不冲突。入库申请流的确认进度不用于删除申请去重，两个流可分别提交相同平台、相同 source_id。

文件独占创建并 fsync 后发送，等待 broker 确认（5 秒）。写入失败清理半成品；保存后身份/权限失效删除未发布文件；发布确认超时可能实际已写入，因此保留文件及不确定占位，不自动重发。后续找到消息后按实际 offset 判断，始终未找到时拒绝同键重试并返回 503。进程重启从流恢复未确认申请，完整机制见[流发布去重服务](../capability/application/stream-publish-deduplication.md)。没有事务补偿或投递结果查询接口。

`source_id` 表示子平台内的业务数据标识；当前不验证 passport 或原数据是否存在，不验证人员身份或真实文件格式，也不限制附件大小。删除申请提交成功不表示批准，更不表示子平台文件已经删除。

registry 完成申请处理后调用本流 ack，进度回查成功后清除审核附件。审批结果须通过另一个结果流发布并由目标子平台消费确认，本流 ack 不代替结果发布或子平台业务处理；没有定时清理或异常文件补偿。

---

## 拉取删除申请

`POST /middleware-service/api/deletion-requests/pull`，由注册中心拉取一条删除申请。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为配置 `RABBITMQ_REGISTRY_PLATFORM_CODE` 指定的注册中心有效会话，默认 `registry`。

### 请求参数

无请求体，无 path 或 query 参数。平台身份从 token 取得，不接受客户端指定平台代号。

### 成功响应

`200 OK`，`application/json`，包含 offset 和[完整删除申请消息](../architecture/data/deletion-request.md)：

```json
{
  "offset": 10,
  "message": {
    "message_id": "删除申请标识",
    "platform_code": "contract-pheno",
    "source_id": "contract-001",
    "passport": "原文件批准标识",
    "name": "设备采购合同.pdf",
    "abstract": "合同摘要",
    "uploader": "张三",
    "applicant": "李四",
    "note": "申请删除重复合同",
    "file": {"file_id": "删除申请标识", "file_extension": "pdf"},
    "created_at": "2026-10-10T08:00:00+00:00"
  }
}
```

`204 No Content` 表示本次等待后暂时没有消息，无响应体。两种响应均带 `Cache-Control: no-store`。

### 错误响应

- `401`：身份缺失、无效或过期。
- `403`：非注册中心或无删除申请流消费权限。
- `502`：队首 JSON 或消息标识异常，或单条消息超过缓存字节上限；不跳过、不推进进度。
- `503`：订阅、读取、取消订阅或消费资源异常，可稍后重试。

### 请求示例

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/deletion-requests/pull" -H "Authorization: Bearer <registry access_token>"
```

### 行为与边界

每次只交付一条，未 ack 前重复或并发 pull 返回同一条。原始消息以有界批次缓存，队首 ack 后才允许交付下一条；缓存未耗尽时不会重新订阅。pull 不保存消费 offset，不删除附件。

删除申请与入库申请使用独立缓存和 `<registry_code>:<stream>` 消费名称，不混用消费进度。失活或重启清空内存，未确认申请可能重投，registry 应按 message_id 幂等处理。

返回文件引用，不包含文件二进制；registry 可通过[审核附件读取接口](resource.md#读取删除审核附件)下载。

---

## 确认删除申请

`POST /middleware-service/api/deletion-requests/ack`，确认注册中心已完成当前申请的处理，并清理对应审核附件。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效注册中心会话。

### 请求参数

Body 为 JSON，无 path 或 query 参数，禁止未定义字段。

| 字段 | 类型 | 必填 | 约束与说明 |
| --- | --- | --- | --- |
| `message_id` | string | 是 | 当前已 pull 且处理完成的删除申请标识，1–128 字符；不是 source_id 或 passport。 |

### 成功响应

`200 OK`，`application/json`：

```json
{"message_id":"删除申请标识","status":"acknowledged","offset":10}
```

### 错误响应

- `401`：身份缺失、无效或过期。
- `403`：非注册中心或无消费权限。
- `409`：申请未拉取或不是当前待确认申请，不能确认尚未交付的缓存消息。
- `422`：请求参数不符合结构约束。
- `502`：文件引用非法，在提交进度前拒绝，附件不删除。
- `503`：进度未获确认，或进度已确认但附件删除失败；保留待确认状态，可重试同一 ack。

附件删除失败的响应：

```json
{"detail":"消费进度已确认，但文件清理失败，请重试同一 ack"}
```

### 请求示例

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/deletion-requests/ack" -H "Authorization: Bearer <registry access_token>" -H 'Content-Type: application/json' -d '{"message_id":"删除申请标识"}'
```

### 行为与边界

先验证当前待确认消息和文件引用，再写入 RabbitMQ offset 并回查；确认成功后删除 `data/deletion-request/file/<file_id>.<file_extension>`，最后移除缓存队首。文件不存在视为已清理；删除失败时重复 ack 会再次清理。

同一活跃会话内最近一次成功 ack 可重复；即使已拉取下一条，也不会确认下一条或回退进度。重启/失活会丢失最近确认记录，旧 ack 可能返回 409，持久化 offset 仍用于恢复。

本接口不删除共享流消息，不发布审批结果，不删除子平台原数据。审批结果发布和本流确认需要由 registry 分别发起，当前不校验是否已发布结果。

进度保存与附件删除不是原子事务，崩溃或会话失活可能遗留文件，当前没有后台补偿。删除审批结果的 pull/ack 独立确认子平台处理结果，不重复清理申请附件。
