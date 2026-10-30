# 平台 API

平台使用启动时加载的 YAML 白名单密钥登录，获取内存会话令牌；注册中心可只读验证其他平台令牌并取得可信代码。knowledge-accumulation 与注册中心可全量更新各平台的多管理员集合，注册中心可增删单个管理员。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [平台登录](#平台登录) | `POST` | `/middleware-service/api/platform/login` |
| [平台心跳](#平台心跳) | `POST` | `/middleware-service/api/platform/heartbeat` |
| [只读校验平台令牌](#只读校验平台令牌) | `POST` | `/middleware-service/api/platform/token/verify` |
| [查询所有平台](#查询所有平台) | `GET` | `/middleware-service/api/platform/list` |
| [全量更新平台管理员](#全量更新平台管理员) | `POST` | `/middleware-service/api/platform/admins/full-table` |
| [新增平台管理员](#新增平台管理员) | `POST` | `/middleware-service/api/platform/admins/add` |
| [删除平台管理员](#删除平台管理员) | `POST` | `/middleware-service/api/platform/admins/remove` |
| [查询当前平台管理员](#查询当前平台管理员) | `GET` | `/middleware-service/api/platform/admins/list` |

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

登录成功后按建议间隔调用[平台心跳](#平台心跳)续期。当前尚未实现平台登出接口。内存缓存和配置说明见[平台会话服务](../capability/application/platform-session.md)。


---

## 平台心跳

`POST /middleware-service/api/platform/heartbeat`，仅延长当前调用平台的活跃会话，不同步用户资料、不创建或续期用户登录缓存，也不访问 Redis。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，使用平台登录接口签发的令牌。平台身份完全由统一认证依赖从令牌缓存解析。

### 请求参数

无 path、query 参数，无需请求体。为兼容旧客户端，可接收 `application/json` 对象中的可选 `platform_code`（string 或 null）；该字段被忽略，不参与身份判定。其他字段一律拒绝。

旧的 `users` 字段已移除，即使为 null 或空数组也返回 422。用户资料通过[用户 API](user.md)维护，成功登录由 knowledge-accumulation 调用[写入用户登录缓存](user.md#写入用户登录缓存)。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`：

```json
{"code":"registry","expires_in_seconds":90,"heartbeat_interval_seconds":30}
```

code 为令牌所属平台；expires_in_seconds 为从此次续期起的有效秒数，heartbeat_interval_seconds 为建议心跳间隔，两者读取平台会话配置，与用户登录 TTL 无关。

### 错误响应

| 状态码 | 响应 | 条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 统一依赖拒绝令牌，携带 WWW-Authenticate: Bearer。 |
| `401` | `{"detail":"令牌缺失、无效、已过期或与平台不匹配"}` | 会话服务复核时发现令牌已失效，携带 WWW-Authenticate: Bearer。 |
| `422` | FastAPI 校验错误列表 | 非法 JSON、字段类型或额外字段，包括旧 users 字段。 |

### 请求示例

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform/heartbeat' \
  -H 'Authorization: Bearer replace-with-access-token'
```

### 行为与边界

成功后将平台会话截止时刻设为当前单调时间加有效期，不在旧时间上累加，不轮换令牌。到期立即拒绝，即使后台尚未清理。无效请求不延长平台有效期，收到 401 应重新登录。

平台心跳与用户登录生命周期独立。Redis 运行期间发生故障时，此接口仍只访问平台内存会话；这不改变应用启动时对 Redis 的连接校验。

---

## 只读校验平台令牌

`POST /middleware-service/api/platform/token/verify`，由注册中心验证跨服务请求携带的平台令牌，获得当前可信平台代码。与平台心跳不同，本接口只读，不续期被验证令牌，也不清理其会话和 Stream 资源。

### 认证方式

header 必填 `Authorization: Bearer <registry_platform_access_token>`，表示注册中心自己的中间件平台会话；只有 `RABBITMQ_REGISTRY_PLATFORM_CODE` 配置的平台（默认 registry）可以调用，其他有效平台返回 403。

待验证的是 JSON body 中另一个 access_token，例如知识后端调用注册中心时携带的知识平台令牌。两种令牌不能混用：注册中心用自己的令牌认证本次校验请求，不使用待验证令牌作为校验接口的调用凭据。不需要平台密钥，也不验证用户登录状态。

### 请求参数

媒体类型 `application/json`；无 path、query 参数，body 必填且只能包含：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `access_token` | string | 是 | 待验证的平台访问令牌，非空且不能全为空白；与 header 中注册中心自己的令牌不同，无默认值。 |

不接受 platform_code、用户标识、用户权限、平台密钥或其他额外字段。凭证模型使用 SecretStr，正常响应和参数错误响应都不回显输入令牌或密钥。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`。固定只有 valid、code 两个字段：

```json
{"valid":true,"code":"knowledge-accumulation"}
```

待验证令牌未知、已过期、已被替换、映射不一致或服务正在清理关闭时，统一返回：

```json
{"valid":false,"code":null}
```

valid 表示本次检查时的有效性，code 只在有效时为令牌所属的平台代码。目标令牌无效不是本次调用者认证失败，因此仍返回 200，不返回 401。响应不包含 token、secret、用户资料、权限映射或有效期，不构成对后续操作的持续授权。

### 错误响应

| 状态码 | 响应 | 条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | header 中注册中心自身令牌缺失、无效或过期，携带 WWW-Authenticate: Bearer。 |
| `403` | `{"detail":"仅注册中心可以执行此操作"}` | header 认证平台不是配置的注册中心。 |
| `422` | `{"detail":"请求体仅接受非空 access_token 字符串"}` | JSON 非法、令牌字段缺失、类型错误、空白或存在额外字段，不回显原始请求内容。 |

注册中心需区分自身认证失败和被验证目标无效；中间件连接失败、请求超时及其他非预期响应都应拒绝放行，不应回退为调用 heartbeat 或信任客户端声明的平台代码。

### 请求示例

替换两个令牌为各自角色的实际值，不传平台密钥：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform/token/verify' \
  -H 'Authorization: Bearer replace-with-registry-platform-token' \
  -H 'Content-Type: application/json' \
  -d '{"access_token":"replace-with-calling-platform-token"}'
```

### 行为与边界

使用单进程权威会话索引的同步只读快照，核对双向映射和单调时钟截止时间。检查期间无 await，不获取被验证平台资源锁，不调用 heartbeat、过期清理、释放监听器或 Redis，不更改目标与注册中心自身的有效期。目标即使已过期但还未被后台清理，也立即返回无效；目标资源仍由既有后台清理或后续正常认证、重新登录负责回收。

该接口只证明平台身份，不授权任意 userid 或文件。注册中心仍须核对可信平台代码，例如只允许 knowledge-accumulation 代表其可信上下文绑定的用户访问，再实时检查该用户的文件权限；不能仅凭客户端传入的 userid 放行。每次敏感访问重新校验，不长期缓存有效结果，调用方负责用户来源绑定。

当前平台会话仍为单进程内存，重启后旧令牌失效，多实例必须先解决共享会话或路由到权威实例；本接口不新增共享存储，不承诺跨进程可见。实现与测试见[平台会话服务](../capability/application/platform-session.md#平台令牌只读校验)。

---

## 查询所有平台

`GET /middleware-service/api/platform/list`，返回启动时配置的所有平台基本信息、A2A 参与标记、子管理平台标记、管理平台入口标记及当前会话存活状态。

### 认证方式

必须携带 `Authorization: Bearer <access_token>`，由统一认证依赖验证。任何活跃平台均可查询所有配置平台；不返回密钥或访问令牌。

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
    "is_alive": true,
    "supports_a2a": false,
    "is_sub_management_platform": false,
    "is_management_entry": false,
    "a2aresponsibility": ""
  }
]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | string | 平台唯一代号。 |
| `name` | string | 平台中文名称。 |
| `description` | string | 平台用途描述。 |
| `is_alive` | boolean | 有未过期的登录会话时为 true，否则为 false。 |
| `supports_a2a` | boolean | 平台是否参与 A2A 协议，来自 YAML 配置，默认 false。 |
| `is_sub_management_platform` | boolean | 是否为子管理平台，来自 YAML 配置，默认 false。 |
| `is_management_entry` | boolean | 是否作为管理平台入口，来自 YAML 配置，默认 false。与子管理平台标记独立。 |
| `a2aresponsibility` | string | 平台后续 A2A 协作中的职责。子管理平台返回非空说明，如“负责解答合同相关问题。”；其他平台未配置时为空字符串。 |

### 错误响应

令牌缺失、无效或已过期返回 `401`，响应 `{"detail":"令牌缺失、无效或已过期"}`，并携带 `WWW-Authenticate: Bearer`。

### 请求示例

```bash
curl 'http://127.0.0.1:22000/middleware-service/api/platform/list' \
  -H 'Authorization: Bearer replace-with-access-token'
```

### 行为与边界

读取启动时加载的内存配置，不重新读取 YAML。列表不再隐藏平台，按 YAML 顺序返回所有配置项。supports_a2a、is_sub_management_platform、is_management_entry 与 a2aresponsibility 为静态配置属性，不随登录或失活改变，不代表中间件已经实现 A2A 通信能力，也不改变认证、业务权限或消息角色。

修改配置需要重启服务，配置与旧字段迁移说明见[平台身份配置](../capability/application/platform-config.md)。尚未登录、会话过期或正在关闭的平台均显示失活；状态按同一个时间点计算，过期但尚未被后台清理的记录也返回 false。

查询不续期、不创建或关闭 RabbitMQ 客户端，不探测子平台网络或业务健康情况；`is_alive` 仅表示本中间件中的登录会话状态。

---

## 全量更新平台管理员

`POST /middleware-service/api/platform/admins/full-table`，由注册中心或 knowledge-accumulation 全量重建当前启动配置中所有平台的管理员集合。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，必须属于 `knowledge-accumulation` 或 `RABBITMQ_REGISTRY_PLATFORM_CODE` 指定的注册中心（默认 registry）；其他平台返回 403。请求中的平台代码是维护目标，不是调用方身份。

### 请求参数

无 path、query 参数，必填 JSON body，禁止额外字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `admins` | object | 是 | 完整管理员映射，键为已配置的 platform_code，值为 userid 字符串数组；无默认值。 |
| `admins.<platform_code>[]` | string | 是 | 管理员 userid，非空且不全为空白，同一平台内自动去重。数组可为空。 |

未列出的已配置平台清空管理员；`admins: {}` 清空所有已配置平台。未知平台导致整批拒绝。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`：

```json
{"admin_count":3}
```

`admin_count` 为本次替换后的管理员关系总数：同一平台内去重，同一个 userid 管理两个平台时分别计数。

### 错误响应

| 状态码 | 响应 | 触发条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 平台令牌缺失或无效，携带 `WWW-Authenticate: Bearer`。 |
| `403` | `{"detail":"仅注册中心或 knowledge-accumulation 平台可以更新平台管理员总表"}` | 已认证调用方既不是注册中心，也不是 knowledge-accumulation。 |
| `422` | FastAPI 校验错误列表或业务 `detail` 字符串 | 请求类型、额外字段、平台代码或 userid 不合法。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 故障或写入确认结果不确定。 |

### 请求示例

替换令牌和平台代码为实际配置值：

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform/admins/full-table' \
  -H 'Authorization: Bearer replace-with-knowledge-accumulation-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"admins":{"platform-a":["user-001","user-002"],"platform-b":["user-002"]}}'
```

### 行为与边界

全批校验通过后，在一个 Redis 事务内替换全部已配置平台集合，失败校验不修改旧数据。与单条增删并发时按 Redis 实际执行顺序生效；事务前完成的增删可被本次完整快照覆盖，事务后执行的增删在新集合上生效。

管理员资料无需预先同步，也无需已登录。操作不修改用户资料、登录 TTL、平台会话或整数权限等级。集合不设置 TTL；缓存结构和生命周期见[平台管理员缓存](../capability/application/platform-admin-cache.md)。

---

## 新增平台管理员

`POST /middleware-service/api/platform/admins/add`，在指定平台的管理员集合中添加一个 userid。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，仅注册中心可以调用。指定 registry 作为目标平台不会让其他调用平台获得写权限；注册中心身份由 `RABBITMQ_REGISTRY_PLATFORM_CODE` 配置。

### 请求参数

无 path、query 参数，必填 JSON body，禁止额外字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `platform_code` | string | 是 | 目标平台代码，格式与 YAML 一致且必须已配置；无默认值。 |
| `userid` | string | 是 | 要添加的管理员唯一标识，非空且不全为空白；无默认值。 |

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`：

```json
{"platform_code":"platform-a","userid":"user-001","changed":true}
```

`changed=true` 表示本次确实添加了成员；成员已经存在时返回 `changed=false`，仍为 200。

### 错误响应

| 状态码 | 响应 | 触发条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 无有效平台令牌，携带 `WWW-Authenticate: Bearer`。 |
| `403` | `{"detail":"仅注册中心可以执行此操作"}` | 非注册中心调用。 |
| `422` | FastAPI 校验错误列表或业务 `detail` 字符串 | 目标平台未配置、userid 非法、字段缺失或存在额外字段。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 故障或写入确认结果不确定。 |

### 请求示例

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform/admins/add' \
  -H 'Authorization: Bearer replace-with-registry-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"platform_code":"platform-a","userid":"user-001"}'
```

### 行为与边界

使用 Redis SADD，只影响目标平台，不覆盖其他管理员；同一用户可以属于多个平台。新增关系不等于用户登录或赋予整数权限等级，不续期用户或平台会话。重复请求幂等；并发操作以实际执行顺序为准。

---

## 删除平台管理员

`POST /middleware-service/api/platform/admins/remove`，从指定平台的管理员集合中移除一个 userid。

### 认证方式

必填 header `Authorization: Bearer <access_token>`，仅注册中心可以调用。

### 请求参数

无 path、query 参数，必填 JSON body，禁止额外字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `platform_code` | string | 是 | 目标平台代码，格式与 YAML 一致且必须已配置；无默认值。 |
| `userid` | string | 是 | 要移除的管理员唯一标识，非空且不全为空白；无默认值。 |

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`：

```json
{"platform_code":"platform-a","userid":"user-001","changed":true}
```

`changed=true` 表示本次实际删除了成员；目标平台无管理员或该成员不存在时返回 `changed=false`，仍为 200。

### 错误响应

| 状态码 | 响应 | 触发条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 无有效平台令牌，携带 `WWW-Authenticate: Bearer`。 |
| `403` | `{"detail":"仅注册中心可以执行此操作"}` | 非注册中心调用。 |
| `422` | FastAPI 校验错误列表或业务 `detail` 字符串 | 目标平台未配置、userid 非法、字段缺失或存在额外字段。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 故障或写入确认结果不确定。 |

### 请求示例

```bash
curl -X POST 'http://127.0.0.1:22000/middleware-service/api/platform/admins/remove' \
  -H 'Authorization: Bearer replace-with-registry-access-token' \
  -H 'Content-Type: application/json' \
  -d '{"platform_code":"platform-a","userid":"user-001"}'
```

### 行为与边界

使用 Redis SREM，仅移除目标平台的关系，不删除该用户资料、登录记录或其他平台的管理员关系。删除最后一个成员后 Redis 不保留空集合键，后续内部查询返回空集合。操作不续期用户或平台会话，重复请求幂等。

---

## 查询当前平台管理员

`GET /middleware-service/api/platform/admins/list`，返回当前已认证平台允许的管理员 userid 列表。

### 认证方式

必填 header `Authorization: Bearer <access_token>`。registry 和其他已登录平台均可调用，但都只能查询令牌所属平台。调用方身份由统一认证依赖解析，不由请求参数指定。

### 请求参数

无 path、query 参数，无请求体。额外传入的 platform_code 查询参数不会参与目标平台选择。

### 成功响应

状态码 `200`，媒体类型 `application/json`，响应头 `Cache-Control: no-store`：

```json
["user-001","user-002"]
```

返回去重后的 userid 字符串数组，按字符串升序排序；当前平台没有配置管理员时返回 `[]`。这是管理员成员列表，不按用户资料是否已同步或当前是否登录过滤，也不返回其他平台的管理员。

### 错误响应

| 状态码 | 响应 | 条件 |
| --- | --- | --- |
| `401` | `{"detail":"令牌缺失、无效或已过期"}` | 无有效平台令牌，携带 `WWW-Authenticate: Bearer`。 |
| `503` | `{"detail":"用户存储服务暂不可用，操作结果可能不确定，请稍后核对"}` | Redis 读取失败；不得将故障解释为没有管理员。 |

### 请求示例

使用目标平台自身的访问令牌：

```bash
curl 'http://127.0.0.1:22000/middleware-service/api/platform/admins/list' \
  -H 'Authorization: Bearer replace-with-platform-access-token'
```

### 行为与边界

从 Redis 读取当前平台的管理员 Set 并排序返回，不新增或删除管理员，不续期平台会话、用户登录或管理员集合。registry 使用自己的令牌调用时，也只返回 registry 平台自身的管理员。管理员全量更新允许注册中心和 knowledge-accumulation，添加和删除仅允许注册中心。
