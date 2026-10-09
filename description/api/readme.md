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
| [提交审核反馈](review.md#提交审核反馈) | `POST` | `/middleware-service/api/review-results/publish` |
| [拉取审核反馈](review.md#拉取审核反馈) | `POST` | `/middleware-service/api/review-results/pull` |
| [确认审核反馈](review.md#确认审核反馈) | `POST` | `/middleware-service/api/review-results/ack` |

入库请求提交、拉取与确认已实现，文件下载尚未实现。审核反馈发布、按子平台过滤的拉取及确认均已实现。启动方式见 [FastAPI 应用基础结构](../capability/application/fastapi.md)。
