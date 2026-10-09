"""供业务接口复用的平台会话认证依赖。"""

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False, description="平台登录接口签发的访问令牌。")


@dataclass(frozen=True)
class AuthenticatedPlatform:
    code: str
    access_token: str = field(repr=False)


async def require_platform(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedPlatform:
    unauthorized = HTTPException(
        status_code=401, detail="令牌缺失、无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    # 平台身份只从有效会话映射中获得，不信任请求中的平台代号。
    code = await request.app.state.platform_sessions.resolve_platform(credentials.credentials)
    if code is None:
        raise unauthorized
    return AuthenticatedPlatform(code=code, access_token=credentials.credentials)
