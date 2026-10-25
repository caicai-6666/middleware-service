# 项目文档导航

本文是 `description/` 下文档的统一阅读入口，按阅读目的索引现有文档。

## 从哪里开始

| 阅读目的 | 文档入口 | 内容说明 |
| --- | --- | --- |
| 首次运行或部署项目 | [项目 README](../README.md) | 当前能力、本地启动、消息流转及容器部署入口。 |
| 了解项目或开始开发 | [项目目标说明](project.md) | 项目定位、建设目标、典型流程与服务边界；开发前必读。 |
| 新增或修改文档 | [文档撰写风格手册](documentation.md) | 文档命名、目录分层、内容组织、排版及链接维护规范。 |

---

## 专题文档

| 文档入口 | 内容说明 |
| --- | --- |
| [FastAPI 应用基础结构](capability/application/fastapi.md) | 应用入口、路由组织、本地启动和接口文档地址。 |
| [平台身份配置](capability/application/platform-config.md) | 平台结构体、YAML 的 A2A、子管理平台与管理入口标记、子平台协作职责、默认路径、只读挂载与启动校验。 |
| [平台会话服务](capability/application/platform-session.md) | 登录缓存、心跳续期、过期清理与本项目环境配置。 |
| [用户 Redis 缓存](capability/application/user-cache.md) | 用户总表、资料增量更新、登录成功写入和 TTL 滑动续期。 |
| [平台管理员缓存](capability/application/platform-admin-cache.md) | 平台多管理员 Redis Set、registry 与知识沉淀平台全量、registry 增删、当前平台自查及成员判断。 |
| [用户接口](api/user.md) | 用户总表与增量资料、用户 token 登录写入及查询续期接口。 |
| [助理任务接口](api/assistant-task.md) | 每用户优先级任务发布、多附件与消息归属、用户消息合并与分隔线排版、领取确认及状态查询。 |
| [助理任务队列](capability/application/assistant-task.md) | 单队列设计原则、平台交付结果后确认、Redis 排序与正文、执行租约及幂等。 |
| [入库请求消费服务](capability/application/ingestion-consumer.md) | 注册中心逐条拉取、确认及 offset 恢复。 |
| [入库发布去重服务](capability/application/ingestion-deduplication.md) | 平台代码与 source_id 去重、确认进度判断、并发保护及索引恢复。 |
| [流发布去重服务](capability/application/stream-publish-deduplication.md) | 五个流共用的去重机制，按实际接收方 offset 判断。 |
| [入库请求消息契约](architecture/data/ingestion-request.md) | 文件转存、消息结构及发布失败边界。 |
| [附件读取接口](api/resource.md) | registry 下载审核附件，已认证平台下载助理任务附件，权限及文件生命周期。 |
| [API 导航](api/readme.md) | 已注册 HTTP 接口的统一入口。 |
| [通行证集合契约](architecture/data/passport.md) | 多通行证、单字符串兼容、表单提交及历史消费规范化。 |
| [审核反馈消息契约](architecture/data/review-result.md) | 反馈字段、文件级别、原请求关联及消费边界。 |
| [审核结果消费服务](capability/application/review-consumer.md) | 入库及删除结果共用的按平台过滤、独立进度与有界缓存。 |
| [删除申请消息契约](architecture/data/deletion-request.md) | 无附件申请字段、人员信息与历史消息兼容。 |
| [删除申请发布服务](capability/application/deletion-publish.md) | 无附件发布、确认、权限及部署。 |
| [删除申请消费服务](capability/application/deletion-consumer.md) | registry 逐条交付、独立进度及历史附件兼容清理。 |
| [删除申请接口](api/deletion.md) | 子平台发布、registry 拉取和确认删除申请。 |
| [文件权限调整消息契约](architecture/data/file-permission-adjustment.md) | registry 发布文件新等级，子平台修改后确认，独立进度与按文件去重。 |
| [文件权限调整接口](api/file-permission.md) | registry 发布、目标子平台拉取及确认接口。 |
| [删除审批结果消息契约](architecture/data/deletion-result.md) | 批准/拒绝字段、请求关联及生命周期。 |
| [删除审批结果接口](api/deletion-review.md) | registry 发布批准/拒绝结果，子平台过滤拉取及确认。 |
| [审核反馈接口](api/ingestion-review.md) | 注册中心发布、子平台过滤拉取与确认接口。 |
| [Stream 异步客户端工厂](capability/infrastructure/stream-client-factory.md) | Producer、Consumer 创建与异步连接生命周期。 |
| [业务流启动初始化](capability/infrastructure/stream-initialization.md) | RabbitMQ 容器幂等创建五个流，后端只读校验，及对应启动失败边界。 |
| [RabbitMQ Streams 本地开发环境](capability/infrastructure/rabbitmq-stream.md) | Docker 部署、持久化卷、异步客户端依赖与连接验证。 |
| [Redis 容器部署](capability/infrastructure/redis.md) | 同步部署、密码注入、内部网络、健康检查与 AOF 持久化。 |
| [应用容器镜像](capability/application/container-image.md) | Dockerfile 构建方式、当前入口边界及独立部署项目协作约定。 |

项目目标说明中的能力属于建设目标，具体实现状态应以相应专题文档和代码为准。

---

## 导航维护

新增、迁移、重命名或删除文档时，同步更新本页入口，并检查相对链接是否有效。导航仅索引已存在的文档；详细规则与设计说明保留在各自的主文档中。
