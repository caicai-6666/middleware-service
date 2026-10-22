"""用户资料和登录状态存储于 Redis，资料维护与登录 TTL 独立。"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.config.user import UserCacheSettings
from app.infrastructure.redis_user import RedisUserStore, UserStorageUnavailableError


PlatformCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")]


class UserKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    userid: str = Field(min_length=1, description="用户全局唯一字符串标识，作为哈希表键。")

    @field_validator("userid")
    @classmethod
    def reject_blank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("用户标识不能仅包含空白字符。")
        return value


class UserToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1, repr=False, description="用户成功登录后获得的访问令牌，与请求头用于平台认证的 Bearer 令牌不同；不可为空或纯空白。")

    @field_validator("access_token")
    @classmethod
    def reject_blank_token(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("用户访问令牌不能仅包含空白字符。")
        return value


class UserLoginRequest(UserKey, UserToken):
    """可信平台同步用户登录事件，必须绑定用户与其令牌。"""


class UserTokenConflictError(Exception):
    """有效 token 已绑定其他用户，禁止改变其身份。"""


class UserInfo(UserKey):
    username: str = Field(min_length=1, description="用户展示名称。")
    permissions: dict[PlatformCode, StrictInt] = Field(description="平台权限映射，键为已配置的 platform_code，值为整数等级；空对象撤销全部权限。")

    @field_validator("username")
    @classmethod
    def reject_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("用户名不能仅包含空白字符。")
        return value


class UserNotFoundError(Exception):
    """用户资料尚未同步，不能创建登录缓存。"""


class UserInfoPatch(UserKey):
    username: str | None = Field(default=None, min_length=1, description="需要更新的用户名；省略保留原值，不接受 null 或纯空白。新增用户必须提供。")
    permissions: dict[PlatformCode, StrictInt] | None = Field(default=None, description="需要整体替换的平台权限；省略保留原值，空对象撤销全部权限，不接受 null。新增用户必须提供。")

    @model_validator(mode="after")
    def validate_patch(self):
        fields = self.model_fields_set - {"userid"}
        if not fields:
            raise ValueError("至少提供 username 或 permissions 中的一项。")
        if any(getattr(self, name) is None for name in fields):
            raise ValueError("用户更新字段不能为 null。")
        if self.username is not None and not self.username.strip():
            raise ValueError("用户名不能仅包含空白字符。")
        return self


@dataclass(frozen=True)
class UserProfile:
    userid: str
    username: str
    permissions: Mapping[str, int]


@dataclass(frozen=True)
class UserSnapshot:
    userid: str
    username: str
    permissions: Mapping[str, int]
    is_logged_in: bool
    ttl: int


@dataclass(frozen=True)
class UserLoginStatus:
    userid: str | None
    is_logged_in: bool
    ttl: int
    username: str | None = None


class UserService:
    def __init__(self, platform_codes: Iterable[str], settings: UserCacheSettings, store: RedisUserStore):
        self._platform_codes = frozenset(platform_codes)
        self._settings = settings
        self._store = store

    def validate_updates(self, users: list[UserInfo]) -> None:
        seen = set()
        for user in users:
            if user.userid in seen:
                raise ValueError("同一批用户更新不能包含重复 userid。")
            seen.add(user.userid)
            if user.permissions.keys() - self._platform_codes:
                raise ValueError("用户权限包含未配置的平台代号。")

    @staticmethod
    def _encode(user: UserInfo) -> str:
        # 用户资料与登录键分开，资料 JSON 不包含登录状态或有效期。
        return UserInfo(userid=user.userid, username=user.username,
                        permissions=user.permissions).model_dump_json()

    @staticmethod
    def _decode(raw: str) -> UserProfile:
        try:
            info = UserInfo.model_validate_json(raw)
        except ValueError:
            raise UserStorageUnavailableError() from None
        return UserProfile(info.userid, info.username, MappingProxyType(dict(info.permissions)))

    async def replace_all(self, users: list[UserInfo]) -> int:
        self.validate_updates(users)
        return await self._store.replace_all({user.userid: self._encode(user) for user in users})

    async def update_info(self, update: UserInfoPatch) -> UserProfile:
        # 以原始资料比较并交换，防止两个并发的部分字段更新相互覆盖。
        if update.permissions is not None and update.permissions.keys() - self._platform_codes:
            raise ValueError("用户权限包含未配置的平台代号。")
        for _ in range(8):
            raw = await self._store.get_info(update.userid)
            old = self._decode(raw) if raw is not None else None
            if old is None and (update.username is None or update.permissions is None):
                raise ValueError("新增用户必须同时提供 username 和 permissions。")
            info = UserInfo(
                userid=update.userid,
                username=update.username if update.username is not None else old.username,
                permissions=update.permissions if update.permissions is not None else dict(old.permissions),
            )
            self.validate_updates([info])
            encoded = self._encode(info)
            if await self._store.compare_update(update.userid, raw, encoded):
                return self._decode(encoded)
        raise UserStorageUnavailableError()

    async def get_info(self, userid: str) -> UserProfile | None:
        raw = await self._store.get_info(userid)
        return self._decode(raw) if raw is not None else None

    async def create_login(self, userid: str, access_token: str) -> UserLoginStatus:
        login = UserLoginRequest(userid=userid, access_token=access_token)
        ttl = await self._store.create_login(login.userid, login.access_token, self._settings.ttl_seconds)
        if ttl == -1:
            raise UserTokenConflictError()
        if not ttl:
            raise UserNotFoundError()
        return UserLoginStatus(userid, True, ttl)

    async def get(self, userid: str) -> UserSnapshot | None:
        result = await self._store.get_login(userid)
        if result is None:
            return None
        raw, ttl_ms = result
        profile = self._decode(raw)
        return UserSnapshot(profile.userid, profile.username, profile.permissions,
                            True, max(1, math.ceil(ttl_ms / 1000)))

    async def is_login_valid(self, userid: str) -> bool:
        return await self.get(userid) is not None

    async def touch_login(self, access_token: str) -> UserLoginStatus:
        token = UserToken(access_token=access_token).access_token
        result = await self._store.touch_login(token, self._settings.ttl_seconds)
        if result is None:
            return UserLoginStatus(None, False, 0)
        # 资料与续期在同一次 Redis 脚本中读取，避免随后查询姓名时用户已被移除。
        profile = self._decode(result[2])
        return UserLoginStatus(result[0], True, result[1], profile.username)
