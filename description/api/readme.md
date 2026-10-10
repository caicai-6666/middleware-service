# API 导航

本文汇总已注册并文档化的 HTTP 接口。自动生成的 Schema 位于运行服务的 `/openapi.json`。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [应用存活检查](health.md#应用存活检查) | `GET` | `/middleware-service/api/health` |
| [平台登录](platform.md#平台登录) | `POST` | `/middleware-service/api/platform/login` |
| [平台心跳](platform.md#平台心跳) | `POST` | `/middleware-service/api/platform/heartbeat` |
| [查询所有平台](platform.md#查询所有平台) | `GET` | `/middleware-service/api/platform/list` |
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
| [读取入库审核附件](resource.md#读取入库审核附件) | `GET` | `/middleware-service/api/resource/ingestion-requests/{file_id}` |
| [读取删除审核附件](resource.md#读取删除审核附件) | `GET` | `/middleware-service/api/resource/deletion-requests/{file_id}` |


删除申请及删除审批结果 publish/pull/ack 均已实现。结果按认证子平台过滤，确认后推进该平台在对应结果流中的独立进度。

四个流的 publish 均拒绝尚未确认的同业务键重复消息；申请依据 registry 的各自流进度，结果依据目标子平台在对应流的进度，见[流发布去重服务](../capability/application/stream-publish-deduplication.md)。

入库请求提交、拉取与确认已实现；提交会拒绝同平台、同 source_id 的未确认重复申请，详见[发布接口](ingestion.md#提交入库请求)。registry 文件读取接口已实现。审核反馈发布、按子平台过滤的拉取及确认均已实现。启动方式见 [FastAPI 应用基础结构](../capability/application/fastapi.md)。
