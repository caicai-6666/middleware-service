# 审核反馈接口

向 `ingestion-results` 流发布审核反馈，子平台通过中间件拉取并确认属于自己的消息。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [提交审核反馈](#提交审核反馈) | `POST` | `/middleware-service/api/ingestion-results/publish` |
| [拉取审核反馈](#拉取审核反馈) | `POST` | `/middleware-service/api/ingestion-results/pull` |
| [确认审核反馈](#确认审核反馈) | `POST` | `/middleware-service/api/ingestion-results/ack` |

## 通用约定

HTTP 前缀统一为 `/middleware-service/api/ingestion-results`，原 `/middleware-service/api/review-results` 路径不再注册，调用方需同步更新。底层 Stream 仍由 `RABBITMQ_RESULT_STREAM` 指定，默认名称同步改为 `ingestion-results`。

接口定义位于 `app/router/ingestion_review.py`，通过 `app/router/__init__.py` 注册。
所有接口复用 `require_platform`，从 Bearer token 对应的内存会话取得调用平台身份。
请求体使用 JSON，禁止传入未定义字段。发布服务为 `app/service/review.py`，在 `app/main.py` 启动流程中装配。

发布接口只允许配置 `RABBITMQ_REGISTRY_PLATFORM_CODE` 指定的注册中心调用，默认代号为 `registry`；目标流由 `RABBITMQ_RESULT_STREAM` 指定，默认 `ingestion-results`。复用启动时加载的平台配置快照及当前会话持有的 Producer，无需新增配置。

拉取与确认仅允许子平台调用，按认证身份过滤 `target_platform_code`，每个平台使用独立的缓存和 RabbitMQ 消费进度。实现与配置见[审核反馈消费服务](../capability/application/review-consumer.md)。

---

## 提交审核反馈

`POST /middleware-service/api/ingestion-results/publish`，由注册中心向子平台发送审核反馈。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效的注册中心会话。调用平台身份从 token 缓存取得，不接受请求体指定发送方。

### 请求参数

Body 为 JSON，无 path 或 query 参数。

| 字段 | 类型 | 必填 | 约束与说明 |
| --- | --- | --- | --- |
| `request_id` | string | 是 | 原入库请求的 message_id，1–128 字符，不能仅包含空白；与目标子平台代码组成去重键。 |
| `target_platform_code` | string | 是 | 配置中存在的子平台代号，不能为注册中心；不要求在线。 |
| `note` | string | 是 | 审核意见，最多 10000 字符，允许空字符串。 |
| `reviewer` | string | 是 | 注册中心审核员姓名或代号，1–200 字符，不能仅包含空白。 |
| `passport` | string[]（兼容 string） | 是 | 通行证集合，每项非空白；允许 `[]` 表示未签发。旧单字符串自动转为单元素数组，旧空字符串转为 `[]`。 |

JSON 中的 `"note": ""` 合法，写入消息时原样保留。该字段仍必填，省略、传入 `null` 或超过长度限制时返回 `422`。

通行证数量不影响消息粒度，一条审核结果携带多个通行证，仍按同一 message_id 确认。值不裁剪、不去重，具体校验及历史兼容见[通行证集合契约](../architecture/data/passport.md)。

### 成功响应

`201 Created`，`application/json`。仅在 RabbitMQ 确认消息写入后返回：

```json
{"message_id":"新生成的反馈消息标识","status":"published","message":"审核反馈提交成功"}
```

### 错误响应

- `401`：令牌缺失、无效或过期，包括等待发布期间发现会话已失效。
- `403`：调用方不是注册中心，或没有目标流发布权限。
- `409`：同一目标子平台、同一 request_id 的反馈尚未被该子平台确认。
- `422`：请求结构不合法、必填字段为空白，或目标平台不存在/为注册中心。
- `503`：会话的 Stream 资源不可用、发布未获确认，或无法可靠完成去重判断。

重复提交时返回旧反馈标识及其 offset；尚未扫描到真实 offset 时为 null：

```json
{"detail":{"message":"该子平台的 request_id 已有尚未确认的入库审核反馈","message_id":"此前反馈标识","offset":12}}
```

去重查询失败或此前投递不确定且尚未找到消息时，返回 `503`，detail 为 `无法确认入库审核反馈是否重复，请稍后重试或核对此前投递结果`。

发布未获确认时返回本次消息标识：

```json
{"detail":{"message":"消息发布未获确认，需核对投递结果","message_id":"本次反馈消息标识"}}
```

### 请求示例

替换令牌、原请求标识及目标平台后执行：

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/ingestion-results/publish" \
  -H "Authorization: Bearer <注册中心 access_token>" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"原入库消息标识","target_platform_code":"contract-pheno","note":"审核通过","reviewer":"张三","passport":["通行证-001","通行证-002"]}'
```

### 行为与边界

服务校验注册中心身份、流权限和目标平台，然后生成独立反馈标识及 UTC 时间，按[审核反馈消息契约](../architecture/data/review-result.md)编码为 UTF-8 JSON。复用会话持有的 Producer，发布时在平台锁内再次验证会话与权限，等待 RabbitMQ 发布确认（当前超时为 5 秒）。

目标子平台允许离线，不因为目标离线而拒绝反馈。当前不验证 `request_id` 是否真实存在，也不验证原请求与目标平台的归属关系；注册中心负责从原请求取得正确关联信息。

本接口不确认原入库请求、不删除文件，也不写入消费 offset；原请求处理完毕后仍需调用入库流的 ack。反馈发布与原请求 ack 是两个独立操作，不具备跨操作事务。

发布前按 `(target_platform_code, request_id)` 精确去重，依据接收子平台在本结果流的 `<target_platform_code>:<stream>` 确认 offset。只要旧反馈的 offset 大于该子平台确认进度，便拒绝再次发布；registry 的进度或其他子平台更大的进度均不能解除限制。仅 pull 不解除限制，该目标 ack 到旧反馈后允许重发并生成新标识；相同 request_id 发给不同目标不冲突。修改 note、reviewer 或 passport 不能绕过未确认限制。

发布超时可能是确认丢失，不能视为消息一定未写入；保留不确定占位，后续找到消息则按真实位置判断，始终未找到时返回 503，不自动重发。进程重启可从流恢复可识别的反馈，原有单进程边界保持不变；完整机制见[流发布去重服务](../capability/application/stream-publish-deduplication.md)。当前不提供投递结果查询接口或跨系统事务。

---

## 拉取审核反馈

`POST /middleware-service/api/ingestion-results/pull`，获取一条属于当前子平台的反馈。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效子平台会话。注册中心无消费权限。

### 请求参数

无请求体，无 path 或 query 参数。不接受客户端指定目标平台，身份由 token 对应会话确定。

### 成功响应

`200 OK`，`application/json`，包含消息 offset 及[完整反馈消息](../architecture/data/review-result.md)：

```json
{
  "offset": 12,
  "message": {
    "message_id": "反馈消息标识",
    "request_id": "原入库请求标识",
    "target_platform_code": "contract-pheno",
    "note": "审核通过",
    "reviewer": "张三",
    "passport": ["通行证-001", "通行证-002"],
    "created_at": "2026-10-09T08:00:00+00:00"
  }
}
```

`204 No Content` 表示本次有界扫描未发现属于本平台的反馈，无响应体；应继续轮询，不代表整个流后续一定没有本平台消息。两种成功响应均带 `Cache-Control: no-store`。

### 错误响应

- `401`：身份无效或过期。
- `403`：注册中心或平台无反馈流消费权限。
- `502`：消息不能解析、消息标识或目标字段无效，或单条原始消息超过缓存字节上限；不跳过异常消息。
- `503`：读取、订阅、取消订阅或跳过无关消息时的进度确认失败，可重试。

### 请求示例

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/ingestion-results/pull" -H "Authorization: Bearer <子平台 access_token>"
```

### 行为与边界

中间件内部扫描共享流，只向调用方返回 `target_platform_code` 与认证平台代号完全一致的消息。第一条本平台消息之前的连续无关消息可以自动提交到本平台的消费进度，不影响其他平台。

pull 返回完整消息，passport 统一规范为字符串数组；历史单字符串转为单元素数组，历史空字符串转为 `[]`，历史消息缺失该字段时不补值。不改写原 MQ 消息或消费位置。

存在待确认反馈时，重复或并发 pull 返回同一条，不继续扫描、不越过它推进 offset。缓存耗尽后才读取下一批；本批全为其他平台消息时，保存跳过进度后返回 204。消费并非恰好一次，调用方应按反馈 `message_id` 幂等处理。

---

## 确认审核反馈

`POST /middleware-service/api/ingestion-results/ack`，确认当前子平台已处理完毕的反馈。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效子平台会话。注册中心无消费权限。

### 请求参数

Body 为 JSON，无 path 或 query 参数。

| 字段 | 类型 | 必填 | 约束与说明 |
| --- | --- | --- | --- |
| `message_id` | string | 是 | 当前已 pull 的反馈消息标识，1–128 字符；不是原入库请求标识。 |

不接受客户端提供 offset 或平台代号，未知字段返回 422。

### 成功响应

`200 OK`，`application/json`：

```json
{"message_id":"反馈消息标识","status":"acknowledged","offset":12}
```

### 错误响应

- `401`：身份无效或过期。
- `403`：注册中心或平台无消费权限。
- `409`：不是当前已交付待确认的反馈，包括其他平台的反馈、尚未 pull 的反馈。
- `422`：请求参数不符合结构约束。
- `503`：消费进度未获确认，保留待确认消息，可重试同一 ack。

### 请求示例

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/ingestion-results/ack" -H "Authorization: Bearer <子平台 access_token>" -H 'Content-Type: application/json' -d '{"message_id":"反馈消息标识"}'
```

### 行为与边界

将当前消息 offset 写入该平台独立的 RabbitMQ 消费进度，并回查确认后才移除缓存队首。不会删除共享流消息，不会删除原入库文件，也不会确认原入库请求。

同一活跃会话内，最近一次成功 ack 可以重复，即使下一条已经 pull 也不会确认下一条。失活或服务重启后重复确认记录不保留，旧 ack 可能返回 409；持久化 offset 仍用于后续恢复。

发布契约已由 `passport` 替代 `result`，新请求不再接受 `result`（传入返回 422），新消息也不再写入该字段。已存入流的历史消息不重写，拉取时仅规范化 passport 类型。
