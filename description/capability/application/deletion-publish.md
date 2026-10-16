# 删除申请发布服务

## 用途与依赖

`DeletionService` 在 `app/service/deletion.py` 中校验子平台删除申请字段，直接发布 UTF-8 JSON 并等待 RabbitMQ 确认。在 `app/main.py` 中装配到 `app.state.deletion`，复用会话管理器和长期持有的 Producer，不依赖附件保存服务或磁盘目录。

调用方式见[删除申请 API](../../api/deletion.md)，字段定义见[删除申请消息契约](../../architecture/data/deletion-request.md)。

---

## 关键流程

服务拒绝 registry 发布，检查业务字段约束，并要求 passport 至少包含一个非空白字符串。单字符串兼容输入、数组写入规则共用 app/service/passport.py，详见[通行证集合契约](../../architecture/data/passport.md)。通过 token 会话检查发布权限后，按 `(platform_code, source_id)` 检查重复，再生成 message_id、注入平台代码与 UTC created_at。消息不包含 file 引用，不保存临时文件。

去重依据 registry 在删除申请流中的确认 offset，与入库流隔离。相同键的未确认申请返回 409，pull 不解除限制，ack 后允许重提。共用算法见[流发布去重服务](stream-publish-deduplication.md)。会话锁内再次检查身份及权限，等待 broker 确认后才返回成功。

身份、权限及资源失效不登记不确定占位。发布超时、异常或取消可能发生在发送之后，保留消息 ID 和业务键不确定占位；后续扫描找到消息则按真实 offset 判断，始终找不到则返回 503，不自动重发。HTTP 发布不确定响应带 message_id，当前没有投递结果查询或跨系统事务。

---

## 部署与历史兼容

四个流名称由现有配置注入，RabbitMQ 容器负责初始化，后端只检查存在性，无新增环境变量。新删除请求不需要临时文件存储。

为处理升级前已入流的附件消息，继续保留 `data/deletion-request/file` 目录、Dockerfile 可写目录和 Compose 的 deletion-files 卷；它们仅用于历史下载与 ack 清理，不用于新申请发布。不自动删除旧附件或历史卷。消费说明见[删除申请消费服务](deletion-consumer.md)。

---

## 验证

测试覆盖无需文件即可通过 HTTP 提交、消息无附件字段、发布期间不访问磁盘、表单参数及旧文件字段拒绝、权限、空备注、发布超时和取消的占位、去重及确认后重提。消费测试同时覆盖新消息仅确认进度与旧附件清理兼容。真实联调使用独立临时流，不写入正式业务流。
