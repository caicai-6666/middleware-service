"""平台管理员 Set 存储，共用应用 Redis 连接池与命名空间。"""

from collections.abc import Awaitable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.infrastructure.redis_user import UserStorageUnavailableError


class RedisPlatformAdminStore:
    def __init__(self, client: Redis, key_prefix: str):
        self._client = client
        self._key_prefix = f"{key_prefix}:platforms:admins:"

    def key(self, platform_code: str) -> str:
        return self._key_prefix + platform_code

    @staticmethod
    async def _run(command: Awaitable):
        try:
            return await command
        except RedisError:
            raise UserStorageUnavailableError() from None

    async def replace_admins(self, platform_code: str, userids: frozenset[str]) -> None:
        await self.replace_all_admins({platform_code: userids})

    async def replace_all_admins(self, admins: dict[str, frozenset[str]]) -> None:
        # 清空和写入在同一事务提交，读取方只能观察到替换前或替换后的完整集合。
        if not admins:
            return
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.delete(*(self.key(code) for code in admins))
                for code, userids in admins.items():
                    if userids:
                        pipe.sadd(self.key(code), *userids)
                await pipe.execute()
        except RedisError:
            raise UserStorageUnavailableError() from None

    async def add_admin(self, platform_code: str, userid: str) -> bool:
        return bool(await self._run(self._client.sadd(self.key(platform_code), userid)))

    async def remove_admin(self, platform_code: str, userid: str) -> bool:
        return bool(await self._run(self._client.srem(self.key(platform_code), userid)))

    async def get_admins(self, platform_code: str) -> frozenset[str]:
        return frozenset(await self._run(self._client.smembers(self.key(platform_code))))

    async def is_admin(self, platform_code: str, userid: str) -> bool:
        return bool(await self._run(self._client.sismember(self.key(platform_code), userid)))
