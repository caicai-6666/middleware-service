# 删除申请消费服务

## 用途与职责

`app/service/deletion_consumer.py` 的 `DeletionConsumerService` 为 registry 提供有界预取、逐条交付、消费确认，并兼容历史临时附件清理，由 `app/main.py` 装配为 `app.state.deletion_consumer`。接口约定见[删除申请 API](../../api/deletion.md)，消息结构见[删除申请契约](../../architecture/data/deletion-request.md)。

---

## 机制与依赖

删除与入库申请共用 `app/service/file_request_consumer.py` 的 `FileRequestConsumerService`，底层预取仍使用 `StreamBatchReader`。两个服务实例各自持有缓存、待确认和最近确认记录，通行证通过 app/service/passport.py 统一为字符串数组，历史单字符串在交付前兼容转换。新删除消息没有附件引用，确认只提交 offset；历史删除附件仍使用独立目录处理。

消费必须通过会话资源管理器校验流权限，删除服务还显式校验注册中心代号。平台锁保护 pull/ack 与资源清理，同一 registry 的多个流操作串行化。稳定消费名称为 `<registry_code>:<deletion_request_stream>`，从 RabbitMQ 已保存 offset 的下一位置恢复，无进度时从 FIRST 开始。

预取最多等待 2 秒，达到条数或字节上限后取消订阅。队首未确认时重复 pull 返回同一条，不推进 offset；新消息 ack 回查 offset 后直接移除队首。历史消息包含 file 引用时，先验证旧引用，再提交和回查 offset，最后清理旧附件并移除队首。最近成功确认可以重试。异常消息不跳过，超大消息返回 502。

删除服务注册会话释放回调，失活/停机同步清空全部缓存；重登时未确认申请可重投。进度、文件删除和业务处理没有跨系统事务，registry 需幂等处理。

---

## 配置与部署

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DELETION_CACHE_MAX_MESSAGES` | `100` | 删除申请缓存的消息条数上限。 |
| `DELETION_CACHE_MAX_BYTES` | `4194304` | 删除申请缓存的原始负载字节上限。 |

两项必须为正整数，由项目根 `.env` 与进程环境变量读取，环境变量优先。与 `INGESTION_CACHE_*`、`REVIEW_CACHE_*` 独立，部署 Compose 已注入；修改后需重启。字节数不包含 Python 对象和底层客户端缓冲，不是进程总内存上限。

仅为历史消息保留附件目录 `data/deletion-request/file`，容器挂载独立 `deletion-files` 卷。file_id 必须与 message_id 一致，文件标识及后缀禁止路径分隔符；非法引用在提交 offset 前拒绝。

---

## 失败与边界

新消息确认不访问文件。进度提交失败保留待确认状态；历史附件消息还保留对应文件。进度已提交但回查异常时重试先回查。历史附件删除失败返回明确 503，保留当前队首，重试同一 ack 再清理；文件不存在视为已完成。

历史附件消息的 offset 已保存后、文件删除前的崩溃可能导致孤立文件，没有后台补偿。只支持单进程/单副本消费状态；不允许多个实例写同一消费名称。消费者不能通过 offset 阻止 Stream 保留策略删除历史消息。

消费确认不会删除子平台原文件，不自动产生删除审批反馈；registry 可调用[删除审批结果 publish](../../api/deletion-review.md#提交删除审批结果) 发布结果，子平台通过该结果流的 pull/ack 获取并确认。附件下载见[审核附件读取 API](../../api/resource.md)。

---

## 验证

单元测试覆盖 registry 独占消费、重复 pull、错误/重复 ack、新消息无需附件确认、与入库流的状态和历史附件隔离、提交失败重放、清理失败重试、非法路径及缓存配置。无附件申请的真实 RabbitMQ 临时流联调覆盖 HTTP 发布到消费、offset 回查、服务重建恢复、确认后重提和空流 204，并验证全程不生成临时文件。历史附件清理继续由原有兼容测试验证，完成后清理测试流与客户端。
