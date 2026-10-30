# 平台 A2A API

平台间 A2A 使用一个 `/platform-a2a` 路由：所有请求进入统一请求队列；反馈分为广播聚合和定向单聊两条逻辑流。共用回复发布入口，写入后自动完成原请求，独立于个人助理任务队列。存储与调度设计见[平台 A2A 请求队列](../capability/application/platform-a2a.md)。

> **实现状态：** 七个接口均已接入 Redis。请求 publish/pull 共用原队列；原请求未携带 response_id 时回复进入广播聚合，携带时进入单聊反馈队列。每个请求只产生一个最终结果，子平台不能脱离请求主动发送；回复写入后自动完成原请求，不提供 requests/ack。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [发布请求](#发布请求) | `POST` | `/middleware-service/api/platform-a2a/requests/publish` |
| [领取请求](#领取请求) | `POST` | `/middleware-service/api/platform-a2a/requests/pull` |
| [发布回复](#发布回复) | `POST` | `/middleware-service/api/platform-a2a/replies/publish` |
| [读取回复包](#读取回复包) | `POST` | `/middleware-service/api/platform-a2a/replies/pull` |
| [确认回复包](#确认回复包) | `POST` | `/middleware-service/api/platform-a2a/replies/ack` |
| [领取单聊反馈](#领取单聊反馈) | `POST` | `/middleware-service/api/platform-a2a/direct-replies/pull` |
| [确认单聊反馈](#确认单聊反馈) | `POST` | `/middleware-service/api/platform-a2a/direct-replies/ack` |

## 通用约定

- 全部接口使用 `Authorization: Bearer <platform_access_token>`，复用[平台登录](platform.md#平台登录)。调用平台取自令牌，不接受请求体伪造来源。
- 顶层操作仅允许 `knowledge-accumulation`。子平台操作要求调用者不是顶层，且平台配置的 `supports_a2a` 和 `is_sub_management_platform` 均为 true；该角色校验只影响本模块。
- 请求体为 `application/json`，无 path、query 参数，禁止额外字段。所有标识字符串长 1～128 字符且不能仅为空白；平台代码符合 `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`；消息长 1～10000 字符且不能仅为空白。交付凭证与任务 ID 使用相同标识限制。
- userid 是用户隔离及权限判断的输入，不等同于用户授权。当前不查询用户资料、登录 TTL 或管理员表。顶层负责传入正确用户，子平台按该用户验证本地历史上下文和权限；中间件不解释 response_id、不判断其业务过期。
- 成功响应使用 `application/json` 和 `Cache-Control: no-store`。HTTP 错误沿用 detail 结构；存储不可用或结果不确定返回 503，不暴露 Redis 错误，不回退内存。

请求处理流程为顶层发布请求 → 子平台领取 → 子平台发布一个最终回复并自动完成请求。后续消费分两种：

| 原请求 | 反馈流 | 顶层消费方式 |
| --- | --- | --- |
| 无 response_id，多个目标 | 广播聚合 | 按 broadcast_id 读取，整轮确认后丢弃迟到回复。 |
| 无 response_id，只有一个目标 | 广播聚合 | 首次单聊视作单目标广播，取得该平台 response_id 和 message。 |
| 有 response_id 和所属平台 | 单聊反馈 | 持续轮询领取，按 message_id 与反馈凭证逐条确认，确认一个滑动一个名额。 |

分流依据原请求，回复中新生成的 response_id 不会把首次结果切换到单聊流。子平台请求窗口和顶层单聊反馈窗口独立，广播聚合不占顶层单聊窗口。requests/ack 已移除，调用返回 404。

> **平台协作原则：** 平台 A2A 原则上必须回复，受理和不受理都属于需要反馈的业务结果。受理时返回 message 和可用于后续讨论的 response_id；不受理时也返回 message，明确说明不受理及原因，但不返回 response_id（省略或 null）。业务范围外不能以不回复表示拒绝。该原则适用于广播、首次单聊和定向单聊，由接入平台遵守。中间件不根据职责文本代为判断受理范围；明确不受理使用 not_applicable，接口校验该状态的 response_id 必须为空。

“原则上必须回复”是业务协作要求，“是否及时到达”是独立的运行结果。处理缓慢、执行失败、平台不可用或网络异常导致反馈未到达时，不视为平台已经明确拒绝或处理成功，不能据此补造回复或 response_id。对广播收集，顶层仍可在自身等待预算结束后使用已有结果，即使部分或全部目标尚未回复也能确认关闭；不能以收齐反馈作为后续处理的前提。关闭后的迟到广播反馈按既有规则丢弃，不恢复本轮收集，也不阻塞后续业务。中间件不额外引入收齐等待或超时状态枚举。

---

## 发布请求

`POST /middleware-service/api/platform-a2a/requests/publish`：向指定子管理平台原子发布广播、首次单聊或续聊请求。

**认证方式：** 仅 knowledge-accumulation 的平台 Bearer Token。

**请求参数：** 无 path、query 参数，无业务自定义 header。body 如下。

| 字段 | 类型 | 必填 | 默认值与约束 |
| --- | --- | --- | --- |
| `broadcast_id` | string | 是 | 本轮唯一标识，遵循通用标识限制；单聊和续聊也生成新值，同次重试沿用。 |
| `userid` | string | 是 | 请求所属用户，遵循通用标识限制。 |
| `message` | string | 是 | 本轮消息，遵循通用消息限制，保留原文。 |
| `target_platform_codes` | string[] / null | 条件 | 默认 null；广播、首次单聊提供 1～100 个无重复目标代码，必须为已登记、支持 A2A 的子管理平台。 |
| `response_id` | string / null | 条件 | 默认 null；续聊所接续的历史响应标识，遵循通用标识限制。 |
| `response_platform_code` | string / null | 条件 | 默认 null；与 response_id 同时提供，指定本轮唯一续聊目标。 |

目标列表与续聊字段必须二选一，单元素列表表示首次单聊；广播与续聊的目标均不能是顶层。

**成功响应：** `201 application/json`，新发布和相同内容的幂等重试都返回 201。

```json
{
  "status": "published",
  "duplicate": false,
  "broadcast_id": "b-001",
  "userid": "u-001",
  "source_platform_code": "knowledge-accumulation",
  "target_platform_codes": [
    "platform-a",
    "platform-b"
  ],
  "created_at": "2026-10-26T10:00:00Z",
  "tasks": [
    {
      "task_id": "task-a",
      "target_platform_code": "platform-a"
    },
    {
      "task_id": "task-b",
      "target_platform_code": "platform-b"
    }
  ],
  "reply_flow": "broadcast"
}
```

status 固定为 published，仅表示 Redis 返回本轮任务入队成功；duplicate 表示是否命中原发布。created_at 是 Redis 服务端首次入队 UTC 时间，tasks 是各目标对应的任务引用。目标按代码排序，重试返回原任务 ID 和创建时间，不表示子平台已经处理。

**错误响应：**

| 状态码 | 触发条件 |
| --- | --- |
| `401` | 平台令牌缺失、无效或过期。 |
| `403` | 非顶层平台。 |
| `409` | 同 broadcast_id 被用于不同用户、消息、目标或续聊信息。 |
| `422` | 字段不合法、额外字段、目标重复、广播与续聊组合错误，或目标未登记、不是 A2A 子管理平台。 |
| `429` | 至少一个目标平台已达到未完成任务容量，整次广播未发布。 |
| `503` | Redis 不可用、数据异常或操作结果不确定；沿用 broadcast_id 及原参数重试。 |

业务错误示例：`{"detail":"broadcast_id 已用于不同的请求内容"}`；参数校验使用 FastAPI 标准 detail 列表。

**请求示例：** 替换 TOP_TOKEN 为顶层令牌、目标为已配置的 A2A 子管理平台。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/requests/publish' \
  -H 'Authorization: Bearer TOP_TOKEN' -H 'Content-Type: application/json' \
  -d '{"broadcast_id":"b-001","userid":"u-001","message":"请分析这个问题。","target_platform_codes":["platform-a","platform-b"]}'
```

续聊请求使用新的 broadcast_id：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/requests/publish' \
  -H 'Authorization: Bearer TOP_TOKEN' -H 'Content-Type: application/json' \
  -d '{"broadcast_id":"b-002","userid":"u-001","message":"请进一步解释。","response_id":"response-001","response_platform_code":"platform-a"}'
```

**行为与边界：** 一次广播固定目标集合，每个目标生成独立 task_id。先检查所有目标，再统一写入，避免部分目标成功。广播 ID 的去重范围跨用户；目标列表排序不影响指纹，其他内容变化返回 409。容量检查前先识别幂等重试，已满时仍可返回相同请求的原发布记录。广播、任务和发布记录当前不设置业务 TTL；不自动合并用户消息，不判断历史 response_id 是否存在或过期。修改平台配置后重试仍需通过当前目标角色校验。

---

## 领取请求

`POST /middleware-service/api/platform-a2a/requests/pull`：按目标平台配置自动补齐执行窗口的剩余名额，可显式续租已领取任务。

**认证方式：** 支持 A2A 的子管理平台 Bearer Token，只能领取本平台任务。

**请求参数：** 无 path、query 参数；必填业务 header `Idempotency-Key` 为 1～128 字符非空白标识，同次网络重试保持该键及续租参数不变，每次新领取或续租使用新值。请求体可省略，省略、null 和 {} 均表示无续租参数；有请求体时使用 application/json。

| body 字段 | 类型 | 必填 | 默认值与约束 |
| --- | --- | --- | --- |
| `renew_tasks` | object[] | 否 | 默认 []，最多 100 项，不能重复 task_id，任一凭证无效则整次操作失败。 |
| `renew_tasks[].task_id` | string | 每项必填 | 当前已领取任务，遵循通用标识限制。 |
| `renew_tasks[].delivery_token` | string | 每项必填 | 该任务当前交付凭证，遵循通用标识限制。 |

**成功响应：** `200 application/json`，队列为空或窗口已满也返回 200 和空 tasks。

```json
{
  "duplicate": false,
  "capacity": 3,
  "inflight_count": 1,
  "available_slots": 2,
  "tasks": [
    {
      "task": {
        "task_id": "task-a",
        "broadcast_id": "b-001",
        "userid": "u-001",
        "source_platform_code": "knowledge-accumulation",
        "target_platform_code": "platform-a",
        "message": "请分析这个问题。",
        "response_id": null,
        "response_platform_code": null,
        "created_at": "2026-10-26T10:00:00Z",
        "sequence": "1",
        "reply_flow": "broadcast"
      },
      "delivery_token": "delivery-001",
      "lease_expires_at": "2026-10-26T10:05:00Z"
    }
  ],
  "renewed_tasks": []
}
```

capacity 是本平台配置的 k，优先使用平台 YAML 的 a2a_max_inflight，省略或 null 时使用服务默认 PLATFORM_A2A_MAX_INFLIGHT_PER_PLATFORM（默认 3）；inflight_count 和 available_slots 是操作完成时该平台总执行数及剩余窗口，包含所有用户和实例。tasks 是本次新领取列表；renewed_tasks 使用相同交付对象结构，表示本次成功续租的任务。每个交付对象包含 task、delivery_token 和 lease_expires_at；task 的 sequence 是原始发布顺序的十进制字符串，created_at 和 lease_expires_at 均为 Redis 服务端 UTC 时间。首次请求的 response_id 和 response_platform_code 为 null；续聊时包含原发布的历史响应及所属平台。duplicate=true 表示回放原领取批次，可能返回其他调用续租后的最新截止时间，但本次重放不修改任务租约。

**错误响应：**

| 状态码 | 触发条件 |
| --- | --- |
| `401` | 平台令牌缺失、无效或过期。 |
| `403` | 调用者不是已登记且支持 A2A 的子管理平台。 |
| `409` | 相同 Idempotency-Key 使用不同参数；续租凭证不匹配、已过期或属于其他平台；原领取回执中的任务交付已失效。 |
| `422` | 缺少幂等 header、字段不合法、超出续租数量限制、重复续租任务或存在额外字段；旧 limit 参数也视为额外字段，返回 422。 |
| `503` | Redis 不可用、数据异常或结果不确定；沿用相同 Idempotency-Key 和原 body 重试。 |

业务冲突示例：`{"detail":"Idempotency-Key 已用于不同的领取参数"}`。409 时不进行本次续租或新领取。

**请求示例：** 替换 SUB_TOKEN 为目标子管理平台令牌；每次新调用使用新的唯一幂等键。普通领取无需请求体。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/requests/pull' \
  -H 'Authorization: Bearer SUB_TOKEN' \
  -H 'Idempotency-Key: pull-001'
```

续租并自动补齐剩余窗口，任务与凭证替换为实际领取结果；调用方需同时处理返回的 tasks 和 renewed_tasks：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/requests/pull' \
  -H 'Authorization: Bearer SUB_TOKEN' -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: renew-001' \
  -d '{"renew_tasks":[{"task_id":"task-a","delivery_token":"delivery-001"}]}'
```

**行为与边界：** 实际新领取数为 min(待领取任务数, max(0, 本平台窗口上限 - 有效执行中任务数))，由 Redis 原子计算。同平台所有用户和实例共享窗口，不同平台互不占用窗口。显式续租列表顺序不影响幂等指纹；任一项失败则整体不生效。续租成功后也会领取新任务填满窗口，不再提供 limit=0 的仅续租模式。普通 pull、平台心跳、幂等重试均不会续租未明确指定的任务。

超时任务在下次新 pull 时恢复原始 FIFO 顺序，重领换凭证，旧凭证失效。回执命中只返回原批次，不额外领取或续租；空批次也会回放为空，发现新消息需使用新键。原批次内任一任务已完成或交付失效后重放返回 409，停止使用旧凭证并以新键重新领取。升级移除 limit 参数后应为新调用生成新幂等键，旧领取回执不作参数迁移。回执默认保留 86400 秒，保留期过后无重试保障；调用方不得为新操作复用旧键。服务重建不丢失 Redis 中的任务及窗口，但平台会话仍按原规则重新登录。

本接口不判断业务消息或模型上下文过期，不保证旧执行者因租约到期而停止，也不提供同一会话串行或用户公平调度。子平台处理结束后调用 replies/publish，成功写入回复时自动清理原请求并释放名额；未提交回复的任务仍会超时重领。

---

## 发布回复

`POST /middleware-service/api/platform-a2a/replies/publish`：提交本轮回复，并在同一个 Redis 原子操作中完成原请求、释放并发名额，不需要请求 ack。

**认证方式：** 支持 A2A 的子管理平台 Bearer Token，只能回复本平台持有有效交付凭证的任务。

**请求参数：** 无 path、query 参数，无业务自定义 header。body 如下。

| 字段 | 类型 | 必填 | 默认值与约束 |
| --- | --- | --- | --- |
| `task_id` | string | 是 | 已领取任务的中间件标识，遵循通用标识限制。 |
| `delivery_token` | string | 是 | 当前交付凭证，遵循通用标识限制，必须在有效租约内首次提交。 |
| `status` | string | 是 | 仅允许 succeeded、not_applicable、context_expired、failed，分别表示已答复、不受理、历史上下文过期、处理失败；timeout 是顶层本地结果，不允许上报。 |
| `message` | string | 是 | 业务答复或不受理、失败、过期说明，所有状态均必填，遵循通用消息限制。 |
| `response_id` | string / null | 否 | 默认 null；not_applicable 必须省略或为 null，不能携带任何响应 ID。succeeded 兼容 null，但不等同于不受理。 |

广播、用户、回复来源和 reply_flow 由原任务及认证平台关联，不接受客户端额外提交 userid、broadcast_id、reply_flow 或来源平台。上下文过期时可以直接返回 context_expired，无需调用模型。

| status | 含义 | 响应标识与续聊 |
| --- | --- | --- |
| `succeeded` | 已答复 | 受理原则上返回 response_id；兼容历史空值，空值仅表示没有可续聊标识，不能推断为不受理。 |
| `not_applicable` | 不受理 | message 说明原因，response_id 必须省略或 null，否则返回 422。 |
| `context_expired` | 上下文已过期 | 不继续沿用过期引用，由顶层重新发起首次请求。 |
| `failed` | 处理失败 | 展示失败说明，不从失败反馈新增续聊授权。 |

以上四种是子平台反馈类型，与 knowledge-accumulation 的 A2AReply 契约对齐。timeout 是顶层等待结束后对缺失反馈生成的本地结果，不由中间件代替子平台发布；本接口拒绝该状态。不能以 succeeded + 空 ID 代替明确的 not_applicable，不从 message 原文猜测业务类型。


**成功响应：** `201 application/json`，新提交和相同内容的幂等重试都返回 201。

```json
{
  "status": "published",
  "duplicate": false,
  "task_id": "task-a",
  "broadcast_id": "b-001",
  "request_status": "completed",
  "reply_disposition": "saved",
  "completed_at": "2026-10-26T10:03:00Z",
  "reply_flow": "broadcast",
  "message_id": "reply-a"
}
```

status 固定为 published，request_status 固定为 completed，表示 Redis 已原子处理回复并清理原请求、释放名额。reply_disposition 为 saved 时结果已保存在 reply_flow 指定的反馈流中，为 discarded 时广播已经关闭，回复被丢弃但原请求仍正常完成。reply_flow 表示 broadcast 聚合或 direct 单聊反馈；message_id 是本条反馈的唯一标识。completed_at 是首次完成时的 Redis 服务端 UTC 时间；duplicate 表示是否重放完成回执，重试保持原时间和处置结果，不重复释放名额。

**错误响应：**

| 状态码 | 触发条件 |
| --- | --- |
| `401` | 平台令牌缺失、无效或过期。 |
| `403` | 调用者不是已登记且支持 A2A 的子管理平台。 |
| `409` | 任务未领取、不属于调用平台、凭证不匹配、租约已过期；已完成任务的凭证或回复内容不同；原任务和完成回执均已不存在。 |
| `422` | 字段、状态或额外字段不合法；not_applicable 携带非空 response_id 也会被拒绝，校验失败不写入、不完成原请求。 |
| `429` | 单聊反馈队列已满，原请求未完成、名额不释放；子平台保留已生成结果、及时续租后重试。 |
| `503` | Redis 不可用、数据异常或操作结果不确定；沿用同一任务、凭证、状态、消息及 response_id 重试。 |

业务冲突示例：`{"detail":"该任务已完成，重复提交的交付凭证或回复内容不一致"}`。认证、凭证和状态校验失败时不会保存回复、删除请求或释放名额。

**请求示例：** 替换 SUB_TOKEN 为子管理平台令牌、task_id 和 delivery_token 为领取结果。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/replies/publish' \
  -H 'Authorization: Bearer SUB_TOKEN' -H 'Content-Type: application/json' \
  -d '{"task_id":"task-a","delivery_token":"delivery-001","status":"succeeded","message":"分析结果……","response_id":"response-001"}'
```


不受理同样提交最终反馈并完成原请求，不需要产生模型上下文：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/replies/publish' \
  -H 'Authorization: Bearer SUB_TOKEN' -H 'Content-Type: application/json' \
  -d '{"task_id":"task-a","delivery_token":"delivery-001","status":"not_applicable","message":"该请求不属于本平台业务范围，不予受理。","response_id":null}'
```

**行为与边界：** 回复按原任务的 reply_flow 分流：broadcast 按 broadcast_id 聚合；direct 按到达序号进入顶层单聊反馈队列。单次成功提交同时移除原任务正文、执行记录和凭证，使平台可以领取下一条请求，任意任务完成即可滑动窗口。succeeded、not_applicable、context_expired、failed 均为本轮终态，都会完成任务，中间件不会为 failed 自动重试；平台需要重新发起时应使用新 broadcast_id。

完成回执按平台和任务隔离，默认保留 86400 秒，相同凭证和相同回复内容的重试返回原成功结果，即使原租约后来已到期。不同凭证或内容返回 409，不能改写先前回复。回执过期后，原任务已经清理，重复提交返回 409，不会再次插入回复。完成原任务后重发原广播仍命中发布幂等记录，不重新创建任务。

未完成任务的租约过期后禁止旧执行者首次提交；重领后使用新凭证，旧凭证无法影响新执行。广播关闭的迟到回复仍返回成功并释放窗口，不因丢弃而要求子平台持续重试。关闭只作用于对应首次请求的广播集合，不关闭子平台会话；之后用新的本轮 ID 和已知 response_id 发起的定向单聊不受影响。

回复提交成功不代表顶层已经读取或接受该结果。子平台请求窗口此时已经释放；direct 反馈被顶层领取后，才占顶层自己的反馈窗口，顶层处理或丢弃后确认释放。不要再调用已移除的 requests/ack。

---

## 读取回复包

`POST /middleware-service/api/platform-a2a/replies/pull`：按首次请求的 broadcast_id 读取当前广播聚合结果；首次单聊也使用此入口。

**认证方式：** knowledge-accumulation 的平台 Bearer Token，仅顶层可消费。

**请求参数：** 无 path、query 参数。body 必填 `broadcast_id`，遵循通用标识约束，只能是未携带历史 response_id 的首次广播或首次单聊。本接口不需要 Idempotency-Key。

**成功响应：** `200 application/json`。

```json
{
  "broadcast_id": "b-001",
  "userid": "u-001",
  "state": "open",
  "target_platform_codes": [
    "platform-a",
    "platform-b"
  ],
  "pending_platform_codes": [
    "platform-b"
  ],
  "replies": [
    {
      "message_id": "reply-a",
      "task_id": "task-a",
      "broadcast_id": "b-001",
      "userid": "u-001",
      "platform_code": "platform-a",
      "status": "succeeded",
      "response_id": "response-001",
      "message": "分析结果……",
      "created_at": "2026-10-26T10:03:00Z"
    }
  ]
}
```

state 为 open 时继续收集，closed 时已结束收集。replies 中每条包含响应所属 platform_code、可用于续聊的 response_id、message、状态及用户等元数据；pending_platform_codes 表示尚未回复的平台。关闭后 replies 和 pending_platform_codes 均为空，不再等待。

**错误响应：** `401` 认证失败；`403` 非顶层；`404` 广播不存在；`409` 关联 ID 属于定向单聊，必须使用 direct-replies；`422` 参数错误；`503` 存储不可用或数据异常。 错误沿用 detail 结构，业务错误为字符串，参数校验为 FastAPI 错误列表。

**请求示例：** 替换 TOP_TOKEN 为顶层令牌，关联 ID 与反馈凭证使用实际返回值。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/replies/pull' \
  -H 'Authorization: Bearer TOP_TOKEN' -H 'Content-Type: application/json' \
  -d '{"broadcast_id":"b-001"}'
```

**行为与边界：** 只读，不领取租约、不删除回复、不占顶层单聊窗口。一次返回当前所有已收集反馈，可以稍后再读，不承诺本次已包含所有目标。每条目标只提交一个最终结果，按目标平台代码排序。顶层可保存平台及 response_id 后发起新的定向请求；关闭原广播不使这些响应标识失效。

---

## 确认回复包

`POST /middleware-service/api/platform-a2a/replies/ack`：按广播 ID 原子关闭整轮聚合收集并清理反馈。

**认证方式：** knowledge-accumulation 的平台 Bearer Token，仅顶层可消费。

**请求参数：** 无 path、query 参数。body 必填 `broadcast_id`，遵循通用标识约束，无需快照 ID 或反馈凭证。本接口不需要 Idempotency-Key。

**成功响应：** `200 application/json`。

```json
{
  "broadcast_id": "b-001",
  "state": "closed",
  "duplicate": false,
  "closed_at": "2026-10-26T10:04:00Z"
}
```

state 固定 closed；duplicate 表示是否重复关闭；closed_at 是首次关闭时的 Redis UTC 时间，重试不改变。

**错误响应：** `401` 认证失败；`403` 非顶层；`404` 广播不存在；`409` 定向单聊关联 ID 不能作为广播关闭；`422` 参数错误；`503` 关闭结果不确定，沿用原广播 ID 重试。 错误沿用 detail 结构，业务错误为字符串，参数校验为 FastAPI 错误列表。

**请求示例：** 替换 TOP_TOKEN 为顶层令牌，关联 ID 与反馈凭证使用实际返回值。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/replies/ack' \
  -H 'Authorization: Bearer TOP_TOKEN' -H 'Content-Type: application/json' \
  -d '{"broadcast_id":"b-001"}'
```

**行为与边界：** 确认的是整个收集过程。读取后、确认前到达的回复也会被清理；确认后到达的广播反馈直接丢弃，但子平台原请求仍会完成并释放名额。可以在未收齐时关闭，也可直接关闭尚无回复的广播，不强制停止已经运行的模型任务。关闭和回复写入使用原子脚本，竞态不会重新建立已关闭集合。关闭标记和发布幂等记录当前持续保留，广播 ID 不复用；后续定向单聊不受影响。

---

## 领取单聊反馈

`POST /middleware-service/api/platform-a2a/direct-replies/pull`：持续领取带历史 response_id 的请求产生的反馈，自动补齐顶层剩余窗口。

**认证方式：** knowledge-accumulation 的平台 Bearer Token，仅顶层可消费。

**请求参数：** 无 path、query 参数。必填 header `Idempotency-Key`，1～128 字符非空白标识，新操作使用新值，同次重试沿用原值和参数。body 可省略、null 或 {}；可选 `renew_messages` 默认 []、最多 100 项，每项必填 `message_id`、`delivery_token`，均遵循通用标识约束；不能重复 message_id。不接受 limit。

**成功响应：** `200 application/json`。

```json
{
  "duplicate": false,
  "capacity": 3,
  "inflight_count": 1,
  "available_slots": 2,
  "messages": [
    {
      "message": {
        "message_id": "direct-001",
        "task_id": "task-b",
        "broadcast_id": "b-002",
        "userid": "u-001",
        "platform_code": "platform-a",
        "status": "succeeded",
        "response_id": "response-002",
        "message": "分析结果……",
        "created_at": "2026-10-26T10:03:00Z"
      },
      "delivery_token": "feedback-delivery-001",
      "lease_expires_at": "2026-10-26T10:08:00Z"
    }
  ],
  "renewed_messages": []
}
```

messages 为本次新领取反馈，renewed_messages 为明确续租的反馈，两者都包含 message、delivery_token 和 lease_expires_at。message 包含本轮结果 response_id 与所属 platform_code、userid、message 文本及原 task_id；顶层处理后使用 message.message_id 和反馈 delivery_token 确认。capacity、inflight_count、available_slots 只描述顶层单聊反馈窗口，独立于子平台请求窗口。

**错误响应：** `401` 认证失败；`403` 非顶层；`409` 幂等参数冲突、续租凭证或原批次失效；`422` 缺少幂等 header、非法参数、重复续租或旧 limit 字段；`503` 领取结果不确定，沿用同一幂等键及参数重试。 错误沿用 detail 结构，业务错误为字符串，参数校验为 FastAPI 错误列表。

**请求示例：** 替换 TOP_TOKEN 为顶层令牌，关联 ID 与反馈凭证使用实际返回值。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/direct-replies/pull' \
  -H 'Authorization: Bearer TOP_TOKEN' \
  -H 'Idempotency-Key: feedback-pull-001'
```

**行为与边界：** 窗口优先读取 knowledge-accumulation 的 a2a_max_inflight，未配置时使用全局默认 3。所有用户和顶层实例共享窗口，不传 limit；已领取且尚未确认的反馈占用名额。到达序号 FIFO，生成慢的请求可能晚到，不保证同一会话按发起顺序完成。空队列或满窗口返回 200 和空 messages。反馈业务上已不需要时仍可领取后直接确认丢弃。续租后也会补齐剩余名额；一项续租失败整次不生效。幂等命中只回放原批次，不额外领取或续租，空批次也记录。租约过期后下次新领取恢复原顺序并更换凭证；已确认消息不会再次交付。原批次任一消息已确认或交付失效后，重放返回 409。

---

## 确认单聊反馈

`POST /middleware-service/api/platform-a2a/direct-replies/ack`：顶层处理或丢弃一条单聊反馈后确认消费，释放一个顶层名额。

**认证方式：** knowledge-accumulation 的平台 Bearer Token，仅顶层可消费。

**请求参数：** 无 path、query 参数。body 必填 `message_id` 和 `delivery_token`，均遵循通用标识约束，来自 direct-replies/pull 的 message.message_id 和该条反馈交付凭证。本接口不需要 Idempotency-Key，消息及凭证本身提供确认幂等。

**成功响应：** `200 application/json`。

```json
{
  "message_id": "direct-001",
  "status": "acknowledged",
  "duplicate": false,
  "acknowledged_at": "2026-10-26T10:05:00Z"
}
```

status 固定 acknowledged；duplicate 表示相同消息和凭证的确认重试；acknowledged_at 是首次确认的 Redis UTC 时间。成功时该条正文、反馈执行记录和凭证已经清理。

**错误响应：** `401` 认证失败；`403` 非顶层；`409` 反馈未领取、凭证不匹配、租约过期、已被重领或确认回执已失效；`422` 参数错误；`503` 确认结果不确定，沿用原消息及凭证重试。 错误沿用 detail 结构，业务错误为字符串，参数校验为 FastAPI 错误列表。

**请求示例：** 替换 TOP_TOKEN 为顶层令牌，关联 ID 与反馈凭证使用实际返回值。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform-a2a/direct-replies/ack' \
  -H 'Authorization: Bearer TOP_TOKEN' -H 'Content-Type: application/json' \
  -d '{"message_id":"direct-001","delivery_token":"feedback-delivery-001"}'
```

**行为与边界：** 每确认一条释放一个反馈窗口名额，无需等待更早的消息。未领取的反馈不能直接确认；旧凭证不能确认后来重领的消息。确认回执默认保留 86400 秒，相同凭证重试不重复释放名额、不刷新保留期；回执过期后重试返回 409。确认不关闭子平台会话，也不影响任何广播聚合或其他单聊反馈。子平台原请求早已在回复写入时完成，此处只确认顶层消费反馈。

---

## 代码职责与依赖

[平台 A2A 路由](../../app/router/platform_a2a.py)定义一个共同前缀下的七个接口，由[总路由](../../app/router/__init__.py)注册。反馈读取与确认仅允许顶层，子平台只能基于本平台有效请求交付凭证提交一个最终结果，不支持无新请求的主动推送或同一任务分多段反馈。

请求和两类反馈均由独立 service 和 Redis 存储编排，复用现有平台会话和连接池，不新增第三方依赖；完整存储键、窗口、租约、容量与持久化边界见[平台 A2A 请求队列](../capability/application/platform-a2a.md)。
