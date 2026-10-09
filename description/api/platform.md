# 平台认证 API

平台使用启动时加载的 YAML 白名单密钥登录，获取内存会话令牌。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [平台登录](#平台登录) | `POST` | `/middleware-service/api/platform/login` |
| [平台心跳](#平台心跳) | `POST` | `/middleware-service/api/platform/heartbeat` |
| [查询所有平台](#查询所有平台) | `GET` | `/middleware-service/api/platform/list` |

## 平台登录

`POST /middleware-service/api/platform/login`，验证平台身份并创建唯一活跃会话。

### 认证方式

通过请求体 `platform_code` 与 `secret` 联合认证，无需预先携带令牌。实际部署通过 HTTPS 传输凭据。

### 请求参数

媒体类型为 `application/json`。无 path、query 参数，无业务自定义 header。

| body 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `platform_code` | string | 是 | 平台代号，与 YAML 的 `code` 一致，格式为 `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`；无默认值。 |
| `secret` | string | 是 | 非空，必须与指定平台的密钥完全一致；无默认值。 |

禁止额外字段。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`。

```json
{
  "access_token": "服务生成的随机令牌",
  "token_type": "bearer",
  "code": "registry",
  "expires_in_seconds": 90,
  "heartbeat_interval_seconds": 30
}
```

`code` 是密钥对应的平台代号；`expires_in_seconds` 是从服务端创建会话起计算的有效秒数，由运行配置决定；`token_type` 固定为 `bearer`。`heartbeat_interval_seconds` 是建议发送心跳的间隔秒数，默认 30 秒，必须小于会话有效期。令牌仅在登录成功时返回。

### 错误响应

| 状态码 | 响应 | 触发条件 |
| --- | --- | --- |
| `401` | `{"detail":"平台代号或密钥无效"}` | 平台不存在或密钥与指定平台不匹配。 |
| `503` | `{"detail":"消息服务暂不可用，请稍后重试"}` | 平台消息客户端建立失败或服务正在关闭；登录不生效，可稍后重试。 |
| `409` | `{"detail":"平台已登录"}` | 对应平台仍有有效会话；保留原令牌和过期时间。 |
| `422` | FastAPI 标准 `detail` 校验错误列表 | 请求体格式、类型、必填项或额外字段不符合要求。 |

### 请求示例

将示例密钥替换为本地 YAML 中的密钥：

```bash
curl -X POST 'http://127.0.0.1:8000/middleware-service/api/platform/login' \
  -H 'Content-Type: application/json' \
  -d '{"platform_code":"registry","secret":"replace-with-platform-secret"}'
```

### 行为与边界

登录成功前会建立本平台的 Producer 和 Consumer，最长等待约 30 秒；心跳复用已有客户端，失活后回收资源。同一平台仅允许一个活跃会话；并发登录也只允许一个请求成功。到期即失效，重新登录签发新令牌。服务重启后会话全部失效；平台丢失令牌或登录响应时，需要等旧会话过期再登录。

登录成功后按建议间隔调用[平台心跳](#平台心跳)续期。当前尚未实现登出和消息接口。内存缓存和配置说明见[平台会话服务](../capability/application/platform-session.md)。


---

## 平台心跳

`POST /middleware-service/api/platform/heartbeat`，延长当前活跃会话的有效期。

### 认证方式

使用登录接口签发的令牌，通过 `Authorization: Bearer <access_token>` 请求头认证。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| header | `Authorization` | string | 是 | `Bearer` 加空格及访问令牌，无默认值。 |


无 path、query 参数，无需请求体。平台身份由统一认证依赖从 access_token 对应的会话缓存解析。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`。

```json
{
  "code": "registry",
  "expires_in_seconds": 90,
  "heartbeat_interval_seconds": 30
}
```

`code` 表示令牌所属平台；`expires_in_seconds` 为本次心跳成功起重新计算的有效秒数；`heartbeat_interval_seconds` 为建议下次发送心跳的间隔秒数。两个时间值读取现有会话配置。

### 错误响应

令牌缺失、认证头格式错误、令牌未知、已过期均返回 `401`，响应头 `WWW-Authenticate: Bearer`，媒体类型 `application/json`：

```json
{"detail":"令牌缺失、无效、已过期或与平台不匹配"}
```



### 请求示例

将 `replace-with-access-token` 替换为登录响应中的令牌：

```bash
curl -X POST 'http://127.0.0.1:8000/middleware-service/api/platform/heartbeat' \
  -H 'Authorization: Bearer replace-with-access-token'
```

### 行为与边界

成功心跳将缓存失效时刻设置为“服务端当前单调时间 + 会话有效期”，不会在旧失效时间上累加。令牌保持不变，无需重新保存令牌。平台代号只从令牌缓存取得，不接受请求体指定身份。

到达失效时刻即拒绝续期，即使后台清理尚未运行；收到 `401` 后应使用密钥重新登录。同一平台的心跳、登录和缓存清理共用一把锁，避免失效会话被重新激活。普通令牌查询不会续期。


---

## 查询所有平台

`GET /middleware-service/api/platform/list`，返回启动时配置的全部平台基本信息及当前会话存活状态。

### 认证方式

必须携带 `Authorization: Bearer <access_token>`，由统一认证依赖验证。任何活跃平台均可查询全部平台基本信息，不返回密钥或访问令牌。

### 请求参数

无 path、query 参数，无请求体；必填 Authorization 认证头。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`。按 YAML 配置顺序返回数组：

```json
[
  {
    "code": "registry",
    "name": "注册中心",
    "description": "审核入库请求",
    "is_alive": true
  }
]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | string | 平台唯一代号。 |
| `name` | string | 平台中文名称。 |
| `description` | string | 平台用途描述。 |
| `is_alive` | boolean | 有未过期的登录会话时为 true，否则为 false。 |

### 错误响应

令牌缺失、无效或已过期返回 `401`，响应 `{"detail":"令牌缺失、无效或已过期"}`，并携带 `WWW-Authenticate: Bearer`。

### 请求示例

```bash
curl 'http://127.0.0.1:22000/middleware-service/api/platform/list' \
  -H 'Authorization: Bearer replace-with-access-token'
```

### 行为与边界

读取启动时加载的内存配置，不重新读取 YAML。尚未登录、会话过期或正在关闭的服务均显示平台失活。状态按同一个时间点计算，过期但尚未被后台清理的记录也返回 false。

查询不续期、不创建或关闭 RabbitMQ 客户端，不探测子平台网络或业务健康情况；`is_alive` 仅表示本中间件中的登录会话状态。
