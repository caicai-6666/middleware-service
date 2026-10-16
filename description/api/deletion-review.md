# 删除审批结果接口

注册中心发布删除批准或拒绝结果，子平台按自身身份逐条拉取并确认处理。publish、pull、ack 均已实现。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [提交删除审批结果](#提交删除审批结果) | `POST` | `/middleware-service/api/deletion-results/publish` |
| [拉取删除审批结果](#拉取删除审批结果) | `POST` | `/middleware-service/api/deletion-results/pull` |
| [确认删除审批结果](#确认删除审批结果) | `POST` | `/middleware-service/api/deletion-results/ack` |

## 通用约定

路由模块为 `app/router/deletion_review.py`，由 `app/router/__init__.py` 注册。所有接口通过 `Depends(require_platform)` 验证当前会话，从 token 获取调用平台身份。请求体为 JSON，禁止未知字段。

publish 由 `app/service/deletion_review.py` 组装业务字段，共用 `app/service/result_message.py` 的权限、目标校验、去重及发布确认流程；启动时装配到 `app.state.deletion_review`。pull/ack 共用 `app/service/review_consumer.py` 的 `ReviewConsumerService`，独立装配到 `app.state.deletion_review_consumer`。`RABBITMQ_DELETION_RESULT_STREAM` 默认 `deletion-results`，由 RabbitMQ 容器初始化流程幂等创建，复用活跃平台会话持有的 Producer/Consumer。

流程为子平台申请、注册中心审批、子平台正式删除。批准后子平台应在实际删除成功后确认结果，拒绝则记录拒绝后确认。中间件不负责删除子平台的文件，也不验证子平台是否已执行删除；ack 表示子平台声明本次结果已处理。

---

## 提交删除审批结果

`POST /middleware-service/api/deletion-results/publish`，由注册中心向目标子平台发布删除审批结果。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效的注册中心会话，并具备删除结果流的生产权限。调用方身份由 token 缓存注入，不接受请求体指定发送方；注册中心代号来自现有配置。

### 请求参数

Body 参数均为必填，无 path 或 query 参数。

| 字段 | 类型 | 约束与含义 |
| --- | --- | --- |
| `request_id` | string | 1–128 字符，不能仅为空白，原删除申请的 message_id；与目标平台代码组成去重键。 |
| `target_platform_code` | string | 非空且非纯空白，须为配置中存在的子平台，不能为注册中心；允许离线。 |
| `approved` | boolean | 仅接受 JSON true/false，表示是否批准，不代表删除已完成。 |
| `note` | string | 最多 10000 字符，允许为空，注册中心审批意见。 |
| `reviewer` | string | 1–200 字符，不能仅为空白，注册中心审核员。 |

JSON 中的 `"note": ""` 合法并原样写入消息；该字段仍必填，省略、传入 `null` 或超长时返回 `422`。`approved` 必须为真正的布尔值，不接受字符串或数字。消息契约见[删除审批结果消息](../architecture/data/deletion-result.md)。

### 成功响应

状态码 `201 Created`，媒体类型 `application/json`，仅在 RabbitMQ 确认写入后返回：

```json
{"message_id":"新生成的结果消息标识","status":"published","message":"删除审批结果提交成功"}
```

成功只表示审批结果入流，不表示子平台收到消息或完成删除。

### 错误响应

| 状态码 | detail | 触发条件 |
| --- | --- | --- |
| `401` | 令牌缺失、无效或已过期 | 会话验证失败，包括发送前会话失效。 |
| `403` | 仅注册中心可以提交删除审批结果 | 非注册中心或没有对应流的发布权限。 |
| `409` | 下列重复结果对象 | 同目标、同 request_id 的旧结果尚未被该目标确认。 |
| `422` | 字段校验错误或目标平台错误说明 | 字段缺失、格式或长度错误、未知字段，目标不存在或为注册中心。 |
| `503` | 消息服务暂不可用，请稍后重试 | 平台 Stream 资源不可用。 |
| `503` | 无法确认删除审批结果是否重复，请稍后重试或核对此前投递结果 | 去重读取失败或此前投递不确定且尚未在流中找到。 |
| `503` | 下列发布不确定对象 | 发布未获得确认，需核对投递结果，不自动重发。 |

重复结果响应，`offset` 尚未读到时为 `null`：

```json
{"detail":{"message":"该子平台的 request_id 已有尚未确认的删除审批结果","message_id":"此前结果消息标识","offset":10}}
```

发布不确定响应：

```json
{"detail":{"message":"消息发布未获确认，需核对投递结果","message_id":"本次结果消息标识"}}
```

### 请求示例

替换令牌与业务字段后执行：

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/deletion-results/publish" -H "Authorization: Bearer <有效 access_token>" -H 'Content-Type: application/json' -d '{"request_id": "删除申请消息标识", "target_platform_code": "contract-pheno", "approved": true, "note": "同意删除", "reviewer": "注册中心审核员"}'
```

### 行为与边界

消息仅包含审批业务字段、中间件生成的 message_id 和 UTC created_at，不含附件或凭证。发布不修改删除申请的确认进度、不清理审核附件、不执行子平台删除；新删除申请不包含附件；历史申请附件仍由 registry 对删除申请 ack 后清理。

按 `(target_platform_code, request_id)` 去重，依据该目标在删除结果流的 `<target_platform_code>:<stream>` 已持久化确认 offset，registry、其他子平台或入库结果流的进度均不能解除限制。旧结果尚未确认时返回 409，改变 approved、note 或 reviewer 不能绕过限制；目标通过 ack 确认后允许生成新结果，不同目标使用相同 request_id 不冲突。pull 不解除限制。

目标平台无需在线；原删除申请是否存在、是否属于该目标平台由 registry 正确关联，当前没有持久化关联索引，不作查询验证。超时可能只是确认丢失，服务保留不确定占位；后续扫描找到消息则按真实 offset 判断，尚未找到时返回 503，避免盲目重发。进程重启可恢复流中保留的记录，去重仍限定单进程、单 worker，完整机制见[流发布去重服务](../capability/application/stream-publish-deduplication.md)。

---

## 拉取删除审批结果

`POST /middleware-service/api/deletion-results/pull`，子平台每次获取一条属于自己的删除审批结果。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效子平台会话，并具备删除结果流消费权限；registry 不可调用。身份从 token 缓存注入，调用方不能指定其他平台。

### 请求参数

无请求体，无 path 或 query 参数。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`：

```json
{
  "offset": 12,
  "message": {
    "message_id": "本次审批结果消息标识",
    "request_id": "原删除申请消息标识",
    "target_platform_code": "contract-pheno",
    "approved": true,
    "note": "同意删除",
    "reviewer": "注册中心审核员",
    "created_at": "2026-10-10T08:00:00+00:00"
  }
}
```

本次扫描未发现本平台结果时返回 `204 No Content`，无响应体，同样设置 `Cache-Control: no-store`。offset 仅供观察，不是客户端可提交的进度。

### 错误响应

| 状态码 | detail | 触发条件 |
| --- | --- | --- |
| `401` | 令牌缺失、无效或已过期 | 身份验证失败。 |
| `403` | 仅子平台可以消费删除审批结果 | registry 或无对应流消费权限。 |
| `502` | 流消息格式异常或超过缓存上限，未跳过异常消息 | 非 JSON 对象、无法识别消息标识/目标，或单条消息超过缓存字节上限。 |
| `503` | 消费服务暂不可用，请重试 | 资源不可用、订阅失败、无关前缀进度提交/回查失败或超时。 |

### 请求示例

替换令牌与业务字段后执行：

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/deletion-results/pull" -H "Authorization: Bearer <有效 access_token>"
```

### 行为与边界

中间件以认证所得平台代码过滤 target_platform_code，返回完整原始业务消息。每个平台在本流使用独立消费名称 `<platform_code>:<stream>`；其他平台或其他流的确认不会推进本平台的进度。

未 ack 当前结果时重复 pull 返回相同 message_id 和 offset，不交付下一条本平台结果。仅自动确认第一条本平台结果之前连续的其他平台消息，绝不越过待确认结果；提交成功且回查确认后才清除对应缓存。每次最多扫描一个有界批次，若全为其他平台消息，推进无关前缀后返回 204；应继续轮询，204 不表示整个流没有本平台消息。

数量和字节上限由 `DELETION_REVIEW_CACHE_MAX_MESSAGES`（默认 100）及 `DELETION_REVIEW_CACHE_MAX_BYTES`（默认 4194304）配置，在项目 `.env` 和 `.env.example` 中提供，环境变量优先且必须为正整数。缓存与入库结果流独立。平台失活时释放缓存，重新登录或服务重启后从 broker 的确认进度恢复，未确认结果会重新交付。实现细节见[结果消费服务](../capability/application/review-consumer.md)。

---

## 确认删除审批结果

`POST /middleware-service/api/deletion-results/ack`，子平台确认已处理当前拉取的删除审批结果。

### 认证方式

Header：`Authorization: Bearer <access_token>`，必须为有效子平台会话，并具备删除结果流消费权限；registry 不可调用。身份从 token 缓存注入。

### 请求参数

Body 参数均为必填，无 path 或 query 参数。

| 字段 | 类型 | 约束与含义 |
| --- | --- | --- |
| `message_id` | string | 1–128 字符，当前已拉取并处理完成的删除审批结果消息标识。 |

### 成功响应

状态码 `200`，媒体类型 `application/json`：

```json
{"message_id":"本次审批结果消息标识","status":"acknowledged","offset":12}
```

成功表示该平台在删除结果流中的进度已写入 RabbitMQ 并回查确认，不表示中间件执行了文件删除。

### 错误响应

| 状态码 | detail | 触发条件 |
| --- | --- | --- |
| `401` | 令牌缺失、无效或已过期 | 身份验证失败。 |
| `403` | 仅子平台可以消费删除审批结果 | registry 或无对应流消费权限。 |
| `409` | 消息不是当前待确认删除审批结果，请先拉取 | 未拉取、标识不匹配或确认其他平台/尚未交付的结果。 |
| `422` | 字段校验错误列表 | message_id 缺失、类型/长度错误，或提交 offset、platform_code 等未知字段。 |
| `503` | 消费服务暂不可用，请重试 | 资源不可用、进度提交或回查失败、超时。 |

### 请求示例

替换令牌与业务字段后执行：

```bash
curl -X POST "http://127.0.0.1:22000/middleware-service/api/deletion-results/ack" -H "Authorization: Bearer <有效 access_token>" -H 'Content-Type: application/json' -d '{"message_id": "删除审批结果消息标识"}'
```

### 行为与边界

只接受 message_id，由服务匹配当前平台待确认结果的 offset；不能用客户端 offset 跨越其他结果。回查确认后才清除待确认消息及对应缓存，下一次 pull 才能继续交付；失败时保留待确认状态，可重试相同 ack。当前会话内最近一次成功 ack 可重复调用，不回退进度，也不确认下一条消息；该幂等记录只存在内存中，失活或重启后需先重新 pull。

ack 不删除共享流消息、不清理中间件审核附件、不删除子平台原文件。批准结果应在子平台实际删除成功后确认，拒绝结果应在记录拒绝后确认；业务动作与 offset 没有跨系统事务，调用方需按 message_id 保证处理可重试。单进程、单 worker 的消费边界与入库结果一致。
