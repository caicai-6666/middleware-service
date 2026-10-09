# 项目文档导航

本文是 `description/` 下文档的统一阅读入口，按阅读目的索引现有文档。

## 从哪里开始

| 阅读目的 | 文档入口 | 内容说明 |
| --- | --- | --- |
| 了解项目或开始开发 | [项目目标说明](project.md) | 项目定位、建设目标、典型流程与服务边界；开发前必读。 |
| 新增或修改文档 | [文档撰写风格手册](documentation.md) | 文档命名、目录分层、内容组织、排版及链接维护规范。 |

---

## 专题文档

| 文档入口 | 内容说明 |
| --- | --- |
| [FastAPI 应用基础结构](capability/application/fastapi.md) | 应用入口、路由组织、本地启动和接口文档地址。 |
| [平台身份配置](capability/application/platform-config.md) | 平台结构体、YAML 示例、默认路径、只读挂载与启动校验。 |
| [平台会话服务](capability/application/platform-session.md) | 登录缓存、心跳续期、过期清理与本项目环境配置。 |
| [入库请求消费服务](capability/application/ingestion-consumer.md) | 注册中心逐条拉取、确认及 offset 恢复。 |
| [入库请求消息契约](architecture/data/ingestion-request.md) | 文件转存、消息结构及发布失败边界。 |
| [API 导航](api/readme.md) | 已注册 HTTP 接口的统一入口。 |
| [审核反馈消息契约](architecture/data/review-result.md) | 反馈字段、原请求关联及消费边界。 |
| [审核反馈消费服务](capability/application/review-consumer.md) | 按平台过滤、独立进度与有界缓存。 |
| [审核反馈接口](api/review.md) | 注册中心发布、子平台过滤拉取与确认接口。 |
| [Stream 异步客户端工厂](capability/infrastructure/stream-client-factory.md) | Producer、Consumer 创建与异步连接生命周期。 |
| [业务流启动初始化](capability/infrastructure/stream-initialization.md) | 两个业务流的幂等创建、连接配置和启动失败边界。 |
| [RabbitMQ Streams 本地开发环境](capability/infrastructure/rabbitmq-stream.md) | Docker 部署、持久化卷、异步客户端依赖与连接验证。 |
| [应用容器镜像](capability/application/container-image.md) | Dockerfile 构建方式、当前入口边界及独立部署项目协作约定。 |

项目目标说明中的能力属于建设目标，具体实现状态应以相应专题文档和代码为准。

---

## 导航维护

新增、迁移、重命名或删除文档时，同步更新本页入口，并检查相对链接是否有效。导航仅索引已存在的文档；详细规则与设计说明保留在各自的主文档中。
