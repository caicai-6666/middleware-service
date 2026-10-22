"""异步 Redis 客户端与连接池，连接由应用生命周期统一释放。"""

from redis.asyncio import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config.redis import RedisSettings


def create_redis_client(settings: RedisSettings) -> Redis:
    return Redis(
        host=settings.host, port=settings.port, db=settings.db,
        password=settings.password, decode_responses=True, protocol=2,
        max_connections=settings.max_connections,
        socket_connect_timeout=settings.socket_timeout_seconds,
        socket_timeout=settings.socket_timeout_seconds,
        health_check_interval=30,
        # 写入超时可能已生效，禁用连接层自动重试，交由接口明确反馈不确定性。
        retry=Retry(NoBackoff(), 0),
    )
