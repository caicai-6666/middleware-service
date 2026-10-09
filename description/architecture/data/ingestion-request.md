# 入库请求消息契约

消息由 `app/service/ingestion.py` 构建，以 UTF-8 JSON 字节写入 `RABBITMQ_REQUEST_STREAM` 指定的流，默认 `ingestion-requests`。文件本体不写入 Stream。接口见[提交入库请求](../../api/ingestion.md)。

## 字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `message_id` | string | 服务生成的 UUID 十六进制标识。 |
| `platform_code` | string | 从有效 access_token 对应的会话缓存取得，由认证依赖注入，不接受调用方指定。 |
| `name` | string | 入库资料名称，由平台提交。 |
| `abstract` | string | 入库资料摘要，由平台提交。 |
| `note` | string | 平台与注册中心审核员的沟通说明。 |
| `reviewer` | string | 发起平台审核员姓名或代号，由平台提交。 |
| `file.file_id` | string | 文件标识，与 message_id 一致。 |
| `file.file_extension` | string | 显式提交并转为小写的后缀，不带点；磁盘文件名为 `<file_id>.<file_extension>`。 |
| `created_at` | string | 带 UTC 时区的 ISO 8601 创建时间。 |

消费方使用 file_id 引用文件，不将原始文件名当作路径；当前文件通过中间件保存，后续下载机制尚未实现。消息不包含平台密钥或 access_token。

## 存储与失败边界

本地目录固定为项目根目录 `data/tmp-file`，文件保存为 `<message_id>.<file_extension>`，懒创建，文件写入在线程池中执行以避免阻塞事件循环。先独占创建文件、复制并 fsync，再发布消息并等待确认（5 秒）。写入失败清理半成品；发布前身份或权限失效会清理未发布文件；发布异常时保留文件供后续核对。

生产 Compose 使用 `backend-files` 命名卷挂载 `/workspace/data/tmp-file`，Dockerfile 准备非 root 用户可写目录。卷与 RabbitMQ 数据卷均需要保留，不将临时文件打包入镜像或提交 Git。注册中心 ack 后清理对应文件；现阶段未实现定时清理、事务补偿和重试去重。


## 历史消息

当前消息不再包含 schema_version、原始文件名、媒体类型或文件大小。已发布消息与已有文件不修改；后续消费者应容忍历史消息中的额外字段，历史消息缺少 file_extension 时对应无后缀文件。后缀为调用方声明，不是内容格式验证结果。

新请求必填 name 和 abstract，并原样写入消息顶层；历史消息可能缺少这两个字段，已有消息不改写。
