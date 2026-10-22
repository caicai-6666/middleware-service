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


async def require_registry(
    request: Request,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> AuthenticatedPlatform:
    """注册中心专用接口复用已认证的平台身份，支持配置自定义注册中心代码。"""
    if platform.code != request.app.state.registry_platform_code:
        raise HTTPException(status_code=403, detail="仅注册中心可以执行此操作",
                            headers={"Cache-Control": "no-store"})
    return platform


async def require_knowledge_accumulation(
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> AuthenticatedPlatform:
    """知识沉淀平台专用接口，身份仅取自有效平台令牌。"""
    if platform.code != "knowledge-accumulation":
        raise HTTPException(status_code=403, detail="仅 knowledge-accumulation 平台可以执行此操作",
                            headers={"Cache-Control": "no-store"})
    return platform


async def require_user_table_writer(
    request: Request,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> AuthenticatedPlatform:
    """用户总表允许知识沉淀平台及配置的注册中心维护。"""
    if platform.code not in {"knowledge-accumulation", request.app.state.registry_platform_code}:
        raise HTTPException(status_code=403, detail="仅注册中心或 knowledge-accumulation 平台可以更新用户总表",
                            headers={"Cache-Control": "no-store"})
    return platform


async def require_platform_admin_table_writer(
    request: Request,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> AuthenticatedPlatform:
    """平台管理员总表允许知识沉淀平台及配置的注册中心维护。"""
    if platform.code not in {"knowledge-accumulation", request.app.state.registry_platform_code}:
        raise HTTPException(status_code=403, detail="仅注册中心或 knowledge-accumulation 平台可以更新平台管理员总表",
                            headers={"Cache-Control": "no-store"})
    return platform
