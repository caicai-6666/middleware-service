# 流发布去重服务

`StreamPublishDeduplicator` 在 `app/service/stream_deduplication.py` 中复用流历史扫描、业务键索引、并发锁及发布占位，根据实际接收方已持久化的确认 offset，拒绝尚未完成的重复消息。

本文是共用机制的主文档；各接口的字段和响应分别见[入库申请](../../api/ingestion.md)、[删除申请](../../api/deletion.md)、[入库审核结果](../../api/ingestion-review.md)、[删除审批结果](../../api/deletion-review.md)。

---

## 适用范围与消费进度

| 流默认名称 | 去重键 | 确认进度名称 | 当前状态 |
| --- | --- | --- | --- |
| `ingestion-requests` | `(platform_code, source_id)` | `<registry_code>:<ingestion_request_stream>` | 已接入。 |
| `deletion-requests` | `(platform_code, source_id)` | `<registry_code>:<deletion_request_stream>` | 已接入。 |
| `ingestion-results` | `(target_platform_code, request_id)` | `<target_platform_code>:<ingestion_result_stream>` | 已接入，按目标子平台独立判断。 |
| `deletion-results` | `(target_platform_code, request_id)` | `<target_platform_code>:<deletion_result_stream>` | 已接入，按目标子平台独立判断，目标通过 ack 推进进度。 |

所有流名称、registry 代码和 RabbitMQ 连接参数来自现有配置。业务键精确比较，区分大小写，不裁剪空白；平台身份仍由 token 决定。不同流使用独立服务对象、索引和锁，不共享确认进度。

若同键旧消息的 offset 大于其接收方的确认 offset，返回 409；等于或小于已确认进度时可重提并生成新的 message_id。没有持久化消费进度时按 -1 处理。若历史中已有多条同键消息，以最后一条为准。

申请流的接收方固定为 registry；结果流的接收方为目标子平台。结果发布不参考 registry 的 offset，也不采用全部子平台进度的最大值或最小值。B 平台确认至较大 offset 不会解除 A 平台尚未确认的反馈限制。

仅 pull 或缓存预取不解除重复限制，实际确认进度是唯一判断依据。审核结果内容不参与去重：修改 note、reviewer、passport、approved 或申请附件不能绕过未确认限制。

---

## 实现位置与关键流程

| 位置 | 职责 |
| --- | --- |
| `app/service/stream_deduplication.py` | 共享算法：恢复及增量更新业务键索引，读取接收方确认进度，保护同键检查和发布。 |
| `app/service/ingestion_deduplication.py` | 入库申请配置适配，复用共享算法，保留原服务和异常导入名称。 |
| `app/service/ingestion.py`、`app/service/deletion.py` | 先认证授权及去重，再发布和登记占位；只有入库申请保存新附件，删除申请直接发送业务信息。 |
| `app/service/review.py`、`app/service/deletion_review.py` | 分别组装入库和删除结果业务字段，调用共用结果发布流程。 |
| `app/service/result_message.py` | 确认 registry 权限和目标有效性，以目标代码及原请求 ID 去重，发布并保留确认不确定占位。 |
| `app/infrastructure/stream_stats.py` | 补充原生 StreamStats、ResolveOffsetSpec 查询和最后一个业务块读取，确定扫描终点。 |
| `app/router/ingestion.py`、`app/router/deletion.py`、`app/router/ingestion_review.py`、`app/router/deletion_review.py` | 重复转换为 409，无法可靠判断转换为 503。 |
| `app/main.py` | 为四个 publish 分别装配独立的共享去重服务。 |

申请流首次恢复从 registry 的确认 offset 下一条开始；结果流首次恢复从位置 0 扫描全部保留消息，再按各目标平台自己的确认进度筛除已完成记录。不能从当前目标的 offset 开始恢复结果流全局索引，否则会漏掉其他平台位于较小 offset 的未确认消息。

之后仅扫描上次终点之后的新增消息，重新查询确认进度并清除已确认索引。结果流分别查询当前目标及索引中其他目标的进度，每条记录只与其目标的进度比较。完整扫描和全部必要查询成功后，才更新可信索引；失败不留下半份状态。

索引保存业务键、message_id 和实际流 offset，不保留完整消息或附件。固定 64 个分组锁避免为所有历史键永久保存锁对象；同键锁覆盖去重检查、文件保存和发布确认，保证单进程并发只有一条能提交。不同键可能因哈希分组短暂等待；索引刷新单独串行，附件保存和发布不持有刷新锁。

