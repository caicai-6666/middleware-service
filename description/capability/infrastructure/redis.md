# Redis 容器部署

Redis 由独立的 `middleware-service-deploy` 项目维护，与 RabbitMQ 和后端通过同一份 Compose 部署。后端已通过异步客户端读写用户资料和登录缓存；平台会话、Stream 预取缓存和发布控制仍按原有进程内实现运行。用户存储结构与原子更新规则见[用户 Redis 缓存](../application/user-cache.md)。

## 文件与配置

部署项目新增 `redis/` 目录：

| 文件 | 职责 |
| --- | --- |
| `redis/Dockerfile` | 基于官方 Redis 7.4.8 Bookworm 镜像并固定 digest，打包配置、启动脚本和认证健康检查。 |
| `redis/redis.conf` | 内部监听、AOF 持久化和默认不淘汰键的策略。 |
| `redis/start-redis.sh` | 校验密码，通过官方入口启动 Redis，沿用非 root 运行与数据目录权限处理。 |
| `docker-compose.yml` | 构建 Redis 镜像、挂载命名卷并声明后端启动依赖。 |
| `.env.example` | 提供 `REDIS_PASSWORD` 模板，实际密码由部署 `.env` 统一注入。 |

Redis 通过 Compose 默认网络的 `redis:6379` 提供服务，不发布宿主机端口。使用默认用户 `default` 和密码认证，不支持匿名访问。后端容器注入 `REDIS_HOST=redis`、`REDIS_PORT=6379` 和统一密码，并支持 `REDIS_DB`（默认 0）、`REDIS_KEY_PREFIX`（默认 middleware-service）、`REDIS_MAX_CONNECTIONS`（默认 20）。客户端使用 `redis==8.1.0` 的 asyncio 接口，应用启动时 PING，退出时关闭连接池而不清空数据；生命周期用法参阅 [redis-py 官方说明](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)。

密码非空时才能启动，不应使用模板中的占位值。启动脚本将密码作为单个参数传递，不对密码进行 shell 二次解析；Compose `.env` 中需要字面量 `$` 时使用单引号，遵循 Compose 环境文件语法。

---

## 启动与健康检查

首次部署复制 `.env.example` 为 `.env`，已有部署则在原 `.env` 中补充 `REDIS_PASSWORD`，保留其他实际配置。在部署项目目录执行：

```bash
docker compose up -d --build --wait
docker compose ps
```

Redis 镜像每 10 秒执行一次携带密码的 `PING`，只有准确返回 `PONG` 才健康。后端同时等待 Redis 与 RabbitMQ 健康后启动。该依赖仅控制启动顺序，运行期间用户读写故障会返回 503，连接恢复后后续请求可重新连接；不会回退为内存用户状态。

仅升级或启动 Redis 时可执行：

```bash
docker compose up -d --build --wait redis
docker compose logs --tail=100 redis
```

容器内部验证连接：

```bash
docker compose exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping'
```

Redis 密码由每次启动时的环境变量配置；修改密码后需重建 Redis 和后端容器以同步环境，不需要删除数据卷。

---

## 持久化与边界

命名卷 `redis-data` 挂载 `/data`。开启 `appendonly yes` 和 `appendfsync everysec`，关闭周期 RDB 快照。正常停止或重建容器时复用原卷恢复数据；每秒同步并不保证异常断电零丢失，具体语义见 [Redis 官方持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)。

当前采用单节点部署，不提供副本、自动故障切换或高可用。未设置 `maxmemory` 容量上限，淘汰策略为 `noeviction`；业务接入时需要根据数据规模补充容量、过期时间和资源限制。不要通过删除命名卷来更新配置；`docker compose down -v` 会删除本部署的持久化数据。

后端镜像、生产目录结构和统一启动方式见[应用容器镜像](../application/container-image.md)。

---

## 验证记录

2026-10-19 使用独立测试容器与命名卷完成验证：Compose 配置解析、Redis 镜像构建、包含空格及特殊字符的密码认证、匿名与错误密码拒绝、认证健康检查、非 root 运行、无宿主机端口、AOF 配置、正常停止并重建后的数据恢复，以及缺少密码时拒绝启动，均通过。

测试完成后删除测试容器和测试卷；未启动整套生产 Compose，也未修改现有 RabbitMQ 数据。未执行异常断电验证。


---

## 本机开发

本机开发连接使用本项目 `.env`，与生产 deploy 的实际密码独立。当前已准备 `data/redis-dev/docker-compose.yml`，项目名为 `middleware-service-redis-dev`，只向本机 `127.0.0.1` 发布 Redis 端口，默认 6379；卷名为 `middleware-service-redis-dev_redis-data`。

从应用项目根目录执行：

```bash
docker compose --env-file .env -f data/redis-dev/docker-compose.yml up -d --build --wait redis
```

该 Compose 文件按 data 白名单规则忽略，只有 `data/redis-dev/.gitkeep` 进入 Git。新工作区需自行准备本机 Compose 或连接现成 Redis；下面是当前开发配置的最小示例，构建目录假定 deploy 与应用仓库同级：

```yaml
name: middleware-service-redis-dev
services:
  redis:
    build:
      context: ../../../middleware-service-deploy/redis
    image: middleware-redis:7.4.8
    restart: unless-stopped
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:?请配置本项目 .env}
    ports:
      - "127.0.0.1:${REDIS_PORT:-6379}:6379"
    volumes:
      - redis-data:/data
    stop_grace_period: 30s
volumes:
  redis-data:
```

在 `.env` 中设置 REDIS_HOST=localhost、REDIS_PORT、REDIS_DB=0 和独立强密码；修改端口时同步后端连接配置。该开发目录被 Docker 构建上下文排除，生产部署仍不发布 Redis 端口。

2026-10-19 用户存储迁移后，真实 Redis 测试使用随机键前缀隔离，覆盖自动过期、登录映射原子续期、退出与查询并发、总表版本冲突、并发增量资料更新、故障 503、跨服务实例共享和后端停机保留数据。测试仅清理自己的前缀；本机 Redis 开发容器保留运行，未操作现有 RabbitMQ 业务消息。
