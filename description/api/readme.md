# API 导航

本文汇总已注册并文档化的 HTTP 接口。自动生成的 Schema 位于运行服务的 `/openapi.json`。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [应用存活检查](health.md#应用存活检查) | `GET` | `/middleware-service/api/health` |
| [平台登录](platform.md#平台登录) | `POST` | `/middleware-service/api/platform/login` |
| [平台心跳](platform.md#平台心跳) | `POST` | `/middleware-service/api/platform/heartbeat` |
| [只读校验平台令牌](platform.md#只读校验平台令牌) | `POST` | `/middleware-service/api/platform/token/verify` |
| [查询所有平台](platform.md#查询所有平台) | `GET` | `/middleware-service/api/platform/list` |
| [全量更新平台管理员](platform.md#全量更新平台管理员) | `POST` | `/middleware-service/api/platform/admins/full-table` |
| [新增平台管理员](platform.md#新增平台管理员) | `POST` | `/middleware-service/api/platform/admins/add` |
| [删除平台管理员](platform.md#删除平台管理员) | `POST` | `/middleware-service/api/platform/admins/remove` |
| [查询当前平台管理员](platform.md#查询当前平台管理员) | `GET` | `/middleware-service/api/platform/admins/list` |
| [重建用户信息总表](user.md#重建用户信息总表) | `POST` | `/middleware-service/api/user/full-table` |
| [更新单个用户信息](user.md#更新单个用户信息) | `POST` | `/middleware-service/api/user/update` |
| [写入用户登录缓存](user.md#写入用户登录缓存) | `PUT` | `/middleware-service/api/user/login-cache` |
| [查询并续期用户登录缓存](user.md#查询并续期用户登录缓存) | `POST` | `/middleware-service/api/user/login-cache` |
| [查询用户在本平台的文件权限](user.md#查询用户在本平台的文件权限) | `POST` | `/middleware-service/api/user/file-permission` |
| [发布助理任务](assistant-task.md#发布助理任务) | `POST` | `/middleware-service/api/assistant/tasks/publish` |
| [领取助理任务](assistant-task.md#领取助理任务) | `POST` | `/middleware-service/api/assistant/tasks/pull` |
| [确认助理任务](assistant-task.md#确认助理任务) | `POST` | `/middleware-service/api/assistant/tasks/ack` |
| [查询助理任务状态](assistant-task.md#查询助理任务状态) | `GET` | `/middleware-service/api/assistant/tasks/status` |
| [发布平台 A2A 请求](platform-a2a.md#发布请求) | `POST` | `/middleware-service/api/platform-a2a/requests/publish` |
| [领取平台 A2A 请求](platform-a2a.md#领取请求) | `POST` | `/middleware-service/api/platform-a2a/requests/pull` |
| [发布平台 A2A 回复](platform-a2a.md#发布回复) | `POST` | `/middleware-service/api/platform-a2a/replies/publish` |
| [读取平台 A2A 回复包](platform-a2a.md#读取回复包) | `POST` | `/middleware-service/api/platform-a2a/replies/pull` |
| [确认平台 A2A 回复包](platform-a2a.md#确认回复包) | `POST` | `/middleware-service/api/platform-a2a/replies/ack` |
| [领取平台 A2A 单聊反馈](platform-a2a.md#领取单聊反馈) | `POST` | `/middleware-service/api/platform-a2a/direct-replies/pull` |
| [确认平台 A2A 单聊反馈](platform-a2a.md#确认单聊反馈) | `POST` | `/middleware-service/api/platform-a2a/direct-replies/ack` |
| [提交入库请求](ingestion.md#提交入库请求) | `POST` | `/middleware-service/api/ingestion-requests/publish` |
| [拉取一条入库请求](ingestion.md#拉取一条入库请求) | `POST` | `/middleware-service/api/ingestion-requests/pull` |
| [确认处理完成](ingestion.md#确认处理完成) | `POST` | `/middleware-service/api/ingestion-requests/ack` |
| [提交审核反馈](ingestion-review.md#提交审核反馈) | `POST` | `/middleware-service/api/ingestion-results/publish` |
| [拉取审核反馈](ingestion-review.md#拉取审核反馈) | `POST` | `/middleware-service/api/ingestion-results/pull` |
| [确认审核反馈](ingestion-review.md#确认审核反馈) | `POST` | `/middleware-service/api/ingestion-results/ack` |
| [提交删除申请](deletion.md#提交删除申请) | `POST` | `/middleware-service/api/deletion-requests/publish` |
| [拉取删除申请](deletion.md#拉取删除申请) | `POST` | `/middleware-service/api/deletion-requests/pull` |
| [确认删除申请](deletion.md#确认删除申请) | `POST` | `/middleware-service/api/deletion-requests/ack` |
| [提交删除审批结果](deletion-review.md#提交删除审批结果) | `POST` | `/middleware-service/api/deletion-results/publish` |
| [拉取删除审批结果](deletion-review.md#拉取删除审批结果) | `POST` | `/middleware-service/api/deletion-results/pull` |
| [确认删除审批结果](deletion-review.md#确认删除审批结果) | `POST` | `/middleware-service/api/deletion-results/ack` |
| [发布文件权限调整](file-permission.md#发布文件权限调整) | `POST` | `/middleware-service/api/file-permission-adjustments/publish` |
| [拉取文件权限调整](file-permission.md#拉取文件权限调整) | `POST` | `/middleware-service/api/file-permission-adjustments/pull` |
| [确认文件权限调整](file-permission.md#确认文件权限调整) | `POST` | `/middleware-service/api/file-permission-adjustments/ack` |
| [读取入库审核附件](resource.md#读取入库审核附件) | `GET` | `/middleware-service/api/resource/ingestion-requests/{file_id}` |
| [读取删除审核附件](resource.md#读取删除审核附件) | `GET` | `/middleware-service/api/resource/deletion-requests/{file_id}` |
| [读取助理任务附件](resource.md#读取助理任务附件) | `GET` | `/middleware-service/api/resource/assistant-tasks/{file_id}` |


用户资料支持 employment_status（在职/离职）：全表注册返回逐用户状态，增量更新和登录查询响应携带当前状态；仅记录资料，不改变登录或权限判断，详见[用户 API](user.md)。

平台 A2A 七个接口均已接入 Redis：统一请求发布和领取，回复发布时按原请求是否携带 response_id 分流并自动完成请求；首次结果通过 replies/pull、replies/ack 聚合读取和关闭，定向单聊结果通过 direct-replies/pull、direct-replies/ack 持续领取和逐条确认，顶层窗口独立。无 requests/ack，详见[平台 A2A API](platform-a2a.md)，不影响个人助理任务。

删除申请及删除审批结果 publish/pull/ack 均已实现。结果按认证子平台过滤，确认后推进该平台在对应结果流中的独立进度。

五个流的 publish 均拒绝尚未确认的同业务键重复消息；申请依据 registry 的各自流进度，结果及文件权限调整依据目标子平台在对应流的进度，见[流发布去重服务](../capability/application/stream-publish-deduplication.md)。

入库请求提交、拉取与确认已实现；提交会拒绝同平台、同 source_id 的未确认重复申请，详见[发布接口](ingestion.md#提交入库请求)。registry 文件读取接口已实现。审核反馈发布、按子平台过滤的拉取及确认均已实现。启动方式见 [FastAPI 应用基础结构](../capability/application/fastapi.md)。
