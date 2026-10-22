# 用户 API

用户总表允许注册中心与 knowledge-accumulation 维护，登录缓存写入仅由 knowledge-accumulation 负责；单用户资料更新仅允许注册中心调用。其他已登录平台可以查询并续期有效的用户登录缓存。基础路径为 `/middleware-service/api/user`，请求和响应均使用 `application/json`。所有接口均需平台 Bearer 令牌。用户登录缓存请求体中的 `access_token` 是用户令牌，与请求头中的平台令牌分别使用。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [重建用户信息总表](#重建用户信息总表) | `POST` | `/middleware-service/api/user/full-table` |
| [更新单个用户信息](#更新单个用户信息) | `POST` | `/middleware-service/api/user/update` |
| [写入用户登录缓存](#写入用户登录缓存) | `PUT` | `/middleware-service/api/user/login-cache` |
| [查询并续期用户登录缓存](#查询并续期用户登录缓存) | `POST` | `/middleware-service/api/user/login-cache` |

用户对象、登录缓存及生命周期见[用户 Redis 缓存](../capability/application/user-cache.md)。Redis 不可用时返回 503，不退回本机缓存。所有成功响应携带 `Cache-Control: no-store`；请求禁止未知字段。


---

## 重建用户信息总表

`POST /middleware-service/api/user/full-table`，由注册中心或 knowledge-accumulation 平台发送，立即按整张表重建用户信息与平台权限映射。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，必须属于 `knowledge-accumulation` 或 `RABBITMQ_REGISTRY_PLATFORM_CODE` 指定的注册中心（默认 registry）。其他已登录平台返回 403，不能修改总表。调用方身份只从令牌解析，不能通过请求字段指定。

### 请求参数

无 path、query 参数，必填 JSON body：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `users` | array | 是 | 完整用户总表，无默认值；空数组清空总表及全部用户登录缓存。 |
| `users[].userid` | string | 是 | 用户唯一标识，非空且不全为空白，同一总表不能重复。 |
| `users[].username` | string | 是 | 用户名，非空且不全为空白。 |
| `users[].permissions` | object | 是 | 键为 YAML 中配置的 platform_code，值为严格整数；允许空对象，拒绝布尔、字符串及小数值。 |

不能携带 `is_logged_in` 或 `ttl`；同步信息不会创建登录状态。

### 成功响应

状态码 `200`，媒体类型 `application/json`：

```json
{"count":2}
```

`count` 表示重建后用户总数。

### 错误响应

| 状态码 | 响应 | 条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 无有效平台令牌，响应携带 `WWW-Authenticate: Bearer`。 |
| `403` | `{"detail":"仅注册中心或 knowledge-accumulation 平台可以更新用户总表"}` | 调用方为其他平台。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 故障、写入结果不确定或并发重试耗尽。 |
| `422` | FastAPI 校验错误列表或业务 `detail` 字符串 | 字段缺失、类型非法、额外字段、重复 userid 或未配置权限平台。 |

### 请求示例

将令牌和平台代码替换为实际值：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/user/full-table' \
  -H 'Authorization: Bearer replace-with-knowledge-accumulation-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"users":[{"userid":"user-001","username":"审核员甲","permissions":{"platform-a":2}},{"userid":"user-002","username":"审核员乙","permissions":{}}]}'
```

### 行为与边界

全表校验通过后原子替换，失败不影响旧数据。表外用户资料及登录缓存立即移除；保留用户更新资料及权限，但不延长原有登录有效期；新增用户默认为未登录。重复提交同一总表不会为用户续期。本接口不会续期调用平台的会话。

---

## 更新单个用户信息

`POST /middleware-service/api/user/update`，更新指定用户的部分资料，用户不存在时新增。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，仅注册中心可以调用，其他已登录平台返回 `403`。

### 请求参数

无 path、query 参数，必填 JSON body：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `userid` | string | 是 | 非空且不全为空白，定位用户。 |
| `username` | string | 条件必填 | 已有用户可省略并保留原值；新增用户必须提供，不能为 null、空字符串或纯空白。 |
| `permissions` | object | 条件必填 | 已有用户可省略并保留原值；新增用户必须提供。键为已配置 platform_code，值为严格整数，不能为 null。 |

`username` 和 `permissions` 至少提供一项。权限是完整映射替换，不逐键合并，传 `{}` 撤销全部权限。请求不接受登录状态或 TTL。

### 成功响应

状态码 `200`，媒体类型 `application/json`，返回更新后的完整用户资料：

```json
{"userid":"user-001","username":"审核员甲","permissions":{"platform-a":2}}
```

字段定义与总表用户项一致，不返回或修改登录状态。

### 错误响应

| 状态码 | 响应 | 条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 平台令牌缺失、无效或过期，携带 `WWW-Authenticate: Bearer`。 |
| `403` | `{"detail":"仅注册中心可以执行此操作"}` | 其他平台调用。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 故障、写入结果不确定或并发重试耗尽。 |
| `422` | FastAPI 校验错误列表或业务 `detail` 字符串 | 无实际更新字段、显式 null、类型或权限平台非法，或新增用户缺少完整资料。 |

### 请求示例

更新已有用户的用户名，保留原权限：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/user/update' \
  -H 'Authorization: Bearer replace-with-registry-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"userid":"user-001","username":"审核员甲（更新）"}'
```

新增用户须同时提供用户名和权限，例如 `{"userid":"user-003","username":"审核员丙","permissions":{"platform-a":1}}`。

### 行为与边界

已有用户仅修改提供的资料字段；不存在时校验完整资料后新增。校验失败时不修改任何数据。资料更新不会创建、撤销或续期用户登录状态，用户成功登录后由 knowledge-accumulation 单独写入登录缓存；本接口也不会续期平台会话。

---

## 写入用户登录缓存

`PUT /middleware-service/api/user/login-cache`，knowledge-accumulation 平台在实际用户身份验证成功后调用，创建或重设该用户的登录缓存及 TTL。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，仅 knowledge-accumulation 平台可以调用；其他平台只能使用同路径的 POST 查询续期。实际用户身份验证由 knowledge-accumulation 平台完成，本接口接收可信的成功登录通知。

### 请求参数

无 path、query 参数，必填 JSON body，禁止额外字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `userid` | string | 是 | 已同步资料的用户标识，非空且不全为空白，无默认值。 |
| `access_token` | string | 是 | 该用户成功登录后获得的令牌，非空且不全为空白，无默认值；不是平台 Bearer 令牌。 |

不接受 is_logged_in、ttl、用户名或权限。用户资料须先通过总表或单用户更新接口同步。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`：

```json
{"userid":"user-001","is_logged_in":true,"ttl":90}
```

is_logged_in 是服务端根据本次成功写入计算的响应值，不是需同步的布尔状态。ttl 为配置的完整有效秒数，默认 90。

### 错误响应

| 状态码 | 响应 | 条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 无有效平台令牌，携带 WWW-Authenticate: Bearer。 |
| `403` | `{"detail":"仅 knowledge-accumulation 平台可以执行此操作"}` | 非 knowledge-accumulation 平台调用写入。 |
| `404` | `{"detail":"用户不存在，请先同步用户信息"}` | 用户资料尚未同步或已被移除，不创建登录键。 |
| `409` | `{"detail":"用户访问令牌已绑定其他用户"}` | 同一有效用户令牌已绑定其他 userid，拒绝写入且不修改原登录。 |
| `422` | FastAPI 校验错误列表 | 用户标识或令牌非法、缺少必填字段、包含额外字段。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 故障或写入结果不确定。 |

### 请求示例

```bash
curl -X PUT 'http://127.0.0.1:22000/middleware-service/api/user/login-cache' \
  -H 'Authorization: Bearer replace-with-knowledge-accumulation-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"userid":"user-001","access_token":"replace-with-user-access-token"}'
```

### 行为与边界

在 Redis 脚本中原子检查资料存在，保存 userid 到用户 token 的登录记录及 token 到 userid 的查询索引，两者使用相同 TTL。同一用户仅保留一个当前 token，重新登录时立即移除旧 token 索引。重复提交同一用户及 token 会重设完整 TTL。过期后只能由新的成功登录事件重新写入，普通查询不能重建；登录缓存过期即视为退出，不需要持续同步 false 或发用户心跳。

本接口不更新用户资料、整数权限、平台管理员或调用平台会话。与总表删除并发时不会留下被删除用户的有效登录；应用关闭不会主动清空 Redis 登录键。

---

## 查询并续期用户登录缓存

`POST /middleware-service/api/user/login-cache`，查询用户是否登录有效，若有效则滑动续期。由于该操作会修改有效期，使用 POST。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，registry 和其他已登录平台均可调用。不接受请求体指定调用平台身份；请求体 `access_token` 用于识别用户，不参与平台认证。

### 请求参数

无 path、query 参数，必填 JSON body：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `access_token` | string | 是 | 要查询的用户访问令牌，非空且不全为空白，无默认值。请求不再接受 userid。 |

### 成功响应

状态码 `200`，媒体类型 `application/json`：

```json
{"userid":"user-001","username":"审核员甲","is_logged_in":true,"ttl":90}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `userid` | string 或 null | 有效 token 对应的用户标识；token 无效或过期时为 null。 |
| `username` | string 或 null | 用户资料中的当前用户名；token 无效或过期时为 null。 |
| `is_logged_in` | boolean | 查询时是否存在未过期的登录缓存，由键存在性和 TTL 判断。 |
| `ttl` | integer | 有效时返回续期后的完整有效秒数，默认 90；无效为 0。 |

未知、已替换或过期的 token 都返回 `200`，例如 `{"userid":null,"username":null,"is_logged_in":false,"ttl":0}`，不创建资料或登录记录。有效时返回用户资料中的当前用户名，不返回跨平台权限。用户名与登录校验、续期在同一次 Redis 操作中读取。

### 错误响应

| 状态码 | 响应 | 条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 无有效平台令牌，携带 `WWW-Authenticate: Bearer`；不续期用户。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 不可用，不能据此判断用户已退出。 |
| `422` | FastAPI 校验错误列表 | 用户令牌缺失、类型非法、空白或请求包含额外字段（包括 userid）。 |

### 请求示例

任一已登录平台可调用：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/user/login-cache' \
  -H 'Authorization: Bearer replace-with-platform-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"access_token":"replace-with-user-access-token"}'
```

### 行为与边界

按用户 token 索引解析 userid，原子校验用户资料、双向映射及两份 TTL；有效时同时重新设置登录记录与 token 索引的 USER_CACHE_TTL_SECONDS 有效秒数，不累加旧剩余时间；每次有效查询都会续期。到达截止时刻立即无效，由 Redis 过期判断；查询不会复活过期用户，也不会给已退出用户续期。

用户持续被查询时可以持续存活，直到总表移除该用户或停止续期后登录键过期；后端重启不主动清空 Redis。用户缓存续期不延长调用平台本身的登录会话，平台仍需独立发送心跳。
