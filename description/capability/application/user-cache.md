# 用户 Redis 缓存

本模块以 userid 为索引，维护用户资料及登录状态。Redis 是这两类数据的统一存储，后端不保留另一份用户内存缓存；当前尚未将用户权限接入消息接口授权。

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

资料 JSON 仅包含 userid、username、permissions。JSON 由 Python 编解码，Lua 不解析权限数值，避免大整数精度和空权限对象形态变化。资料返回为不可变快照，权限映射复制并设为只读。

平台与多个管理员 userid 的集合独立存储，使用 Redis Set，存储契约及内部方法见[平台管理员缓存](platform-admin-cache.md)。

## 总表与增量资料

总表接口允许 knowledge-accumulation 与 RABBITMQ_REGISTRY_PLATFORM_CODE 指定的注册中心调用，整表校验后原子替换资料 Hash，移除表外用户的登录键，保留表内已有用户的原登录 TTL；新增用户不会自动登录，空数组清空资料及对应登录状态。读取旧版本和用户列表后，在 Lua 中核对版本并提交，期间若发生并发资料写入则重新读取，最多重试 8 次，持续冲突返回 503。

单用户更新仅注册中心可调用，只改提供的字段。新增必须提供完整用户名和权限；权限映射整体替换，空对象撤销全部权限。先读取资料、在 Python 合并，再通过 Lua 比较原始 JSON 后写入，冲突最多重试 8 次，避免并发的用户名更新和权限更新互相覆盖。资料操作不创建登录状态，也不续期用户或平台会话。

所有写入前校验未知字段、类型、空白标识、重复 userid 和未配置的权限平台。权限值为严格整数，不接受布尔值、字符串或小数；等级含义由业务方定义。

---

## 用户登录与 TTL 退出

用户资料通过总表和单用户增量接口维护，与登录有效期独立。knowledge-accumulation 平台验证用户身份并确认登录成功后，调用 `PUT /middleware-service/api/user/login-cache`，必须传 userid 与用户 access_token；不传 is_logged_in、不传 TTL。平台认证仍使用请求头中的平台 Bearer 令牌。

服务在 Lua 中原子检查资料存在，并写入双向登录映射及同一 TTL。同一用户再次登录时移除旧 token 索引，只保留当前 token；有效 token 已归属其他用户时返回 409，不修改任何登录记录。资料未同步时返回 404，不创建登录键；重复成功登录通知会重设完整 TTL。过期后由新的成功登录事件重新写入。

后续查询通过 `POST /middleware-service/api/user/login-cache`，请求仅包含用户 access_token，按 token 解析 userid，只对已有有效登录续期。有效时同时返回用户当前 username；无效 token 返回 userid=null、username=null、is_logged_in=false、ttl=0。没有及时续期时，Redis 自动让登录键过期，即视为退出，无需持续同步布尔状态或发送用户心跳。is_logged_in 仅作为响应中根据键存在性和 TTL 计算的结果，不是缓存中的资料字段。

平台心跳现在只续期平台会话，不访问 Redis，不接收 users（包括空列表和 null，均返回 422）。旧的 UserUpdate、update_many 和 sync_logins 已移除。用户总表和部分资料更新不会自动登录或续期；管理员集合也与登录键独立。

## 登录查询与续期

登录缓存查询续期接口向所有已登录平台开放，写入接口仅允许 knowledge-accumulation。Lua 原子检查 token 索引、用户资料、登录记录与 token 一致且两份键均具有有效 TTL，然后使用 EXPIRE 为两份键设置完整有效期，并返回同次读取的用户资料，由服务层解析用户名。查询不会创建登录键，不会复活未知、未登录、已过期或无 TTL 的异常登录记录；无效的孤立 token 索引会被清理，总表移除用户时同时删除登录记录和 token 索引。

有效时返回 is_logged_in=true 和完整 TTL，默认 90 秒；无效返回 false、ttl=0。有效查询持续发生时用户持续存活，直到总表移除或登录键自然过期。用户登录续期不延长调用平台本身的会话。

内部方法均为异步：

| 方法 | 行为 |
| --- | --- |
| `await get_info(userid)` | 查询资料，缺失返回 None，与登录 TTL 无关。 |
| `await get(userid)` | 读取资料与有效登录快照，不续期；无有效登录返回 None。 |
| `await is_login_valid(userid)` | 只读登录有效性判断，不续期。 |
| `await touch_login(access_token)` | 查询并原子续期有效登录。 |
| `await replace_all(users)` | 原子替换总表并清理被移除用户的登录键。 |
| `await update_info(user)` | 部分资料合并、比较后写入。 |
| `await create_login(userid, access_token)` | knowledge-accumulation 平台的成功登录通知，要求资料存在，原子绑定用户 token 并设置双向映射的 TTL。 |

只读快照 TTL 按剩余毫秒向上取整，调用方不能长期持有旧快照判断有效性。Redis 负责过期，无本机清理任务。

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
