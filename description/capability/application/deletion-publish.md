# 删除申请发布服务

## 用途与依赖

`DeletionService` 在 `app/service/deletion.py` 中校验子平台删除申请字段，通过共用的 `FileMessagePublisher` 保存审核附件和发布消息。在 `app/main.py` 中装配到 `app.state.deletion`，复用会话管理器和长期持有的 Producer。

调用接口见[删除申请 API](../../api/deletion.md)，精确消息结构见[消息契约](../../architecture/data/deletion-request.md)。

---

## 关键流程

服务拒绝注册中心发布删除申请，复核业务字段后，先检查指定流的发布权限，再按平台代码与 source_id 检查未确认重复。重复时在落盘前返回 409。附件通过线程池独占创建、复制及 fsync；随后构造消息，在会话锁内再次检查身份及权限并等待 broker 确认。

入库申请和删除申请共用 `app/service/file_message.py` 的附件发布流程，分别保存到 `data/ingestion-request/file` 和 `data/deletion-request/file`。共用[流发布去重服务](stream-publish-deduplication.md)，分别比较 registry 在各自流的确认进度。发布失败处理见 API，当前没有事务补偿。

同一平台复用一个 Producer/Consumer 客户端，按角色显式配置可读写流集合；每条流使用独立 `<platform_code>:<stream>` 消费名称，不跨流共享 offset。

---

## 配置与部署

`RABBITMQ_DELETION_REQUEST_STREAM` 默认 `deletion-requests`，`RABBITMQ_DELETION_RESULT_STREAM` 默认 `deletion-results`，四个业务流名称不得重复。新增流随启动幂等初始化，已有流不重建。

Dockerfile 准备非 root 用户可写的删除附件目录，`.dockerignore` 排除其中内容。部署项目使用独立 `deletion-files` 卷挂载 `/workspace/data/deletion-request/file`，不与入库卷混用；不要删除含有待审附件的卷。

---

## 当前边界与验证

删除申请 publish/pull/ack 均已实现，registry ack 后清理审核附件，消费细节见[删除申请消费服务](deletion-consumer.md)。[删除审批结果](../../api/deletion-review.md) publish/pull/ack 均已实现，实际删除由子平台执行，中间件仅发布审批结果并记录消费确认。

单元测试验证附件先落盘再发送、角色和参数拒绝、身份失效后的文件回收、保存失败不发送、发布超时保留文件、重复拒绝及 HTTP 状态。隔离临时流的真实 RabbitMQ 联调验证上传、发布确认、registry 拉取与 ack、未确认重复拒绝、ack 后重提和索引重建；完成后删除临时流与文件。
