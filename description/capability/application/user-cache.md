# 用户 Redis 缓存

本模块以 userid 为索引，维护用户资料（包括任职状态）及登录状态。Redis 是这两类数据的统一存储，后端不保留另一份用户内存缓存；当前尚未将用户权限接入消息接口授权。

已提供[本平台文件权限查询](../../api/user.md#查询用户在本平台的文件权限)，用于 A2A 等只有可信 userid 的场景，由调用平台服务端执行本平台文件过滤；消息路由不自动代行业务资源过滤。

外部契约见[用户 API](../../api/user.md)，容器与本机连接方式见 [Redis 部署](../infrastructure/redis.md)。

## 职责与存储结构

| 位置 | 职责 |
| --- | --- |
| `app/config/redis.py` | Redis 地址、密码、数据库、键前缀及连接池上限。 |
| `app/infrastructure/redis.py` | asyncio 客户端、连接池、连接/读写超时和关闭。 |
| `app/infrastructure/redis_user.py` | 用户 Redis 存储与跨键原子操作。 |
| `app/service/user.py` | 用户模型、权限校验、资料增量合并与业务返回。 |
| `app/router/user.py` | 用户资料维护、成功登录写入和登录查询续期，按接口区分 knowledge-accumulation 与注册中心的写权限。 |
| `app/main.py` | 启动连接校验、服务装配、503 映射和停机断开。 |

默认 `REDIS_KEY_PREFIX=middleware-service`：

| Redis Key | 类型 | 内容及有效期 |
| --- | --- | --- |
| `middleware-service:users:profiles` | Hash | field 为 userid，value 为完整用户资料 JSON；不设 TTL。 |
| `middleware-service:users:login:<userid>` | String | 值为该用户的 access_token；按统一 TTL 自动过期。 |
| `middleware-service:users:token:<access_token>` | String | 值为 userid，供用户 token 查询；与登录记录同时写入、续期和过期。 |
| `middleware-service:users:revision` | String | 资料版本随机标识，每次资料写入变化，保护总表替换并发一致性。 |

资料 JSON 包含 userid、username、permissions、departments、employment_status。departments 是部门名称字符串数组，可有多个部门；名称去除首尾空白后非空且不可重复，不接受 null，顺序不表示主部门或权限。旧 JSON 缺少该字段时按空列表读取，无需改 Redis 键或执行数据迁移；新写入完整保存该字段，内部快照用 tuple 防止外部修改。JSON 由 Python 编解码，Lua 不解析权限数值，避免大整数精度和空权限对象形态变化。资料返回为不可变快照，权限映射复制并设为只读。

平台与多个管理员 userid 的集合独立存储，使用 Redis Set，存储契约及内部方法见[平台管理员缓存](platform-admin-cache.md)。

## 总表与增量资料

总表接口允许 knowledge-accumulation 与 RABBITMQ_REGISTRY_PLATFORM_CODE 指定的注册中心调用，整表校验后原子替换资料 Hash，移除表外用户的登录键，保留表内已有用户的原登录 TTL；新增用户不会自动登录，空数组清空资料及对应登录状态。任职状态省略时按默认在职完整替换，HTTP 响应返回 count 及逐用户的 userid、employment_status；精确字段契约见[用户 API](../../api/user.md#重建用户信息总表)。完整用户项省略 departments 时按 [] 写入，会清空原部门；总表替换不会保留省略字段的旧值。读取旧版本和用户列表后，在 Lua 中核对版本并提交，期间若发生并发资料写入则重新读取，最多重试 8 次，持续冲突返回 503。

单用户更新仅注册中心可调用，只改提供的字段。新增必须提供完整用户名和权限；权限映射整体替换，空对象撤销全部权限。departments 提供时整体替换，[] 清空，省略保留已有部门，显式 null 拒绝；新建用户部门可省略，仍须提供用户名和权限。仅更新部门也合法。先读取资料、在 Python 合并，再通过 Lua 比较原始 JSON 后写入，冲突最多重试 8 次，避免并发的用户名、部门、任职状态和权限更新互相覆盖。资料操作不创建登录状态，也不续期用户或平台会话。

所有写入前校验未知字段、类型、空白标识、重复 userid 和未配置的权限平台。权限值为严格整数，不接受布尔值、字符串或小数；等级含义由业务方定义。

---

## 任职状态

`app/service/user.py` 使用共享的 EmploymentStatus 字面量类型定义 employed（在职）与 resigned（离职），资料模型、增量模型和查询响应共用两种值。完整资料省略时默认为在职，增量省略时保留原值；显式 null 和非法值在写入前拒绝。旧 JSON 缺字段时按在职解码，只读兼容不改写原始资料，无需 Redis 数据迁移。

状态随现有 profiles Hash 的 JSON 保存，沿用总表版本比较与增量比较交换，不新增 Redis 键或 Lua 分支。内部 UserProfile、UserSnapshot 和 UserLoginStatus 携带该字段；增量更新返回完整资料，登录查询从原子读取的同一份资料取得最新状态，无有效登录时返回 null。全表响应按已成功写入的请求顺序逐项返回标识与状态，不再次读取可能已被并发变更的资料。

本次仅增加资料标识：离职状态不删除或续期登录键，不拒绝成功登录通知，不改变整数文件权限、管理员集合、A2A 调度或任务队列。现有登录、权限和平台认证规则继续独立执行。完整输入输出、全表省略字段的覆盖语义与调用方升级注意事项见[用户 API](../../api/user.md)。不新增运行配置或依赖，不修改其他项目。

---

## 用户登录与 TTL 退出

用户资料通过总表和单用户增量接口维护，与登录有效期独立。部门归属不自动改变整数文件权限或平台管理员集合。knowledge-accumulation 平台验证用户身份并确认登录成功后，调用 `PUT /middleware-service/api/user/login-cache`，必须传 userid 与用户 access_token；不传 is_logged_in、不传 TTL。平台认证仍使用请求头中的平台 Bearer 令牌。

服务在 Lua 中原子检查资料存在，并写入双向登录映射及同一 TTL。同一用户再次登录时移除旧 token 索引，只保留当前 token；有效 token 已归属其他用户时返回 409，不修改任何登录记录。资料未同步时返回 404，不创建登录键；重复成功登录通知会重设完整 TTL。过期后由新的成功登录事件重新写入。

后续查询通过 `POST /middleware-service/api/user/login-cache`，请求仅包含用户 access_token，按 token 解析 userid，只对已有有效登录续期。有效时同时返回用户当前 username、departments 和 employment_status；有效登录无部门时 departments=[]，无效 token 返回 userid=null、username=null、departments=null、employment_status=null、is_logged_in=false、ttl=0。没有及时续期时，Redis 自动让登录键过期，即视为退出，无需持续同步布尔状态或发送用户心跳。is_logged_in 仅作为响应中根据键存在性和 TTL 计算的结果，不是缓存中的资料字段。

平台心跳现在只续期平台会话，不访问 Redis，不接收 users（包括空列表和 null，均返回 422）。旧的 UserUpdate、update_many 和 sync_logins 已移除。用户总表和部分资料更新不会自动登录或续期；管理员集合也与登录键独立。

## 登录查询与续期

登录缓存查询续期接口向所有已登录平台开放，写入接口仅允许 knowledge-accumulation。Lua 原子检查 token 索引、用户资料、登录记录与 token 一致且两份键均具有有效 TTL，然后使用 EXPIRE 为两份键设置完整有效期，并返回同次读取的用户资料，由服务层解析用户名、部门和任职状态。查询不会创建登录键，不会复活未知、未登录、已过期或无 TTL 的异常登录记录；无效的孤立 token 索引会被清理，总表移除用户时同时删除登录记录和 token 索引。

有效时返回 is_logged_in=true 和完整 TTL，默认 90 秒；无效返回 false、ttl=0。有效查询持续发生时用户持续存活，直到总表移除或登录键自然过期。用户登录续期不延长调用平台本身的会话。

内部方法均为异步：

| 方法 | 行为 |
| --- | --- |
| `await get_info(userid)` | 查询资料，缺失返回 None，与登录 TTL 无关。 |
| `await get_file_permission(userid, platform_code)` | 只读权威资料，返回该用户在认证平台的文件等级；未登记或无键为未授权，显式 0 有效，不读取登录或管理员键。 |
| `await get(userid)` | 读取资料与有效登录快照，不续期；无有效登录返回 None。 |
| `await is_login_valid(userid)` | 只读登录有效性判断，不续期。 |
| `await touch_login(access_token)` | 查询并原子续期有效登录。 |
| `await replace_all(users)` | 原子替换总表并清理被移除用户的登录键。 |
| `await update_info(user)` | 部分资料合并、比较后写入。 |
| `await create_login(userid, access_token)` | knowledge-accumulation 平台的成功登录通知，要求资料存在，原子绑定用户 token 并设置双向映射的 TTL。 |

只读快照 TTL 按剩余毫秒向上取整，调用方不能长期持有旧快照判断有效性。Redis 负责过期，无本机清理任务。

---

## 本平台文件权限只读查询

`app/router/user.py` 的 query_user_file_permission 使用 require_platform 认证，JSON body 复用禁止额外字段的 UserKey，只接受 userid；平台代码从 AuthenticatedPlatform.code 获取。有效登录平台均可调用，不要求平台管理员，不使用用户 access_token。

`UserService.get_file_permission` 复用 get_info 的单次 Redis HGET，解析当前资料并核对资料正文 userid 与查询索引一致。缺失用户或没有本平台权限键时返回 file_level=null；显式存在等级 0 时返回 file_level=0。UserFilePermission 模型只包含严格非负整数或 null 的 file_level 字段，不是裸数字，不返回用户、平台标识或完整权限映射。

本接口要求本平台等级为严格非负整数，拒绝布尔、字符串、小数、null 和负数；资料或等级异常与 Redis 故障统一通过 UserStorageUnavailableError 返回 503，调用方必须拒绝放行。既有用户权限维护接口的有符号整数规则不变，不在本次全局调整历史等级语义。

查询不访问用户登录键、token 索引或平台管理员集合，不创建、续期或撤销登录；未登录及登录已过期的用户，只要资料仍有效，都按本平台当前权限返回。每次实时读取，无独立权限缓存；调用方应在文件读取或会话续接时复查，服务端过滤文件，不将等级传给模型。本次不增加 Redis 键、运行配置或第三方依赖。

---

## 配置、生命周期与故障

进程环境优先于本项目 `.env`，最后使用默认值，不展开变量引用。配置仅启动时读取，修改需重启。

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `USER_CACHE_TTL_SECONDS` | `90` | 成功登录写入或有效查询设置的用户登录 TTL，正整数秒。 |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` | 本机地址；Compose 使用 redis:6379。 |
| `REDIS_PASSWORD` | 无 | 必须非空，Redis 默认用户密码，不输出到日志。 |
| `REDIS_DB` | `0` | 非负整数数据库编号，由服务端数据库数量约束。 |
| `REDIS_KEY_PREFIX` | `middleware-service` | 用户存储命名空间；不同环境须隔离。 |
| `REDIS_MAX_CONNECTIONS` | `20` | 单进程连接池上限，正整数。 |

连接和读写超时均为 3 秒，协议使用 RESP2，写操作不进行连接层自动重试。Redis 连接、认证或命令失败统一返回 503，不静默改用内存或将故障当作用户未登录。写入超时可能已经生效，503 不能作为确定未写入的依据。

应用启动 PING 失败会终止启动；关闭只断开客户端及连接池，绝不清空用户数据。后端重启后资料和仍有 TTL 的登录继续从 Redis 读取；平台访问令牌仍需重新登录。旧内存数据不自动迁移，首次切换需 knowledge-accumulation 发送完整用户总表，并在用户实际成功登录后写入登录缓存。

删除旧的 USER_CACHE_CLEANUP_INTERVAL_SECONDS 配置；Redis 自动处理过期，原参数不再生效。部署模板和后端环境注入已同步。本实现针对单节点 Redis；虽然用户状态可共享，平台会话、消息缓存和发布控制仍限制后端为单实例、单 worker。

## 旧登录记录与升级

原登录键值为 `1` 的记录缺少用户 token，升级后不会被当作有效登录。用户资料保留，knowledge-accumulation 需在用户成功登录后通过新契约写入 token；旧请求按 userid 查询会返回 422。Redis 中用户 token 属于登录凭据，不应输出到应用日志或响应中。

---

## 验证

用户测试连接真实 Redis，使用随机键前缀隔离，仅清理测试前缀，不清空共享数据库。覆盖 token 查询、换 token 失效、跨用户 token 冲突、双键续期、总表替换及版本冲突、并发部分字段更新、大整数权限、自动过期、资料移除与登录写入/查询并发、跨服务实例共享、用户写入/查询权限隔离、旧心跳 users 拒绝、平台心跳不访问 Redis、Redis 故障 503、启动失败关闭连接池，以及应用停机保留数据。测试目录按当前 Git 白名单仅保留在本机。

2026-10-26 使用独立 Redis 7.4.8 临时容器完成本平台文件权限的 9 项测试，全部通过：平台隔离和最小响应、等级 0 与缺失键区分、权限降级及撤销即时生效、无需用户登录及查询不改变双向 TTL、非法数据及身份不一致拒绝、既有负整数写入兼容与大整数精度、平台认证和额外参数拒绝、Redis 故障及错误键类型 503、OpenAPI 字段范围。测试位于本机忽略目录 tests/test_user_file_permission.py，结束后清理自身前缀并移除容器，未操作业务数据。

2026-10-29 多部门契约扩展：资料同步、部分更新、Redis 编解码和登录查询响应支持 departments，具体字段约束与完整替换/增量保留语义以[用户 API](../../api/user.md)为准。调用方若严格拒绝未知响应字段，需要同步更新 DTO；本次仅修改中间件，不自动重启 IDE 服务或改写业务用户资料。

多部门扩展验证：新增 10 项多部门用例与既有 9 项文件权限回归共 19 项全部通过。使用独立 Redis 7.4.8 临时容器和真实 ASGI 路由，覆盖多部门持久化、空白/重复/非法输入拒绝、旧 JSON 兼容、全量与增量语义、并发更新、登录查询最新资料与 TTL、认证隔离及 OpenAPI 字段约束。测试位于本机忽略目录 tests/test_user_departments.py 和 tests/test_user_file_permission.py，完成后移除临时容器，未访问或改写业务 Redis。

2026-11-02 任职状态扩展：新增 10 项任职状态用例，与现有多部门及文件权限的 19 项回归共 29 项全部通过。验证全表逐用户状态回执、增量省略保留、全量省略默认在职、旧 JSON 兼容、非法输入整批拒绝、并发字段更新、登录查询最新状态，以及离职不撤销登录或改变文件等级。OpenAPI 校验覆盖枚举、null 边界和最小响应。

验证使用本项目真实 Redis、随机测试前缀和真实 ASGI 路由，仅操作并清理测试前缀，未改写业务用户或重启服务。独立测试容器因 Docker 资源不足未能启动，未将其记录为验证成功。测试源码在本机忽略目录 tests/test_user_employment_status.py，并同步调整多部门回归的响应断言；不新增依赖或运行配置。

2026-11-09 提交前复验：上述任职状态、多部门和文件权限共 29 项回归全部通过，继续使用随机测试前缀隔离真实 Redis 数据；部署说明已同步任职状态响应及兼容边界，无新增部署参数。
