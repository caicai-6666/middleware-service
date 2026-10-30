# 平台 A2A 请求队列

本模块承接 knowledge-accumulation 发起的广播、首次单聊和定向续聊。所有请求共用原有请求队列，反馈拆成广播聚合与单聊反馈两类逻辑流，均使用 Redis。接口契约见[平台 A2A API](../../api/platform-a2a.md)，平台角色来自[平台身份配置](platform-config.md)。本模块独立于个人助理任务，不共享其队列、租约、容量或消息合并逻辑。

## 代码职责与装配

| 文件 | 主要职责 |
| --- | --- |
| `app/router/platform_a2a.py` | 一个共同前缀下的七个接口、请求模型、平台认证、角色与参数校验、HTTP 错误映射。 |
| `app/service/platform_a2a.py` | 目标校验、反馈流判定、指纹、任务及反馈模型、顶层消费编排。 |
| `app/infrastructure/redis_platform_a2a.py` | Lua 原子发布、租约队列、反馈分流、完成请求、广播关闭及逐条反馈确认。 |
| `app/config/platform_a2a.py` | 从项目 .env 与进程环境读取默认窗口、容量、租约和回执保留时间。 |
| `app/main.py` | 复用 Redis 连接池，装配 application.state.platform_a2a，统一处理存储故障。 |

七个接口均已实现。不提供独立 requests/ack，回复写入时原子完成原请求；不支持子平台无新请求时主动推送，也不支持同一任务分多段回复。复用现有 redis、FastAPI 和 Pydantic，不新增依赖，不创建 RabbitMQ Stream，也不使用 Redis Streams。完整应用启动仍按原生命周期检查 Redis 和 RabbitMQ。

---

## 请求路由与反馈分流

| 原请求 | 目标 | 内部 reply_flow | 顶层反馈消费 |
| --- | --- | --- | --- |
| 无 response_id，多目标 | 目标平台列表 | broadcast | 按 broadcast_id 聚合读取、整轮确认。 |
| 无 response_id，单目标 | 单元素目标列表 | broadcast | 首次单聊视为单目标广播，取得平台响应上下文。 |
| 有 response_id | response_platform_code | direct | 持续领取单聊反馈、逐条确认。 |

reply_flow 由原请求是否含历史 response_id 决定并随任务保存；子平台回复里新生成的 response_id 不改变本轮分流。客户端不传 reply_flow。每个请求任务仅允许一个最终结果，succeeded（已答复）、not_applicable（不受理）、context_expired（上下文过期）、failed（失败）均为终态；timeout 只属于顶层本地等待结果，不允许子平台上报。

每轮发布仍使用新的 broadcast_id，续聊时该字段只作为本轮请求关联和幂等标识，不能通过广播聚合接口读取或关闭。顶层从首次聚合结果取得 platform_code、response_id 后，使用新的本轮 ID 发起定向续聊，子平台的最终结果进入单聊反馈队列；下一轮可继续使用结果中的新 response_id。

只有已认证的 knowledge-accumulation 可以发布请求、消费两类反馈。子平台必须已经登记、不是顶层，且 supports_a2a、is_sub_management_platform 均为 true，才能成为目标、领取请求和提交回复。广播列表无重复、最多 100 个目标，非法目标使整轮发布失败。

userid 从顶层请求保存到任务和反馈，不接受子平台另行伪造。当前不查询用户资料、用户登录 TTL 或平台管理员表，也不解释 opaque response_id 或验证其用户归属；顶层负责传入正确用户，子平台按该用户校验本地上下文和权限。上下文过期由子平台直接返回 context_expired，顶层可再次发起无历史响应标识的首次单聊。

---

## 两个独立的滑动窗口

子管理平台领取请求时占用自己的请求窗口，最终回复可靠写入后释放；顶层领取单聊反馈时占用自己的反馈窗口，逐条 ack 后释放。广播聚合读取没有消费租约，不占顶层单聊窗口。顶层未领取或未确认单聊反馈，不会继续占用子平台请求窗口。

两种窗口都优先使用消费者平台 YAML 的 a2a_max_inflight（严格整数 1～100）；省略或 null 时使用 PLATFORM_A2A_MAX_INFLIGHT_PER_PLATFORM（默认 3）。顶层配置决定反馈窗口，子平台配置决定请求窗口，所有用户和实例共享对应窗口，但不同消费方向的 Redis 记录与计数独立。

