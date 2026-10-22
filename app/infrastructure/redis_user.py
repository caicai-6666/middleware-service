"""Redis 用户存储：以 Lua 保证跨键原子性，以版本比较保护总表替换。"""

from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError


class UserStorageUnavailableError(Exception):
    """用户存储不可用或操作结果不确定，不暴露 Redis 原始错误。"""


# 总表读取版本后构造差集，提交时核对版本，防止覆盖并发资料更新或遗漏登录清理。
_REPLACE = """
if (redis.call('GET', KEYS[2]) or '') ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
for i = 5, #ARGV, 2 do redis.call('HSET', KEYS[1], ARGV[i], ARGV[i+1]) end
for i = 3, #KEYS do
    local token = redis.call('GET', KEYS[i])
    local userid = string.sub(KEYS[i], string.len(ARGV[4]) + 1)
    if token and redis.call('GET', ARGV[3] .. token) == userid then
        redis.call('DEL', ARGV[3] .. token)
    end
    redis.call('DEL', KEYS[i])
end
redis.call('SET', KEYS[2], ARGV[2])
return 1
"""

# 使用原始 JSON 比较，Python 合并增量；避免 Lua cjson 改变空权限对象或大整数精度。
_PATCH = """
if (redis.call('HGET', KEYS[1], ARGV[1]) or '') ~= ARGV[2] then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[3])
redis.call('SET', KEYS[2], ARGV[4])
return 1
"""

# 写入、换 token 和续期都在脚本中完成，避免并发登录留下旧 token 的有效映射。
_CREATE_LOGIN = """
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 0 then return 0 end
local owner = redis.call('GET', KEYS[3])
if owner and owner ~= ARGV[1] then return -1 end
local old = redis.call('GET', KEYS[2])
if old and redis.call('GET', ARGV[4] .. old) == ARGV[1] then
    redis.call('DEL', ARGV[4] .. old)
end
redis.call('SET', KEYS[2], ARGV[3], 'EX', ARGV[2])
redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[2])
return tonumber(ARGV[2])
"""

_TOUCH = """
local userid = redis.call('GET', KEYS[2])
if not userid then return {} end
local login = ARGV[3] .. userid
local profile = redis.call('HGET', KEYS[1], userid)
if not profile
    or redis.call('GET', login) ~= ARGV[1]
    or redis.call('PTTL', login) <= 0
    or redis.call('PTTL', KEYS[2]) <= 0 then
    redis.call('DEL', KEYS[2])
    return {}
end
redis.call('EXPIRE', login, ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[2])
return {userid, tonumber(ARGV[2]), profile}
"""

_READ_LOGIN = """
local profile = redis.call('HGET', KEYS[1], ARGV[1])
local token = redis.call('GET', KEYS[2])
if not profile or not token then return {} end
local tokenkey = ARGV[2] .. token
if redis.call('GET', tokenkey) ~= ARGV[1] then return {} end
local ttl = math.min(redis.call('PTTL', KEYS[2]), redis.call('PTTL', tokenkey))
if ttl <= 0 then return {} end
return {profile, ttl}
"""


class RedisUserStore:
    def __init__(self, client: Redis, key_prefix: str):
        self._client = client
        self.profiles_key = f'{key_prefix}:users:profiles'
        self.revision_key = f'{key_prefix}:users:revision'
        self._login_prefix = f'{key_prefix}:users:login:'
        self._token_prefix = f'{key_prefix}:users:token:'

    def login_key(self, userid: str) -> str:
        return self._login_prefix + userid

    def token_key(self, access_token: str) -> str:
        return self._token_prefix + access_token

    async def _eval(self, script: str, keys: list[str], args: list):
        try:
            return await self._client.eval(script, len(keys), *keys, *args)
        except RedisError:
            raise UserStorageUnavailableError() from None

    async def get_info(self, userid: str) -> str | None:
        try:
            return await self._client.hget(self.profiles_key, userid)
        except RedisError:
            raise UserStorageUnavailableError() from None

    async def replace_all(self, profiles: dict[str, str]) -> int:
        for _ in range(8):
            try:
                async with self._client.pipeline(transaction=True) as pipe:
                    revision, old_ids = await pipe.get(self.revision_key).hkeys(self.profiles_key).execute()
            except RedisError:
                raise UserStorageUnavailableError() from None
            removed = set(old_ids) - profiles.keys()
            keys = [self.profiles_key, self.revision_key, *(self.login_key(userid) for userid in removed)]
            pairs = [item for pair in profiles.items() for item in pair]
            if await self._eval(_REPLACE, keys, [revision or '', uuid4().hex, self._token_prefix, self._login_prefix, *pairs]):
                return len(profiles)
        raise UserStorageUnavailableError()

    async def compare_update(self, userid: str, old: str | None, new: str) -> bool:
        return bool(await self._eval(_PATCH, [self.profiles_key, self.revision_key],
                                     [userid, old or '', new, uuid4().hex]))

    async def create_login(self, userid: str, access_token: str, ttl: int) -> int:
        return await self._eval(
            _CREATE_LOGIN, [self.profiles_key, self.login_key(userid), self.token_key(access_token)],
            [userid, ttl, access_token, self._token_prefix],
        )

    async def touch_login(self, access_token: str, ttl: int) -> tuple[str, int, str] | None:
        # 双向映射、资料存在性与 TTL 一并验证，不允许旧 token 或孤立索引续期。
        result = await self._eval(
            _TOUCH, [self.profiles_key, self.token_key(access_token)],
            [access_token, ttl, self._login_prefix],
        )
        return (result[0], result[1], result[2]) if result else None

    async def get_login(self, userid: str) -> tuple[str, int] | None:
        result = await self._eval(_READ_LOGIN, [self.profiles_key, self.login_key(userid)],
                                  [userid, self._token_prefix])
        return (result[0], result[1]) if result else None
