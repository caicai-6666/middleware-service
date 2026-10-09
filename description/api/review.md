# 审核反馈接口

向 `review-results` 流发布审核反馈，子平台通过中间件拉取并确认属于自己的消息。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [提交审核反馈](#提交审核反馈) | `POST` | `/middleware-service/api/review-results/publish` |
| [拉取审核反馈](#拉取审核反馈) | `POST` | `/middleware-service/api/review-results/pull` |
| [确认审核反馈](#确认审核反馈) | `POST` | `/middleware-service/api/review-results/ack` |

## 通用约定

接口定义位于 `app/router/review.py`，通过 `app/router/__init__.py` 注册。
所有接口复用 `require_platform`，从 Bearer token 对应的内存会话取得调用平台身份。
请求体使用 JSON，禁止传入未定义字段。发布服务为 `app/service/review.py`，在 `app/main.py` 启动流程中装配。

发布接口只允许配置 `RABBITMQ_REGISTRY_PLATFORM_CODE` 指定的注册中心调用，默认代号为 `registry`；目标流由 `RABBITMQ_RESULT_STREAM` 指定，默认 `review-results`。复用启动时加载的平台配置快照及当前会话持有的 Producer，无需新增配置。

拉取与确认仅允许子平台调用，按认证身份过滤 `target_platform_code`，每个平台使用独立的缓存和 RabbitMQ 消费进度。实现与配置见[审核反馈消费服务](../capability/application/review-consumer.md)。

---

## 提交审核反馈

`POST /middleware-service/api/review-results/publish`，由注册中心向子平台发送审核反馈。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效的注册中心会话。调用平台身份从 token 缓存取得，不接受请求体指定发送方。

### 请求参数

Body 为 JSON，无 path 或 query 参数。

| 字段 | 类型 | 必填 | 约束与说明 |
| --- | --- | --- | --- |
| `request_id` | string | 是 | 原入库请求的 message_id，1–128 字符，不能仅包含空白。 |
| `target_platform_code` | string | 是 | 配置中存在的子平台代号，不能为注册中心；不要求在线。 |
| `note` | string | 是 | 审核意见，最多 10000 字符，允许空字符串。 |
| `reviewer` | string | 是 | 注册中心审核员姓名或代号，1–200 字符，不能仅包含空白。 |
| `passport` | string | 是 | 最终审核结果字符串，允许为空；中间件原样写入流，不解析、不去除空白。 |

### 成功响应

`201 Created`，`application/json`。仅在 RabbitMQ 确认消息写入后返回：

```json
{"message_id":"新生成的反馈消息标识","status":"published","message":"审核反馈提交成功"}
```

### 错误响应

- `401`：令牌缺失、无效或过期，包括等待发布期间发现会话已失效。
- `403`：调用方不是注册中心，或没有目标流发布权限。
- `422`：请求结构不合法、必填字段为空白，或目标平台不存在/为注册中心。
- `503`：会话的 Stream 资源不可用，或者发布未获确认。

发布未获确认时返回本次消息标识：

```json
{"detail":{"message":"消息发布未获确认，需核对投递结果","message_id":"本次反馈消息标识"}}
```

### 请求示例

替换令牌、原请求标识及目标平台后执行：

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/review-results/publish" \
  -H "Authorization: Bearer <注册中心 access_token>" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"原入库消息标识","target_platform_code":"contract-pheno","note":"审核通过","reviewer":"张三","passport":"最终审核结果"}'
```

### 行为与边界

服务校验注册中心身份、流权限和目标平台，然后生成独立反馈标识及 UTC 时间，按[审核反馈消息契约](../architecture/data/review-result.md)编码为 UTF-8 JSON。复用会话持有的 Producer，发布时在平台锁内再次验证会话与权限，等待 RabbitMQ 发布确认（当前超时为 5 秒）。

目标子平台允许离线，不因为目标离线而拒绝反馈。当前不验证 `request_id` 是否真实存在，也不验证原请求与目标平台的归属关系；注册中心负责从原请求取得正确关联信息。

本接口不确认原入库请求、不删除文件，也不写入消费 offset；原请求处理完毕后仍需调用入库流的 ack。反馈发布与原请求 ack 是两个独立操作，不具备跨操作事务。

发布超时可能是确认丢失，不能视为消息一定未写入。当前不自动重发、不持久化待重试消息，也不提供发布幂等或投递结果查询接口；重复提交会生成新的反馈标识，可能产生重复反馈，调用方需核对后处理。

---

## 拉取审核反馈

`POST /middleware-service/api/review-results/pull`，获取一条属于当前子平台的反馈。

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
    "passport": "最终审核结果",
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
curl -X POST "http://127.0.0.1:22000/middleware-service/api/review-results/pull" -H "Authorization: Bearer <子平台 access_token>"
```

### 行为与边界

中间件内部扫描共享流，只向调用方返回 `target_platform_code` 与认证平台代号完全一致的消息。第一条本平台消息之前的连续无关消息可以自动提交到本平台的消费进度，不影响其他平台。

pull 原样返回消息字段，包括新消息中的 `passport`；历史消息若没有该字段，不自动补充。

存在待确认反馈时，重复或并发 pull 返回同一条，不继续扫描、不越过它推进 offset。缓存耗尽后才读取下一批；本批全为其他平台消息时，保存跳过进度后返回 204。消费并非恰好一次，调用方应按反馈 `message_id` 幂等处理。

---

## 确认审核反馈

`POST /middleware-service/api/review-results/ack`，确认当前子平台已处理完毕的反馈。

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
curl -X POST "http://127.0.0.1:22000/middleware-service/api/review-results/ack" -H "Authorization: Bearer <子平台 access_token>" -H 'Content-Type: application/json' -d '{"message_id":"反馈消息标识"}'
```

### 行为与边界

将当前消息 offset 写入该平台独立的 RabbitMQ 消费进度，并回查确认后才移除缓存队首。不会删除共享流消息，不会删除原入库文件，也不会确认原入库请求。

同一活跃会话内，最近一次成功 ack 可以重复，即使下一条已经 pull 也不会确认下一条。失活或服务重启后重复确认记录不保留，旧 ack 可能返回 409；持久化 offset 仍用于后续恢复。

发布契约已由 `passport` 替代 `result`，新请求不再接受 `result`（传入返回 422），新消息也不再写入该字段。已存入流的历史消息保持原样，不自动迁移。