客户端不传 limit，普通领取可省略请求体。新领取数量为 min(待领取数, max(0, k - 有效租约数))，在 Redis 原子计算。请求队列按发布序号 FIFO；单聊反馈按结果到达序号 FIFO，不能保证同一会话按请求发起顺序完成，不做用户公平调度或会话串行锁。

租约使用 Redis TIME。请求通过 renew_tasks、单聊反馈通过 renew_messages 显式续租，续租成功后也会补齐剩余名额；任一凭证失效则整次操作不生效。平台登录心跳、普通领取、幂等回放都不续未明确列出的租约。

到期条目在下一次新领取时恢复原序号，重新交付并换凭证；旧凭证不能提交首次结果或确认新交付。超时不删除业务数据，也不能强制停止已经运行的模型调用。下调窗口不撤销已有有效交付，只停止新领取直到计数低于上限。

---

## 广播聚合与关闭

replies/pull 只接受 broadcast 流的关联 ID，原子读取当前已到达回复及尚未回复的平台列表。每条反馈包含 platform_code、response_id、message、status、userid 和原任务信息，按平台代码排序。首次单聊也使用此入口。读取不删除、不关闭，顶层可以等待后再次读取。

接入平台应遵守[必须反馈的协作原则](../../api/platform-a2a.md#通用约定)：受理时返回消息和用于后续讨论的 response_id，不受理时使用 not_applicable 返回说明消息，response_id 必须省略或 null。该原则适用于两类反馈，业务范围外也不能静默忽略。中间件仅传递反馈，不判断平台业务范围，也不将“无 response_id”自动解释为不受理或执行失败；不受理反馈按普通反馈完成原任务、释放名额并进入原请求对应的反馈流。

状态枚举由 service 中的 PlatformA2AReplyStatus 统一定义，发布模型、服务入口及反馈输出模型共用校验函数；not_applicable 带响应标识在写入前拒绝，HTTP 返回 422，原请求及窗口不变。保留 succeeded + null 的历史兼容，不把该组合改写为不受理。此校验也覆盖广播和单聊反馈的读取模型，防止不合法的不受理结果被交付。

该原则不构成及时到达保证。收集不设置所有目标必须回复的屏障，慢响应、失败或断线不能让顶层无限等待。等待预算由顶层决定，可在只有部分结果或没有结果时关闭，继续自身业务；缺失反馈与明确不受理需要区分。不因未到达而增加强制超时配置、自动生成的子平台反馈或中间件超时状态。

replies/ack 确认整个收集过程：同一脚本内将状态设为 closed、记录首次关闭时间并清理已有回复。读取后、确认前到达的结果也会清理；之后到达的同一轮广播回复直接丢弃，但原请求仍完成并释放子平台名额。可以在未收齐时关闭，不强制停止子平台执行。

关闭和回复提交都是 Redis 原子操作，竞态无论先后都不会复活已关闭集合。重复关闭返回原时间与 duplicate=true。关闭后查询返回 closed，replies 和 pending_platform_codes 均为空，表示不再等待。关闭标记和发布幂等记录持续保留，广播 ID 不复用。

关闭只作用于该轮收集，不使子平台 response_id 失效，也不影响新关联 ID 下的定向单聊。未知广播返回 404，direct 关联 ID 误用聚合读取或关闭返回 409。

---

## 单聊反馈消费与背压

原请求携带 response_id 时，子平台最终结果进入顶层单聊反馈队列，保留原 task_id、本轮 broadcast_id、userid、响应平台、新 response_id 和 message。每条反馈有独立 message_id，顶层领取获得新的反馈 delivery_token，该凭证与子平台执行请求时的凭证不同。

direct-replies/pull 持续领取并自动补齐顶层窗口；direct-replies/ack 在顶层处理或丢弃后逐条确认，清理该条正文和执行状态，释放一个名额。任意一条确认即可滑动窗口，不必等待更早条目；未领取的反馈不能直接确认。确认不关闭会话，不影响广播或其他反馈。

单聊反馈队列包含待领取与已领取但未确认的全部条目，达到容量时回复发布返回 429，不保存结果、不完成原请求、不释放子平台名额。子平台应保留已生成结果、续租原请求并重试，不需要重新调用模型；顶层消费确认后恢复容量。这种背压防止顶层持续不消费时无限积压。广播反馈不进入该队列。

---

## 原子性与幂等

发布指纹包含来源平台、用户、消息、排序后的目标和续聊信息。同广播 ID 相同内容返回原任务和时间，不同内容返回 409，目标排列变化不构成新请求；ID 范围跨用户。先检查全部目标容量，再统一入队，避免半次广播。

两种有租约领取都要求 Idempotency-Key，按各自队列隔离。同次重试使用相同键和续租参数，排序后的续租列表用于指纹。回执只保存引用和凭证，重放不额外领取或续租；空批次也回放为空，新轮询要用新键。原批次任一条已完成、已确认或凭证失效后，回放返回 409。同键不同参数返回 409；回执过保留期后不提供重试保障，旧键不复用。

回复发布的指纹包括 task_id、子平台凭证、状态、原文和 response_id。检查有效租约、分流写入、原请求清理、子平台名额释放和完成回执在同一脚本内完成。相同结果重试命中原完成回执，不重复写入任何反馈；即使顶层已经消费并清理反馈，也不会被子平台重试重新插入。

顶层单聊反馈 ack 使用 message_id 与反馈凭证幂等，同一凭证重试返回原确认时间，不重复释放、不刷新保留期；回执过期后原消息已清理，重试返回 409。子平台任务完成回执与顶层反馈确认回执是不同键，不能互相替代。

Lua 只提供执行隔离，不提供出错回滚；脚本在首次写入前检查相关键类型、解析正文并编码结果，降低数据异常导致部分变更的风险。广播原任务定位以 Redis 事务同时读任务或完成回执，随后脚本重新校验；存储故障统一返回 503，不回退内存。发布、关闭沿用 broadcast_id，领取沿用 Idempotency-Key，子平台回复沿用任务、凭证和完整结果，顶层确认沿用消息和反馈凭证重试。

---

## Redis 存储结构

全部数据位于 `<REDIS_KEY_PREFIX>:platform-a2a`，与个人助理隔离。所有“流”均为逻辑消费方向，底层是 Hash、Sorted Set 与原子脚本。

| 后缀 | 类型 | 用途 |
| --- | --- | --- |
| `:sequence` | String | 请求发布递增序号，同广播各目标共享。 |
| `:broadcast:<sha256(id)>` | Hash | 发布指纹、原响应、reply_flow、open/closed 状态及关闭时间。 |
| `:broadcast:<sha256(id)>:replies` | Hash | 广播反馈按原任务 ID 聚合，确认后删除。 |
| `:platform:<code>:pending/tasks/inflight/tokens` | Sorted Set / Hash | 子平台请求待领取、正文、租约和凭证。 |
| `:platform:<code>:pull:<sha256(key)>` | Hash | 子平台领取幂等回执，有保留期。 |
| `:platform:<code>:completed:<sha256(task_id)>` | Hash | 子平台回复完成回执，有保留期。 |
| `:direct-replies:sequence` | String | 单聊反馈到达递增序号。 |
| `:direct-replies:pending/tasks/inflight/tokens` | Sorted Set / Hash | 顶层单聊反馈待领取、内部信封、租约和凭证。 |
| `:direct-replies:pull:<sha256(key)>` | Hash | 顶层反馈领取幂等回执，有保留期。 |
| `:direct-replies:ack:<sha256(message_id)>` | Hash | 顶层逐条确认回执，有保留期。 |

两种租约队列复用底层算法和内部信封格式，不共享状态。单聊反馈信封内部 task_id 指 message_id，公开反馈正文的 task_id 始终是原请求任务 ID。新请求明确保存 reply_flow；历史已收集结果缺少该字段时按原广播聚合读取，旧完成回执兼容补齐反馈标识。

当前仅支持项目单节点 Redis，不支持 Redis Cluster 多槽原子操作。持久化依赖部署 AOF 与数据卷，未承诺异常断电零丢失，见[Redis 部署](../infrastructure/redis.md#持久化与边界)。

---

## 配置与运行边界

模板见 [.env.example](../../../.env.example)。进程环境优先于项目 .env；平台 YAML 及环境配置修改后重启，各实例使用一致配置。

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `PLATFORM_A2A_MAX_INFLIGHT_PER_PLATFORM` | `3` | 平台 YAML 未配置 a2a_max_inflight 时的消费窗口，1～100。 |
| `PLATFORM_A2A_MAX_TASKS_PER_PLATFORM` | `1000` | 每子平台未完成请求容量，以及顶层未确认单聊反馈容量的各自上限，正整数。 |
| `PLATFORM_A2A_LEASE_SECONDS` | `300` | 子平台请求及顶层单聊反馈的执行租约秒数。 |
| `PLATFORM_A2A_PULL_RECEIPT_TTL_SECONDS` | `86400` | 两种领取回执的保留秒数，不能小于租约。 |
| `PLATFORM_A2A_COMPLETION_RECEIPT_TTL_SECONDS` | `86400` | 子平台回复完成与顶层反馈确认回执的各自保留秒数。 |

每次最多续租 100 条，窗口最大 100，确保 Lua 对交付记录的遍历有界。广播最多 100 个目标，读取按发布目标逐一查找。任务和反馈正文不设置业务过期，完成请求、逐条确认或关闭广播时清理；请求发布元数据、关闭标记当前持续保留，尚无自动归档清理。需要按实际平台数、积压量和消息大小规划 Redis 内存。

### 本机 IDE 联调配置

本机 `.env` 已显式设置以下开发参数，代码默认值和 `.env.example` 保持不变：

```dotenv
REDIS_MAX_CONNECTIONS=64
PLATFORM_A2A_MAX_INFLIGHT_PER_PLATFORM=3
PLATFORM_A2A_MAX_TASKS_PER_PLATFORM=1000
PLATFORM_A2A_LEASE_SECONDS=300
PLATFORM_A2A_PULL_RECEIPT_TTL_SECONDS=3600
PLATFORM_A2A_COMPLETION_RECEIPT_TTL_SECONDS=86400
```

连接池参数参考本轮[负载验证](platform-a2a-validation.md)，不等同于平台执行窗口。领取回执在本机缩短为一小时，仍长于五分钟租约；同一领取幂等键的回放保障仅覆盖该保留期。完成与确认回执仍保留一天。该调整不回溯缩短已经写入的 Redis 回执有效期，也不清理广播元数据。

平台会话继续使用本机已有的 `5s` 有效期、`1s` 心跳和 `1s` 清理周期，见[开发快速失效配置](platform-session.md#本机开发快速失效配置)。其他连接信息和凭据沿用本机 `.env`，不使用实验命名空间或模拟平台表。

IDE 选择 `middleware-service` Conda 环境，以本项目根目录为工作目录，通过模块 `app.main` 运行（命令等价于 `python -m app.main`），监听 `127.0.0.1:22000`。配置在启动时加载；IDE 若额外设置同名环境变量，将覆盖 `.env`。

---

## 验证状态

三项目真实组件联调、六个模拟子平台的负载与容量恢复，以及 Redis 连接池突发限制和轮询回执占用，见[平台 A2A 联调与负载验证](platform-a2a-validation.md)。该在线验证与以下隔离单元测试分别记录，不把模拟业务吞吐当作真实模型性能。

2026-10-26 使用独立 Redis 7.4.8 临时容器和随机键前缀验证，原有 27 项测试全部通过。原请求发布、平台窗口、续租、重领、幂等、自动完成、容量和权限用例继续通过；新增广播聚合、首次单聊、关闭与迟到丢弃、关闭/回复并发、首次响应转定向续聊、顶层反馈窗口、并发消费者、逐条确认幂等、反馈续租和超时重领、队列满时原请求保留及消费后的恢复。

反馈类型对齐后扩展为 29 项并全部通过，新增 not_applicable 的广播与单聊反馈、空响应标识约束、拒绝非法组合时原请求不变、幂等与窗口释放；HTTP 验证不受理携带 response_id 和子平台上报 timeout 返回 422。使用 knowledge-accumulation-be 的实际 DTO 源码只读验证中间件输出的广播包和单聊窗口 JSON，均可解析；succeeded + null 的兼容行为一致。未修改上游代码或真实业务数据。

HTTP 验证覆盖认证、角色、参数及 200/201/404/409/503 响应；429 容量分支通过真实 Redis 服务测试，并与其他错误码一起声明在 OpenAPI，嵌套对象字段都有说明；请求 ack 保持 404，旧 limit 返回 422。测试使用本机忽略目录 `tests/test_platform_a2a.py`，按现有规则不提交测试目录。测试只清理自身前缀，临时容器结束后移除，未操作现有 Redis/RabbitMQ 数据。未执行生产整栈启动、高可用或 Redis 异常断电验证。
