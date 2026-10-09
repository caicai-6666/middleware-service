"""平台登录与心跳 HTTP 契约，业务状态由会话服务维护。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.router.dependencies import AuthenticatedPlatform, require_platform
from app.service.platform_session import PlatformResourcesUnavailableError, InvalidAccessTokenError, InvalidSecretError, PlatformAlreadyOnlineError

router = APIRouter(prefix="/platform", tags=["平台认证"])


class PlatformRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform_code: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
        description="平台唯一代号，必须与认证凭据所属平台一致。",
    )


class LoginRequest(PlatformRequest):
    secret: SecretStr = Field(min_length=1, description="平台 YAML 白名单中的密钥。")


class LoginResponse(BaseModel):
    access_token: str = Field(repr=False, description="访问令牌，仅登录成功时签发。")
    token_type: Literal["bearer"] = Field(default="bearer", description="令牌认证类型。")
    code: str = Field(description="认证成功的平台代号。")
    heartbeat_interval_seconds: int = Field(description="建议发送心跳的间隔秒数，小于会话有效期。")
    expires_in_seconds: int = Field(description="自登录成功起的会话有效秒数。")


@router.post("/login", response_model=LoginResponse, summary="平台登录",
             responses={401: {"description": "平台代号或密钥无效"}, 409: {"description": "平台已登录"}, 503: {"description": "消息服务暂不可用"}})
async def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    try:
        result = await request.app.state.platform_sessions.login(body.platform_code, body.secret.get_secret_value())
    except PlatformResourcesUnavailableError:
        raise HTTPException(status_code=503, detail="消息服务暂不可用，请稍后重试") from None
    except InvalidSecretError:
        raise HTTPException(status_code=401, detail="平台代号或密钥无效") from None
    except PlatformAlreadyOnlineError:
        raise HTTPException(status_code=409, detail="平台已登录") from None
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(access_token=result.access_token, code=result.code,
                         expires_in_seconds=result.expires_in_seconds,
                         heartbeat_interval_seconds=result.heartbeat_interval_seconds)


class HeartbeatResponse(BaseModel):
    code: str = Field(description="本次续期的平台代号。")
    expires_in_seconds: int = Field(description="从本次心跳成功起重新计算的会话有效秒数。")
    heartbeat_interval_seconds: int = Field(description="建议下次发送心跳的间隔秒数。")




@router.post("/heartbeat", response_model=HeartbeatResponse, summary="平台心跳",
             responses={401: {"description": "令牌缺失、无效、已过期或与平台不匹配"}})
async def heartbeat(
    request: Request,
    response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> HeartbeatResponse:
    unauthorized = HTTPException(
        status_code=401, detail="令牌缺失、无效、已过期或与平台不匹配",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        result = await request.app.state.platform_sessions.heartbeat(platform.code, platform.access_token)
    except InvalidAccessTokenError:
        raise unauthorized from None
    response.headers["Cache-Control"] = "no-store"
    return HeartbeatResponse(
        code=result.code, expires_in_seconds=result.expires_in_seconds,
        heartbeat_interval_seconds=result.heartbeat_interval_seconds,
    )


class PlatformInfo(BaseModel):
    code: str = Field(description="平台唯一代号。")
    name: str = Field(description="平台中文名称。")
    description: str = Field(description="平台用途描述。")
    is_alive: bool = Field(description="是否存在尚未过期的登录会话；不代表业务或 RabbitMQ 健康状态。")


@router.get("/list", response_model=list[PlatformInfo], summary="查询所有平台")
async def list_platforms(
    request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> list[PlatformInfo]:
    statuses = request.app.state.platform_sessions.alive_snapshot()
    response.headers["Cache-Control"] = "no-store"
    # 显式选择公开字段，避免直接序列化配置或会话而泄露凭据。
    return [
        PlatformInfo(code=platform.code, name=platform.name,
                     description=platform.description, is_alive=statuses[platform.code])
        for platform in request.app.state.platforms.values()
    ]
