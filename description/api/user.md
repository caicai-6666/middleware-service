# 用户 API

用户总表允许注册中心与 knowledge-accumulation 维护，登录缓存写入仅由 knowledge-accumulation 负责；单用户资料更新仅允许注册中心调用。其他已登录平台可以查询并续期有效的用户登录缓存，或独立按 userid 只读查询用户在调用平台的文件访问等级。基础路径为 `/middleware-service/api/user`，请求和响应均使用 `application/json`。所有接口均需平台 Bearer 令牌。用户登录缓存请求体中的 `access_token` 是用户令牌，与请求头中的平台令牌分别使用；文件权限查询不需要用户令牌。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [重建用户信息总表](#重建用户信息总表) | `POST` | `/middleware-service/api/user/full-table` |
| [更新单个用户信息](#更新单个用户信息) | `POST` | `/middleware-service/api/user/update` |
| [写入用户登录缓存](#写入用户登录缓存) | `PUT` | `/middleware-service/api/user/login-cache` |
| [查询并续期用户登录缓存](#查询并续期用户登录缓存) | `POST` | `/middleware-service/api/user/login-cache` |
| [查询用户在本平台的文件权限](#查询用户在本平台的文件权限) | `POST` | `/middleware-service/api/user/file-permission` |

用户对象、登录缓存及生命周期见[用户 Redis 缓存](../capability/application/user-cache.md)。Redis 不可用时返回 503，不退回本机缓存。所有成功响应携带 `Cache-Control: no-store`；请求禁止未知字段。


---

## 重建用户信息总表

`POST /middleware-service/api/user/full-table`，由注册中心或 knowledge-accumulation 平台发送，立即按整张表重建用户信息、多部门归属、任职状态与平台权限映射。

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
| `users[].departments` | string[] | 否 | 所属部门名称列表，默认 `[]`；允许多部门，名称去除首尾空白后必须非空且不可重复，不接受 null。 |
| `users[].employment_status` | string | 否 | 任职状态，仅接受 `employed`（在职）或 `resigned`（离职），默认 `employed`，不接受 null、布尔值、数字或其他字符串。 |

`departments` 保存部门名称，不解释部门 ID、层级或主部门，顺序不表示权限优先级。例如 `["财务部","采购部"]`。完整总表中省略该字段等同于 `[]`，会清空该用户原有部门；仅希望保留旧部门时应使用增量更新接口。旧 Redis 资料缺少该字段时按 `[]` 读取，无需迁移。部门归属不自动赋予权限或管理员身份。

`employment_status` 仅是资料标识。全表省略该字段按 `employed` 保存，会覆盖原有离职状态；维护方应传入权威状态，若仅改其他字段并保留任职状态，应使用单用户增量更新。旧 Redis 资料缺少该字段时按 `employed` 读取，不自动回写或迁移。状态不影响登录、文件权限或管理员身份。

不能携带 `is_logged_in` 或 `ttl`；同步信息不会创建登录状态。

### 成功响应

状态码 `200`，媒体类型 `application/json`：

```json
{"count":2,"users":[{"userid":"user-001","employment_status":"employed"},{"userid":"user-002","employment_status":"resigned"}]}
```

`count` 表示重建后用户总数；`users` 按请求顺序逐项返回本次成功保存的 `userid` 和 `employment_status`，不回显用户名、部门、权限或令牌。空总表返回 `{"count":0,"users":[]}`。这是本次写入结果，不保证后续并发更新后的状态仍相同。

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
  -d '{"users":[{"userid":"user-001","username":"审核员甲","permissions":{"platform-a":2},"departments":["财务部","采购部"],"employment_status":"employed"},{"userid":"user-002","username":"审核员乙","permissions":{},"employment_status":"resigned"}]}'
```

### 行为与边界

全表校验通过后原子替换，失败不影响旧数据。表外用户资料及登录缓存立即移除；保留用户更新资料、部门、任职状态及权限，但不延长原有登录有效期；新增用户默认为未登录。重复提交同一总表不会为用户续期。本接口不会续期调用平台的会话。

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
| `departments` | string[] | 否 | 已有用户省略则保留，`[]` 清空，提供时整体替换；新增用户省略时为 `[]`。名称约束同总表，不接受 null。 |
| `employment_status` | string | 否 | 仅接受 `employed`（在职）或 `resigned`（离职）；已有用户省略则保留，新增用户省略时为 `employed`，不接受 null 或其他值。 |

`username`、`permissions`、`departments`、`employment_status` 至少提供一项。权限是完整映射替换，不逐键合并，传 `{}` 撤销全部权限。请求不接受登录状态或 TTL。

### 成功响应

状态码 `200`，媒体类型 `application/json`，返回更新后的完整用户资料：

```json
{"userid":"user-001","username":"审核员甲","permissions":{"platform-a":2},"departments":["财务部","采购部"],"employment_status":"employed"}
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

更新已有用户的用户名，保留原权限、部门及任职状态：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/user/update' \
  -H 'Authorization: Bearer replace-with-registry-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"userid":"user-001","username":"审核员甲（更新）"}'
```

单独替换多部门归属可提交 `{"userid":"user-001","departments":["财务部","采购部"]}`，清空可提交 `{"userid":"user-001","departments":[]}`。

仅更新任职状态可提交 `{"userid":"user-001","employment_status":"resigned"}`，恢复在职可改为 `employed`；成功响应返回完整用户资料及更新后的状态。

新增用户须同时提供用户名和权限，部门及任职状态可省略，例如 `{"userid":"user-003","username":"审核员丙","permissions":{"platform-a":1}}`。

### 行为与边界

已有用户仅修改提供的资料字段；不存在时校验完整资料后新增。校验失败时不修改任何数据。资料更新不会创建、撤销或续期用户登录状态，用户成功登录后由 knowledge-accumulation 单独写入登录缓存；本接口也不会续期平台会话。标记为离职不撤销用户 token，不阻止新的成功登录通知，也不修改文件权限或平台管理员；调用方可读取状态后执行自身业务规则。

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
{"userid":"user-001","username":"审核员甲","departments":["财务部","采购部"],"employment_status":"employed","is_logged_in":true,"ttl":90}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `userid` | string 或 null | 有效 token 对应的用户标识；token 无效或过期时为 null。 |
| `username` | string 或 null | 用户资料中的当前用户名；token 无效或过期时为 null。 |
| `departments` | string[] 或 null | 当前部门名称列表；有效登录但没有部门或旧资料无字段时为 `[]`，token 无效或过期时为 null。 |
| `employment_status` | string 或 null | 有效登录时为当前 `employed`（在职）或 `resigned`（离职）；旧资料缺字段为 `employed`，token 无效或过期时为 null。 |
| `is_logged_in` | boolean | 查询时是否存在未过期的登录缓存，由键存在性和 TTL 判断。 |
| `ttl` | integer | 有效时返回续期后的完整有效秒数，默认 90；无效为 0。 |

未知、已替换或过期的 token 都返回 `200`，例如 `{"userid":null,"username":null,"departments":null,"employment_status":null,"is_logged_in":false,"ttl":0}`，不创建资料或登录记录。有效时返回用户资料中的当前用户名、部门和任职状态，不返回跨平台权限。这些资料与登录校验、续期在同一次 Redis 操作中读取，后续资料更新会在下一次查询反映。离职用户仍按原规则返回有效登录并续期，可能同时出现 `employment_status="resigned"` 与 `is_logged_in=true`。

升级后增量资料响应及本查询响应包含 `departments` 和 `employment_status`，全表注册响应新增 `users`；严格拒绝未知响应字段的调用方 DTO 需同步接入。登录写入响应及 file-permission 的最小响应结构保持原契约。

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

---

## 查询用户在本平台的文件权限

`POST /middleware-service/api/user/file-permission`，携带 userid，由中间件按认证平台身份返回该用户在本平台的文件等级，供调用平台服务端执行文件过滤。

### 认证方式

必填 header `Authorization: Bearer <platform_access_token>`，允许任意有效登录平台调用，不要求注册中心或平台管理员身份。平台代码仅从有效令牌解析，不能由请求体指定。不需要用户 access_token，不要求用户存在有效登录缓存，也不创建或续期用户、平台登录。

### 请求参数

无 path、query 参数；必填 JSON body：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `userid` | string | 是 | 待查询用户的全局唯一标识，非空且不全为空白，无默认值。 |

请求只接受 userid。携带 platform_code、level、file_level、access_token、管理员标记或其他额外字段均返回 422。

### 成功响应

状态码 `200`，媒体类型 `application/json`，携带 `Cache-Control: no-store`。响应是仅包含 file_level 的 JSON 对象，不是裸数字；不返回 userid、platform_code、authorized、姓名、用户令牌或完整 permissions 映射。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `file_level` | integer / null | 用户在认证平台的文件等级，严格非负整数。显式 0 有效；用户未登记或没有本平台权限键时为 null，不能将 null 默认成 0。 |

有权限且等级为 0：

```json
{"file_level":0}
```

用户未登记，或已登记但没有本平台授权：

```json
{"file_level":null}
```

### 错误响应

| 状态码 | 响应 | 条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 无有效平台令牌，携带 WWW-Authenticate: Bearer。 |
| `422` | FastAPI 标准 detail 校验列表 | userid 缺失、类型错误、空白或携带额外字段。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 不可用、资料 JSON 异常、索引与正文 userid 不一致，或本平台等级为负数、布尔、字符串、小数、null 等非法值。 |

权限映射中没有本平台键返回 file_level=null；显式存储非法等级不当作缺失授权，而是返回 503。故障响应不包含 file_level，调用方必须拒绝放行，不使用旧缓存或管理员身份绕过。

### 请求示例

替换为实际平台令牌和用户，不传平台代码：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/user/file-permission' \
  -H 'Authorization: Bearer replace-with-platform-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"userid":"user-001"}'
```

### 行为与边界

每次通过现有 Redis 资料 Hash 的单次 HGET 读取当前快照，不经过用户 token 索引、登录状态查询或 EXPIRE，不续期、不复活用户登录。用户登录已过期但资料仍有效时，仍按当前权限映射返回文件等级。

权限降低、撤销、总表移除后，后续查询立即反映新的权威资料；单次响应不是永久授权。调用平台应在敏感读取及续聊恢复时重新核对权限，并在服务端过滤文件；等级不应进入模型提示词、工具参数或反馈。本接口不证明调用者已经完成终端用户身份验证，调用平台必须从可信业务上下文绑定 userid，不能让模型或未认证用户任意指定。

权限维护接口沿用既有严格整数契约，不在本次修改。文件权限查询要求等级非负，历史负数在本接口返回 503，需由资料维护方核对修正。不根据其他平台权限或管理员集合推断本平台授权。任职状态仅作资料标识，本接口不因 `resigned` 自动撤销或降低等级，也不在响应中增加状态字段。无需新增 Redis 键、配置或依赖，具体实现见[用户 Redis 缓存](../capability/application/user-cache.md#本平台文件权限只读查询)。
