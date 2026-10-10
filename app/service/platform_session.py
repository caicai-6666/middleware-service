"""单进程平台会话缓存，所有状态操作通过同一把锁串行化。"""

import asyncio
import secrets
import time
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from app.config.platform import Platform
from app.config.session import SessionSettings
from app.service.platform_stream import PlatformStreamManager, PlatformStreamResources

logger = logging.getLogger(__name__)


class PlatformResourcesUnavailableError(Exception):
    """客户端建立失败，登录尚未生效。"""


class PlatformConsumeForbiddenError(Exception):
    """平台不允许消费目标流。"""


class PlatformPublishForbiddenError(Exception):
    """平台不允许向目标流发布。"""


class InvalidSecretError(Exception):
    """密钥不属于已登记平台。"""


class PlatformAlreadyOnlineError(Exception):
    """平台存在尚未过期的会话。"""


class InvalidAccessTokenError(Exception):
    """令牌不存在或已过期，不能续期。"""


@dataclass(frozen=True)
class HeartbeatResult:
    code: str
    expires_in_seconds: int
    heartbeat_interval_seconds: int


@dataclass(frozen=True)
class LoginResult:
    access_token: str = field(repr=False)
    code: str
    expires_in_seconds: int
    heartbeat_interval_seconds: int


class PlatformSessionService:
    def __init__(
        self, platforms: Mapping[str, Platform], settings: SessionSettings,
        clock: Callable[[], float] = time.monotonic,
        stream_manager: PlatformStreamManager | None = None,
    ):
        self._platforms = platforms
        self._settings = settings
        self._clock = clock
        self._stream_manager = stream_manager
        # 平台之间互不阻塞；同一平台的登录、续期和资源关闭必须串行。
        self._locks = {code: asyncio.Lock() for code in platforms}
        self._token_to_code: dict[str, str] = {}
        self._code_to_token: dict[str, str] = {}
        self._expires_at: dict[str, float] = {}
        self._resources: dict[str, PlatformStreamResources] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self._closing = False
        self._release_listeners: list[Callable[[str], None]] = []

    def add_release_listener(self, listener: Callable[[str], None]) -> None:
        """注册同步内存回收回调，在平台锁内与会话失效一起执行。"""
        self._release_listeners.append(listener)

    async def _release(self, code: str) -> None:
        token = self._code_to_token.pop(code, None)
        if token is not None:
            self._token_to_code.pop(token, None)
            self._expires_at.pop(token, None)
        for listener in self._release_listeners:
            listener(code)
        self._resources.pop(code, None)
        stack = self._stacks.pop(code, None)
        if stack is not None:
            # 先撤销身份再关闭；连接异常不得恢复旧会话或终止整个清理循环。
            try:
                await stack.aclose()
            except Exception:
                logger.error("平台 %s 的 Stream 资源关闭失败。", code)

    async def _expire(self, code: str) -> None:
        token = self._code_to_token.get(code)
        if token is not None and self._expires_at[token] <= self._clock():
            await self._release(code)

    async def login(self, platform_code: str, secret: str) -> LoginResult:
        platform = self._platforms.get(platform_code)
        if platform is None or not secrets.compare_digest(
            secret.encode("utf-8"), platform.secret.get_secret_value().encode("utf-8")
        ):
            raise InvalidSecretError()
        code = platform.code
        async with self._locks[code]:
            if self._closing:
                raise PlatformResourcesUnavailableError()
            await self._expire(code)
            if code in self._code_to_token:
                raise PlatformAlreadyOnlineError()
            stack = AsyncExitStack()
            try:
                if self._stream_manager is not None:
                    async with asyncio.timeout(30):
                        resources = await stack.enter_async_context(self._stream_manager.open(code))
                    self._resources[code] = resources
            except BaseException as error:
                await stack.aclose()
                if isinstance(error, Exception):
                    raise PlatformResourcesUnavailableError() from None
                raise
            # 连接准备完成才签发令牌并开始计时，连接耗时不占用会话有效期。
            self._stacks[code] = stack
            token = secrets.token_urlsafe(32)
            while token in self._token_to_code:
                token = secrets.token_urlsafe(32)
            self._token_to_code[token] = code
            self._code_to_token[code] = token
            self._expires_at[token] = self._clock() + self._settings.ttl_seconds
            return LoginResult(token, code, self._settings.ttl_seconds,
                               self._settings.heartbeat_interval_seconds)

    async def heartbeat(self, platform_code: str, access_token: str) -> HeartbeatResult:
        if platform_code not in self._locks:
            raise InvalidAccessTokenError()
        async with self._locks[platform_code]:
            await self._expire(platform_code)
            if self._token_to_code.get(access_token) != platform_code:
                raise InvalidAccessTokenError()
            self._expires_at[access_token] = self._clock() + self._settings.ttl_seconds
            return HeartbeatResult(platform_code, self._settings.ttl_seconds,
                                   self._settings.heartbeat_interval_seconds)

    async def resolve_platform(self, access_token: str) -> str | None:
        code = self._token_to_code.get(access_token)
        if code is None:
            return None
        async with self._locks[code]:
            await self._expire(code)
            return self._token_to_code.get(access_token)

    @asynccontextmanager
    async def consumer_resources(self, code: str, token: str, stream: str):
        """消费期间持有平台锁，保护订阅与确认不被失活清理中断。"""
        if code not in self._locks:
            raise InvalidAccessTokenError()
        async with self._locks[code]:
            await self._expire(code)
            if self._closing or self._token_to_code.get(token) != code:
                raise InvalidAccessTokenError()
            resources = self._resources.get(code)
            if resources is None:
                raise PlatformResourcesUnavailableError()
            if resources.consume_stream != stream and stream not in resources.consume_streams:
                raise PlatformConsumeForbiddenError()
            # 为本次操作选择独立消费名称，避免入库与删除共享 offset。
            yield resources if resources.consume_stream == stream else replace(
                resources, consume_stream=stream, consumer_name=f"{code}:{stream}",
            )

    async def check_publish_access(self, code: str, token: str, stream: str) -> None:
        if code not in self._locks:
            raise InvalidAccessTokenError()
        async with self._locks[code]:
            await self._expire(code)
            self._publisher(code, token, stream)

    def _publisher(self, code: str, token: str, stream: str):
        if self._closing or self._token_to_code.get(token) != code:
            raise InvalidAccessTokenError()
        resources = self._resources.get(code)
        if resources is None:
            raise PlatformResourcesUnavailableError()
        if resources.publish_stream != stream and stream not in resources.publish_streams:
            raise PlatformPublishForbiddenError()
        return resources.producer

    async def publish(self, code: str, token: str, stream: str, payload: bytes) -> None:
        if code not in self._locks:
            raise InvalidAccessTokenError()
        async with self._locks[code]:
            await self._expire(code)
            producer = self._publisher(code, token, stream)
            await producer.send_wait(stream, payload, timeout=5)

    def alive_snapshot(self) -> dict[str, bool]:
        """返回全部平台的会话状态，不触发续期或网络资源清理。"""
        # 同一事件循环中无 await，读取期间不会与登录/心跳协程交错。
        # 统一使用一次时间采样，过期但尚未清理的缓存也必须显示为失活。
        now = self._clock()
        return {
            code: not self._closing and self._expires_at.get(token, now) > now
            for code in self._platforms
            for token in (self._code_to_token.get(code),)
        }

    async def cleanup_expired(self) -> None:
        async def cleanup(code):
            async with self._locks[code]:
                await self._expire(code)
        await asyncio.gather(*(cleanup(code) for code in self._locks))

    async def run_cleanup(self) -> None:
        while True:
            await asyncio.sleep(self._settings.cleanup_interval_seconds)
            cleanup = asyncio.create_task(self.cleanup_expired())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # 停机取消后台循环时，先完成正在进行的资源关闭。
                await cleanup
                raise

    async def clear(self) -> None:
        self._closing = True
        async def close(code):
            async with self._locks[code]:
                await self._release(code)
        await asyncio.gather(*(close(code) for code in self._locks))
