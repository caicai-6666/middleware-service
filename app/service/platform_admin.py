"""平台管理员映射，独立于用户登录 TTL 与权限整数等级。"""

from collections.abc import Iterable

from app.infrastructure.redis_platform_admin import RedisPlatformAdminStore
from app.service.user import UserKey


class PlatformAdminService:
    def __init__(self, platform_codes: Iterable[str], store: RedisPlatformAdminStore):
        self._platform_codes = frozenset(platform_codes)
        self._store = store

    def _validate_platform(self, platform_code: str) -> None:
        if not isinstance(platform_code, str) or platform_code not in self._platform_codes:
            raise ValueError("管理员缓存的平台代码必须存在于平台配置中。")

    async def replace_admins(self, platform_code: str, userids: list[str]) -> None:
        self._validate_platform(platform_code)
        if not isinstance(userids, list):
            raise ValueError("管理员列表必须为用户标识字符串列表。")
        # 整批校验后再替换；Set 自然去重，不要求用户先同步资料或处于登录状态。
        ids = frozenset(UserKey(userid=userid).userid for userid in userids)
        await self._store.replace_admins(platform_code, ids)

    async def replace_all_admins(self, admins: dict[str, list[str]]) -> int:
        if not isinstance(admins, dict):
            raise ValueError("管理员总表必须为平台代码到用户标识列表的映射。")
        # 先校验全表；省略的平台也明确写为空，防止旧管理员残留。
        assignments = {code: frozenset() for code in self._platform_codes}
        for code, userids in admins.items():
            self._validate_platform(code)
            if not isinstance(userids, list):
                raise ValueError("管理员列表必须为用户标识字符串列表。")
            assignments[code] = frozenset(UserKey(userid=userid).userid for userid in userids)
        await self._store.replace_all_admins(assignments)
        return sum(len(userids) for userids in assignments.values())

    async def add_admin(self, platform_code: str, userid: str) -> bool:
        self._validate_platform(platform_code)
        return await self._store.add_admin(platform_code, UserKey(userid=userid).userid)

    async def remove_admin(self, platform_code: str, userid: str) -> bool:
        self._validate_platform(platform_code)
        return await self._store.remove_admin(platform_code, UserKey(userid=userid).userid)

    async def get_admins(self, platform_code: str) -> frozenset[str]:
        self._validate_platform(platform_code)
        return await self._store.get_admins(platform_code)

    async def is_admin(self, platform_code: str, userid: str) -> bool:
        self._validate_platform(platform_code)
        return await self._store.is_admin(platform_code, UserKey(userid=userid).userid)
