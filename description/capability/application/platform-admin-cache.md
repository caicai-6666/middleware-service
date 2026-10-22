# 平台管理员缓存

本模块在 Redis 中保存平台代码与管理员 userid 集合的对应关系。每个平台可有多位管理员，同一用户也可管理多个平台。现已提供 knowledge-accumulation 与注册中心可调用的全量更新接口、注册中心专用的添加和删除 HTTP 接口，以及已登录平台查询自身管理员的接口，具体契约见[平台 API](../../api/platform.md#全量更新平台管理员)。管理员映射不会自动改变现有消息接口的授权判断。

与[用户 Redis 缓存](user-cache.md)共用应用 Redis 客户端、连接池及命名空间，连接参数见 [Redis 部署](../infrastructure/redis.md)。

## 存储契约

| 项目 | 规则 |
| --- | --- |
| Redis Key | `<REDIS_KEY_PREFIX>:platforms:admins:<platform_code>` |
| 默认示例 | `middleware-service:platforms:admins:platform-a` |
| 类型 | Set |
| 成员 | 管理员 userid，非空且不全为空白的字符串，与用户资料使用相同标识规则。 |
| TTL | 不设置；显式更新或移除。 |

业务上的 key 是平台代码，value 是管理员 userid 集合。例如：

```text
middleware-service:platforms:admins:platform-a → {user-001, user-002}
middleware-service:platforms:admins:platform-b → {user-002, user-003}
```

Set 自动去重，不保证顺序。未设置管理员、删除最后一位管理员或以空列表替换时，Redis 不保留空集合键，查询统一返回空集合。

## 主要职责与使用方式

| 位置 | 职责 |
| --- | --- |
| `app/infrastructure/redis_platform_admin.py` | Set 读写、集合成员判断、整组原子替换及错误转换。 |
| `app/service/platform_admin.py` | 平台和 userid 校验，提供内部业务方法。 |
| `app/router/platform.py` | knowledge-accumulation 与注册中心全量更新、注册中心增删接口，以及平台通过令牌自查管理员列表。 |
| `app/main.py` | 创建 `app.state.platform_admins`，复用现有 Redis 连接。 |

```python
admins = request.app.state.platform_admins
await admins.replace_all_admins({"platform-a": ["user-001", "user-002"]})
await admins.replace_admins("platform-a", ["user-001", "user-002"])
await admins.add_admin("platform-a", "user-003")
await admins.remove_admin("platform-a", "user-001")
userids = await admins.get_admins("platform-a")
matched = await admins.is_admin("platform-a", "user-002")
```

| 方法 | 返回与行为 |
| --- | --- |
| `replace_all_admins(admins)` | 校验平台代码到 userid 列表的全量映射；未列出的已配置平台清空，同一事务替换所有已配置平台，返回管理员关系总数。 |
| `replace_admins(platform_code, userids)` | 接收字符串列表，全批校验后替换指定平台的完整集合；空列表清空，返回 None。 |
| `add_admin(platform_code, userid)` | 新增成员返回 true；已存在返回 false，不覆盖其他管理员。 |
| `remove_admin(platform_code, userid)` | 实际删除返回 true；不存在返回 false。 |
| `get_admins(platform_code)` | 返回不可变 frozenset，无配置时为空集合。 |
| `is_admin(platform_code, userid)` | 返回该用户是否属于该平台的管理员集合。 |

## 校验、并发与生命周期

平台代码必须存在于启动 YAML 配置中；未知平台或非法 userid 导致 ValueError（包括模型校验错误），不修改现有数据。整组替换先校验全部成员，再以 Redis 事务执行删除和写入，读取只会看到替换前或替换后的完整集合。新增、删除和成员判断分别使用 SADD、SREM、SISMEMBER。

管理员配置独立于用户资料、整数权限等级及登录状态，不要求用户先同步资料或已登录，以支持不同数据的初始化顺序。用户退出、登录 TTL 过期或用户总表重建不会自动删除管理员映射；管理方需要显式更新管理员集合。管理员集合成员关系本身不等于用户当前登录有效。

Redis 故障抛出统一的 UserStorageUnavailableError，不回退到内存、不把故障当作空管理员集合。沿用用户存储的超时和不确定结果处理方式，异常不暴露原始 Redis 凭据；管理员读写接口均复用应用已注册的 503 错误处理。

无需增加环境变量或修改 deploy。后台关闭只断开共享连接，不清空管理员集合；重启后继续读取原 Redis 数据。测试不写入实际业务命名空间。

## 验证

`tests/test_platform_admin.py` 和 `tests/test_platform_admin_router.py` 使用真实 Redis 和随机前缀，验证多管理员、重复去除、平台隔离、幂等增删、空列表清空、非法批次不部分覆盖、并发添加、跨平台原子替换、全量遗漏平台清空、HTTP 全量更新与单条增删的角色隔离及令牌校验、自查按令牌隔离与稳定排序、登录与资料独立、跨服务实例共享及故障错误脱敏。测试完成后仅删除测试前缀内的键。测试目录按当前 Git 白名单规则仅保留在本机。