---

## 消费只读扫描与业务终点

扫描使用独立 Consumer 及 `middleware:publish-deduplication` 订阅名，不占用业务消费者的订阅，不调用 store_offset，不删除流消息。整个过程不推进任何平台确认进度。

RabbitMQ 的确认追踪记录也占用流位置，而普通订阅只交付业务数据，不能直接等待 StreamStats 的 committed_offset 对应一条业务回调。服务先以 ResolveOffsetSpec 解析 LAST 业务块位置，结合统计识别空流，再读取该块的 num_records 得到最后一条业务消息 offset；最后完整扫描至该边界。

块首位置不能替代块内最后一条消息，也不能以短时未收到消息推断扫描已完成。业务 offset 因确认追踪记录而出现间隔属于正常情况，不要求连续。

Producer 的发布序号不是流 offset。发布获得确认后保留 message_id 占位，后续扫描取得实际 offset 后替换；确认尚未在扫描中可见时，仍拦截重复。同键发布尚未返回、其他键的扫描已观察到该消息时，不重复保留占位，避免错误阻塞已经完成的消息。

---

## 失败、恢复与边界

- 重复申请在发布新消息前返回 409，入库申请还在保存新附件前拒绝；去重键没有新增持久化字段，新删除申请不含附件引用。
- 发布异常或取消请求可能发生在发送之后，保留投递不确定占位。后续找到消息则按真实 offset 判断；始终未找到时同键返回 503，不自动重发或清除占位。入库申请附件继续保留供核对，无附件删除申请仅保留消息标识及占位。
- 明确的字段、身份、权限、文件保存失败不登记占位。原 ack 已持久化进度但附件清理失败时，按已确认进度判断，允许再次发布。
- 平台失活不清除去重状态；重新登录不能绕过未确认限制。目标离线也不妨碍 registry 发布反馈，去重直接读取 broker 中的持久化进度。
- 重启后内存索引消失，首次发布从 RabbitMQ 恢复已入流的可识别记录。尚未入流的不确定占位没有持久化；跨系统事务和端到端恰好一次仍未实现。
- 历史消息缺少业务标识字段时无法参与该键去重，保持原样、不补值；具有业务标识却缺少合法平台代码或 message_id、非 JSON 或非对象消息使扫描失败，不猜测或静默跳过。
- 只保障一个中间件进程，部署保持单 worker，业务发布统一经过本服务。多副本、多 worker 或绕过本服务的并发写入不受本地锁保护。
- 保留策略已经删除的历史消息不能从流恢复，不将去重索引当成永久业务登记。运行中删除或重建流、回退消费进度不受支持；检测到末尾回退时返回 503，需处理管理操作后重启。

---

## 配置、依赖及验证

无新增环境变量、管理 HTTP 端口或外部缓存。沿用现有 Stream 主机、端口、vhost 和统一账号密码；原生协议适配要求 RabbitMQ 4.3 或以上，当前部署镜像满足。适配依赖固定的 rstream 1.1.0 连接、认证、心跳、订阅及关闭接口，升级依赖后需要重新验证。

协议定义见 [RabbitMQ Stream 协议](https://github.com/rabbitmq/rabbitmq-server/blob/main/deps/rabbitmq_stream/docs/PROTOCOL.adoc)，统计末尾的版本要求见 [StreamStats API](https://rabbitmq.github.io/rabbitmq-stream-java-client/stable/api/com/rabbitmq/stream/StreamStats.html)。仅使用专用连接，不修改第三方安装文件或全局解码器。

一次刷新总超时 30 秒，订阅读取扫描终点最多等待 15 秒。首次恢复开销与需要检查的保留消息量相关：申请流检查未确认区间，结果流检查整个保留区间。后续增量检查新增消息和目标平台进度；积压导致超时时返回 503，不降级为放行。

验证覆盖同键并发、跨流和跨平台隔离、确认 offset 等于消息位置、其他平台较大 offset 不解除限制、旧记录恢复、投递不确定状态及 409/503 响应。申请及入库结果的真实 RabbitMQ 验证使用三个独立合成流、合成平台会话和临时文件，检查 publish/pull/ack；删除结果另用独立临时流验证 HTTP publish/pull/ack、目标进度隔离、重建恢复及确认后重新发布。测试完成后关闭客户端并删除测试流和文件，不写入正式业务流。
