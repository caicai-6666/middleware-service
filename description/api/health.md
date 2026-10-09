# 健康检查 API

用于探测 HTTP 应用是否能够响应请求。本地基础地址为 `http://127.0.0.1:8000`。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [应用存活检查](#应用存活检查) | `GET` | `/middleware-service/api/health` |

---

## 应用存活检查

`GET /middleware-service/api/health`：检查应用存活状态。

- **认证方式**：无需认证。
- **请求参数**：无 path、query 或业务 header 参数，无请求体。
- **成功响应**：HTTP `200`，媒体类型 `application/json`。`status` 为字符串，固定为 `ok`。

```json
{"status": "ok"}
```

- **错误响应**：无业务错误码；连接失败或超时表示此次探测未成功，不会返回伪造的成功状态。
- **请求示例**：

```bash
curl --fail http://127.0.0.1:8000/middleware-service/api/health
```

- **行为与边界**：仅检查应用存活，不检查 RabbitMQ、平台认证配置或消息投递能力，不能作为业务就绪判断。
