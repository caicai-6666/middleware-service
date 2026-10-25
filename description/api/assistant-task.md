# AI 个人助理任务接口

为每个用户维护独立的优先级任务队列，提供发布、单任务领取、完成确认和状态查询。底层使用 Redis，不创建新的 RabbitMQ Stream。每个助理只保留任务队列，平台负责交付处理结果，完成后通过 `ack` 确认原任务；设计原则见[助理任务队列](../capability/application/assistant-task.md#设计原则)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [发布助理任务](#发布助理任务) | `POST` | `/middleware-service/api/assistant/tasks/publish` |
| [领取助理任务](#领取助理任务) | `POST` | `/middleware-service/api/assistant/tasks/pull` |
| [确认助理任务](#确认助理任务) | `POST` | `/middleware-service/api/assistant/tasks/ack` |
| [查询助理任务状态](#查询助理任务状态) | `GET` | `/middleware-service/api/assistant/tasks/status` |

## 通用约定

非空业务响应使用 `application/json`；publish 兼容纯文本 JSON 和携带附件的 `multipart/form-data`。四个接口均通过请求头 `Authorization: Bearer <platform_access_token>` 认证平台，身份来自现有平台会话；不接受请求体伪造来源平台。目前所有有效登录平台均可操作指定 `userid` 的队列，不额外限制为 registry 或 knowledge-accumulation，也不以用户登录 TTL 控制任务存续。平台负责确认目标用户及任务来源的业务合法性；本接口不校验用户资料是否已同步，不执行用户级授权。

优先级从高到低固定为 `reminder`（提醒）、`user_message`（用户发给自己助理的消息）、`assistant_message`（其他助理发来的消息）、`heartbeat`（助理保活心跳）。同级按成功入队序号先进先出；已经领取且租约有效的任务不受插队影响。定时触发由生产端负责，中间件不提供提醒调度。

`heartbeat` 由生产端定期发布，用于没有业务消息时唤醒助理；消费者自行决定心跳处理行为，处理完仍需 `ack`。心跳沿用必填、非空的 `message`，例如“执行一次空闲心跳”。中间件不会自动生成心跳，队列为空仍返回 204；心跳也不会自动续期平台登录、用户登录或当前任务租约。每次周期心跳应使用不同的 `request_id`，同一次发布重试沿用相同标识。心跳参与普通任务的容量、幂等和超时恢复规则，不自动合并或丢弃。

任务对象由以下字段组成：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `task_id` | string | 中间件生成的任务唯一标识 |
| `userid` | string | 接收任务的用户唯一标识 |
| `task_type` | string | 上述四种类型之一 |
| `message` | string | 单条保持原文；多条用户消息之间仅添加空行和 Markdown 分隔线 |
| `source_platform_code` | string | 创建任务的首条消息的认证来源平台 |
| `source_assistant_id` | string 或 null | 发送方助理唯一标识；合并用户任务取首条来源，各条来源见 user_messages |
| `request_id` | string 或 null | 创建任务的首条消息的幂等标识，未提供时为 null |
| `created_at` | UTC datetime | 合并用户任务取组内最晚消息的接收时间，其他任务取初次入队时间 |
| `sequence` | string | 用户队列内递增的十进制序号，字符串避免大整数精度丢失 |
| `user_messages` | array | 用户任务按接收顺序排列的原始消息列表；其他任务类型为 [] |
| `files` | array | 本任务所有附件引用汇总，无附件时为 [] |

`user_messages` 中每项包含 `message_id`（原始消息独立标识）、`message`（原文）、`source_platform_code`（本条消息认证来源）、`source_assistant_id`（本条消息声明的发送方助理标识或 null）、`request_id`（本条消息幂等标识或 null）、`created_at`（本条消息的 UTC 接收时间）、`files`（属于本条消息的附件引用）。不同来源平台向同一 userid 发布的用户消息也可合并，各条来源与附件独立保存。

`userid` 表示接收消息的用户，`source_assistant_id` 表示发送方助理，两者不可混用。助理标识由生产平台定义并填写，例如发送方助理所属用户的 userid；中间件不从目标 userid 或平台令牌推断助理身份，也不校验助理标识是否已注册。新发布的 assistant_message 必须提供非空助理来源；其他类型未提供来源及历史未记录的数据返回 null。历史无来源助理任务仍可正常 pull/ack，不强制补填或推断。`assistant_message` 无论来源相同或不同，每次新发布均独立入队，不参与合并；仅 user_message 按合并规则处理。

文件引用仅包含 `file_id` 和 `file_extension`，后缀由生产者显式传入，不依据原始文件名或 MIME 类型推断。文件二进制保存在 `data/assistant-task/file/`，不进入 Redis。使用[助理附件下载接口](resource.md#读取助理任务附件)取得文件，任务确认后清理。合并任务应根据 `user_messages[].files` 对应原文与附件，任务根部 `files` 仅用于完整汇总。

队列结构、租约、清理与配置见[助理任务队列](../capability/application/assistant-task.md)。

---

## 发布助理任务

`POST /middleware-service/api/assistant/tasks/publish`：向指定用户的助理写入一条任务。

**认证：** 有效平台 Bearer Token。

**请求参数：** 无 path/query 参数。无附件时可以继续使用 JSON body；上传附件时使用 multipart 表单。以下普通字段在两种媒体类型中保持一致：

| 字段 | 必填 | 类型与约束 |
| --- | --- | --- |
| `userid` | 是 | string，1～128 字符，不能纯空白 |
| `task_type` | 是 | `reminder`、`user_message`、`assistant_message`、`heartbeat` |
| `message` | 是 | string，1～10000 字符，不能纯空白 |
| `request_id` | 否 | string，1～128 字符，不能纯空白；省略或 null 表示不启用发布幂等 |
| `source_assistant_id` | assistant_message 必填 | string，1～128 字符，不能纯空白；助理类型不接受省略或 null，其他类型可省略或为 null |

multipart 额外支持以下字段；JSON 不接受文件引用或文件字段：

| 字段 | 必填 | 类型与约束 |
| --- | --- | --- |
| `files` | 否 | 重复出现的二进制文件字段，默认最多 10 个，每个最多 20971520 字节（20 MiB） |
| `file_extensions` | 有文件时是 | 重复出现的后缀字符串字段，与 files 数量一致、按各自列表顺序一一对应；1～16 位英文字母或数字、不带点，保存为小写 |

每次发布的文件全部属于本次 message；相同普通字段不允许重复。上传文件时仍须提供非空 message。所有四类任务都可带附件；只有 user_message 自动合并。multipart 省略 request_id 表示不去重；assistant_message 必须提供非空白 source_assistant_id，其他类型可省略，不能用空字符串代替省略。JSON 和 multipart 使用同一条件必填校验，缺失、null、空串或纯空白来源返回 422，校验失败不会入队或落盘业务附件。

不接受额外字段，特别是客户端提交的优先级数字、来源平台、入队时间和序号。

**成功响应：** `201 application/json`，新任务、合并追加和幂等重试均返回此状态码；`duplicate` 区分是否重复，`merged` 表示本次原始发布是否追加到已有待领取用户任务。

```json
{
  "status": "published",
  "duplicate": false,
  "merged": false,
  "task": {
    "task_id": "7c7eb58d72594a4c8872710ccf6742be",
    "userid": "10001",
    "task_type": "reminder",
    "message": "请提醒用户参加会议。",
    "source_platform_code": "knowledge-accumulation",
    "source_assistant_id": null,
    "request_id": "meeting-20261024-001",
    "created_at": "2026-10-24T07:00:00Z",
    "sequence": "1",
    "user_messages": [],
    "files": []
  }
}
```

**错误响应：** `401` 平台认证失败；`409` 同来源平台、同用户、同 `request_id` 已绑定不同类型、正文、附件后缀或内容；`413` 单个附件过大；`415` 媒体类型不支持；`422` 请求字段、文件数量或后缀不合法；`429` 单用户保留任务数达到上限；`503` Redis 不可用、文件保存/清理失败或写入结果不确定。

**请求示例：** 将 URL、平台令牌和用户 ID 替换为实际值。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/assistant/tasks/publish' \
  -H 'Authorization: Bearer <platform_access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"userid":"10001","task_type":"reminder","message":"请提醒用户参加会议。","request_id":"meeting-20261024-001"}'
```

上传多文件示例：文件路径替换为本机实际文件。`file_extensions` 的第一项对应 `files` 第一项，以此类推。让 curl 自动生成 multipart Content-Type 和 boundary。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/assistant/tasks/publish' \
  -H 'Authorization: Bearer <platform_access_token>' \
  -F 'userid=10001' \
  -F 'task_type=user_message' \
  -F 'message=请结合合同和截图分析付款条款。' \
  -F 'request_id=user-message-001' \
  -F 'file_extensions=pdf' -F 'files=@./contract.pdf' \
  -F 'file_extensions=png' -F 'files=@./screenshot.png'
```

例如首条用户消息附带 PDF，第二条附带 PNG，合并任务中的对应关系如下；省略了其他元数据：

```json
{
  "user_messages": [
    {"message": "请分析合同。", "files": [{"file_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "file_extension": "pdf"}]},
    {"message": "这是补充截图。", "files": [{"file_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "file_extension": "png"}]}
  ],
  "files": [
    {"file_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "file_extension": "pdf"},
    {"file_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "file_extension": "png"}
  ]
}
```

**行为与边界：** 去重范围为 `(来源平台, userid, request_id)`。未完成任务的幂等记录持续保留，确认后继续保留默认 86400 秒；期间相同内容返回原任务及原时间，`duplicate=true`，即使任务已经完成也不会重新执行。保留期过后允许重新使用该请求标识。没有 `request_id` 的重试按新消息处理，用户类型可能再次追加到待领取任务。达到容量上限时，相同请求的幂等重试仍可返回原任务。

source_assistant_id 参与内容幂等校验：同一去重范围内换成另一个发送助理会返回 409，不会覆盖原来源。其他类型省略或 null 均沿用历史无助理来源的指纹规则。新发布及重试 assistant_message 都必须提供来源；历史无来源发布不能通过补填来源重试来覆盖。助理类型消息的最小请求示例如下，发布成功后 task.source_assistant_id 及后续 pull 的同名字段均为 assistant-10002：

```json
{
  "userid": "10001",
  "task_type": "assistant_message",
  "message": "这是来自另一位助理的消息。",
  "source_assistant_id": "assistant-10002",
  "request_id": "assistant-message-001"
}
```

对 `user_message`，发布时尝试追加到该用户最近一个尚未领取且未达到上限的用户任务；任务 ID 和排序序号保持不变，`created_at` 更新为组内最晚一条用户消息的接收时间，返回 `merged=true`。每条新消息仍保存独立消息 ID、接收时间和幂等标识。合并后的任务最多默认 20 条原始消息、原文 UTF-8 总计 65536 字节；达到上限后另建任务，不丢弃或截断消息。单条消息超过合并字节阈值时独立成任务，仍受原接口 10000 字符限制。

新消息与该任务上一条原始用户消息的接收时间间隔不得超过 `ASSISTANT_USER_MESSAGE_MERGE_INTERVAL_SECONDS`，默认 30 秒；恰好等于阈值允许合并，超过则另建任务。间隔根据 Redis 服务端时间判断，每次成功追加后以这条新消息为下一次比较基准，而非始终与首条比较。幂等重试不会刷新基准；如果服务端时钟回拨导致间隔为负，也不合并。

合并后每任务附件总数也不得超过 `ASSISTANT_TASK_MAX_FILES`，默认 10 个，超过时另建任务。每条原始消息的附件不被拆散。幂等指纹包含按上传顺序排列的后缀与文件内容 SHA-256，忽略客户端文件名及新生成文件 ID；相同内容重试返回原引用并清理本次多余上传。同 request_id 改变附件内容、后缀、顺序或数量返回 409。文件已随成功 ack 清理时，发布重试仍返回原快照，不重新生成附件。

任务一旦领取，其消息组成永久冻结，超时重领也不追加；后来消息进入下一条待领取任务。合并间隔只决定两条消息能否合并，不会故意延迟领取来收集消息。提醒保持最高优先级，其他类型不参与用户消息合并。创建时间更新不会重排任务，同级仍按原入队序号排序。达到用户任务容量上限时，满足合并条件的消息仍可追加；需要新任务而无剩余容量时返回 429。

每条原始消息分别去重。重试返回该次原始发布的响应快照和 `merged` 标记，不反映后来追加的内容；消费时以 pull 返回的冻结任务为准。重复发布不会再次追加消息。无 `request_id` 的用户消息重试可能再次追加，因此生产端应为每条原始消息提供稳定标识。

---

## 领取助理任务

`POST /middleware-service/api/assistant/tasks/pull`：领取该用户最高优先级的一条待执行任务；同级按入队序号排序。用户任务可能包含发布阶段合并的多条原始消息，领取时冻结整组内容。

**认证：** 有效平台 Bearer Token。有效租约内的任务绑定领取平台代码；同一平台重新登录后仍可取回当前任务。

**请求参数：** 无 path/query 参数。JSON body 仅包含必填 `userid`，string，1～128 字符，不能纯空白。

**成功响应：** 有任务返回 `200 application/json`；队列为空返回 `204`，没有响应体。

```json
{
  "task": {
    "task_id": "7c7eb58d72594a4c8872710ccf6742be",
    "userid": "10001",
    "task_type": "reminder",
    "message": "请提醒用户参加会议。",
    "source_platform_code": "knowledge-accumulation",
    "source_assistant_id": null,
    "request_id": "meeting-20261024-001",
    "created_at": "2026-10-24T07:00:00Z",
    "sequence": "1",
    "user_messages": [],
    "files": []
  },
  "delivery_token": "d45d727f0fae4359a7b0fc2522b682b02",
  "lease_expires_at": "2026-10-24T07:05:00Z"
}
```

**错误响应：** `401` 平台认证失败；`409` 有其他平台持有有效执行租约；`422` 请求不合法；`503` Redis 不可用、队列数据异常或领取结果不确定。

**请求示例：** 将 URL、平台令牌和用户 ID 替换为实际值。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/assistant/tasks/pull' \
  -H 'Authorization: Bearer <platform_access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"userid":"10001"}'
```

**行为与边界：** 每个用户最多一条执行中任务。同平台重复调用返回同一任务、同一交付凭证，并从本次调用续期默认 300 秒；因此同一平台的并发消费者也可能获得同一任务，调用方需串行调度或进行任务幂等处理。长任务必须在租约到期前重复调用续租。

多条用户消息的 `message` 按接收顺序拼接，消息之间仅插入 `\n\n---\n\n`，不额外添加消息编号、标题或时间；原消息 Markdown 和换行完整保留。结构化 `created_at` 字段仍保留 UTC 时间，单条用户消息的 `message` 保持原文。

```markdown
帮我看看这份合同。

---

重点关注付款条款。

---

另外检查违约责任。
```

租约到期后，下一次 `pull` 才把原任务按原优先级、原序号放回待执行集合，再选择最高优先级任务；新的交付生成新凭证。这个恢复过程不会主动中断旧执行者，但会拒绝其旧凭证确认。平台心跳、用户登录查询不会续期任务租约。空队列不阻塞，调用方自行控制轮询频率。

---

## 确认助理任务

`POST /middleware-service/api/assistant/tasks/ack`：确认任务已经处理完成，移除正文和执行中记录；合并用户任务一次确认全部原始消息，并为所有原始发布标识设置去重保留期。

**认证：** 有效平台 Bearer Token，必须与领取该任务的平台代码相同；并提交对应交付凭证。

**请求参数：** 无 path/query 参数。JSON body 如下：

| 字段 | 必填 | 类型与约束 |
| --- | --- | --- |
| `userid` | 是 | string，1～128 字符，不能纯空白 |
| `task_id` | 是 | string，1～128 字符，来自 pull 返回任务 |
| `delivery_token` | 是 | string，1～128 字符，来自本次 pull，不能纯空白 |

**成功响应：** `200 application/json`。幂等保留期内重复确认返回 `duplicate=true`。

```json
{
  "task_id": "7c7eb58d72594a4c8872710ccf6742be",
  "status": "acknowledged",
  "duplicate": false
}
```

**错误响应：** `401` 平台认证失败；`409` 未领取任务、用户/任务/平台/交付凭证不匹配、租约已过期或确认回执已过保留期；`422` 请求不合法；`503` Redis 不可用、确认结果不确定或已确认但附件清理失败。

**请求示例：** 将 URL、平台令牌、用户 ID、任务 ID 和交付凭证替换为实际值。

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/assistant/tasks/ack' \
  -H 'Authorization: Bearer <platform_access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"userid":"10001","task_id":"7c7eb58d72594a4c8872710ccf6742be","delivery_token":"d45d727f0fae4359a7b0fc2522b682b02"}'
```

**行为与边界：** 完成确认会释放该用户的任务容量。确认回执默认保留 86400 秒，匹配同平台及同交付凭证的重试不会影响后来领取的任务。`ack` 不接收或保存业务结果，表示调用平台声明任务业务处理已经完成，中间件不核验外部交付是否成功。发给其他助理的消息应先成功发布到目标任务队列；发给用户的消息应由平台先完成发送或可靠交接，再确认原任务。调用方须处理“发送成功但确认失败”导致的重试幂等，完整规则见[设计原则](../capability/application/assistant-task.md#设计原则)。

确认后清理本任务全部附件；调用方必须先下载、处理或可靠转存所需文件。Redis 确认成功而磁盘清理失败时返回 503，并提示“任务已确认但附件清理失败”，相同 ack 可根据回执中的文件清单重试删除。清理不会因重复 ack 误删后续任务附件。发布结果不确定时保留文件，避免已入队任务引用被删；此类故障可能留下未引用文件，当前没有后台扫描回收，需要后续核对处理。

---

## 查询助理任务状态

`GET /middleware-service/api/assistant/tasks/status`：查询指定用户助理当前是否有有效执行中任务，以及最近一次确认心跳的时间。

**认证：** 有效平台 Bearer Token，沿用其他助理任务接口的访问范围。

**请求参数：** 无请求体，无 path 参数。query 必填 `userid`，string，1～128 字符，不能纯空白。

**成功响应：** `200 application/json`，带 `Cache-Control: no-store`。未知用户或没有历史记录的用户也返回 200，不自动创建助理或心跳记录。

```json
{
  "userid": "10001",
  "has_in_progress_task": true,
  "task_id": "7c7eb58d72594a4c8872710ccf6742be",
  "task_type": "user_message",
  "lease_expires_at": "2026-10-24T07:05:00Z",
  "last_heartbeat_at": "2026-10-24T06:59:00Z"
}
```

| 响应字段 | 说明 |
| --- | --- |
| `userid` | 查询的用户标识 |
| `has_in_progress_task` | 是否存在租约仍有效的已领取任务 |
| `task_id` | 有效执行中任务 ID；无有效任务时为 null |
| `task_type` | 有效执行中任务类型；无有效任务时为 null |
| `lease_expires_at` | 当前有效任务的 UTC 租约截止时间；无有效任务时为 null |
| `last_heartbeat_at` | 最近一次成功 ack heartbeat 任务的 UTC 时间；无记录时为 null |

**错误响应：** `401` 平台认证失败；`422` 用户标识缺失、不合法或纯空白；`503` Redis 不可用或状态无法读取。

**请求示例：** 将 URL、用户 ID 和平台令牌替换为实际值。

```bash
curl --get 'http://127.0.0.1:22000/middleware-service/api/assistant/tasks/status' \
  -H 'Authorization: Bearer <platform_access_token>' \
  --data-urlencode 'userid=10001'
```

**行为与边界：** 接口只读，不领取任务、不续租、不生成心跳、不执行过期任务恢复。仅有待领取消息时，`has_in_progress_task=false`；租约过期也返回 false，过期执行记录留待后续 pull 恢复。返回值描述 Redis 记录，不保证旧执行者已经停止，也不直接表示助理在线或离线。

历史心跳仅在心跳任务首次成功确认时记录。心跳发布、领取、普通任务完成、重复或失败确认均不会更新该时间。时间保存在 Redis 中，不随任务确认清理和后端重启消失；升级前已完成但未记录的心跳不回填。没有记录时返回 null。响应不包含任务正文或交付凭证。
