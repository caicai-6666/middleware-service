"""平台登录与心跳 HTTP 契约，业务状态由会话服务维护。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.router.dependencies import AuthenticatedPlatform, require_platform, require_registry, require_platform_admin_table_writer
from app.service.platform_session import PlatformResourcesUnavailableError, InvalidAccessTokenError, InvalidSecretError, PlatformAlreadyOnlineError
from app.service.user import PlatformCode, UserKey

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


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_code: str | None = Field(default=None, description="兼容旧客户端的字段，不参与认证或身份选择；身份始终取自 Bearer 令牌。")


class HeartbeatResponse(BaseModel):
    code: str = Field(description="本次续期的平台代号。")
    expires_in_seconds: int = Field(description="从本次心跳成功起重新计算的会话有效秒数。")
    heartbeat_interval_seconds: int = Field(description="建议下次发送心跳的间隔秒数。")




@router.post("/heartbeat", response_model=HeartbeatResponse, summary="平台心跳",
             responses={401: {"description": "令牌缺失、无效、已过期或与平台不匹配"},
                        422: {"description": "心跳请求包含不支持的字段或类型"}})
async def heartbeat(
    request: Request,
    response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
    body: HeartbeatRequest | None = None,
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
    supports_a2a: bool = Field(description="配置中声明的是否参与 A2A 协议。")
    is_sub_management_platform: bool = Field(description="配置中声明的是否为子管理平台。")
    is_management_entry: bool = Field(description="配置中声明的是否作为管理平台入口。")
    a2aresponsibility: str = Field(description="平台在 A2A 协作中的职责；子管理平台为非空说明，其他平台未配置时为空字符串。")
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
                     description=platform.description, is_alive=statuses[platform.code],
                     supports_a2a=platform.supports_a2a,
                     is_sub_management_platform=platform.is_sub_management_platform,
                     is_management_entry=platform.is_management_entry,
                     a2aresponsibility=platform.a2aresponsibility)
        for platform in request.app.state.platforms.values()
    ]


class PlatformAdminTableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admins: dict[PlatformCode, list[str]] = Field(
        description="所有平台的完整管理员映射，键为已配置 platform_code，值为 userid 字符串列表；省略的平台清空，空对象清空全部已配置平台。",
    )


class PlatformAdminTableResponse(BaseModel):
    admin_count: int = Field(description="全量更新后管理员关系总数；单个平台内去重，同一用户管理多个平台时分别计数。")


class PlatformAdminRequest(UserKey):
    platform_code: PlatformCode = Field(description="要增删管理员的目标平台代码，必须在 YAML 中配置；不参与调用方身份认证。")


class PlatformAdminChangeResponse(BaseModel):
    platform_code: str = Field(description="本次操作的目标平台代码。")
    userid: str = Field(description="本次增删的管理员用户标识。")
    changed: bool = Field(description="本次操作是否实际改变集合；重复添加或删除不存在成员时为 false。")


ADMIN_RESPONSES = {
    401: {"description": "平台令牌缺失、无效或过期"},
    403: {"description": "仅注册中心可以执行此操作"},
    422: {"description": "平台代码、用户标识或请求字段不合法"},
    503: {"description": "Redis 不可用或写入结果不确定"},
}


@router.post("/admins/full-table", response_model=PlatformAdminTableResponse,
             summary="全量更新平台管理员",
             responses={**ADMIN_RESPONSES, 403: {"description": "仅注册中心或 knowledge-accumulation 可更新平台管理员总表"}})
async def replace_platform_admins(
    body: PlatformAdminTableRequest, request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform_admin_table_writer)],
) -> PlatformAdminTableResponse:
    try:
        count = await request.app.state.platform_admins.replace_all_admins(body.admins)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    response.headers["Cache-Control"] = "no-store"
    return PlatformAdminTableResponse(admin_count=count)


@router.post("/admins/add", response_model=PlatformAdminChangeResponse,
             summary="新增平台管理员", responses=ADMIN_RESPONSES)
async def add_platform_admin(
    body: PlatformAdminRequest, request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_registry)],
) -> PlatformAdminChangeResponse:
    try:
        changed = await request.app.state.platform_admins.add_admin(body.platform_code, body.userid)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    response.headers["Cache-Control"] = "no-store"
    return PlatformAdminChangeResponse(platform_code=body.platform_code, userid=body.userid, changed=changed)


@router.post("/admins/remove", response_model=PlatformAdminChangeResponse,
             summary="删除平台管理员", responses=ADMIN_RESPONSES)
async def remove_platform_admin(
    body: PlatformAdminRequest, request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_registry)],
) -> PlatformAdminChangeResponse:
    try:
        changed = await request.app.state.platform_admins.remove_admin(body.platform_code, body.userid)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    response.headers["Cache-Control"] = "no-store"
    return PlatformAdminChangeResponse(platform_code=body.platform_code, userid=body.userid, changed=changed)


@router.get("/admins/list", response_model=list[str], summary="查询当前平台管理员",
            responses={401: {"description": "平台令牌缺失、无效或过期"},
                       503: {"description": "Redis 不可用，无法读取管理员列表"}})
async def list_current_platform_admins(
    request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> list[str]:
    # 目标平台只从有效令牌解析，避免调用方通过传参查询其他平台管理员。
    userids = await request.app.state.platform_admins.get_admins(platform.code)
    response.headers["Cache-Control"] = "no-store"
    return sorted(userids)
