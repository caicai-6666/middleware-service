"""用户总表、增量信息与登录缓存入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.router.dependencies import AuthenticatedPlatform, require_platform, require_registry, require_knowledge_accumulation, require_user_table_writer
from app.service.user import (
    UserInfo, UserInfoPatch, UserLoginRequest, UserToken, UserKey, UserFilePermission,
    UserNotFoundError, UserTokenConflictError, Departments,
)

router = APIRouter(prefix="/user", tags=["用户"])


class UserTableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[UserInfo] = Field(description="完整用户信息总表；按 userid 重建，未包含的用户及其登录缓存被移除，空数组清空总表。")


class UserTableResponse(BaseModel):
    count: int = Field(description="重建后用户信息总表中的用户总数。")


class UserLoginResponse(BaseModel):
    userid: str | None = Field(description="有效登录对应的用户唯一标识；用户令牌无效或过期时为 null。")
    is_logged_in: bool = Field(description="用户是否存在有效登录缓存；首次登录写入后为 true，普通查询不会激活未知或已过期用户。")
    ttl: int = Field(description="登录写入或有效查询续期后的完整有效秒数；普通查询无有效登录时为 0。")


class UserLoginQueryResponse(UserLoginResponse):
    username: str | None = Field(description="有效登录对应用户的当前用户名；令牌无效或过期时为 null。")
    departments: Departments | None = Field(description="有效登录对应用户当前所属的部门名称列表，无部门或旧资料为 []；用户令牌无效或过期时为 null，不表示文件权限或管理员身份。")


@router.post('/file-permission', response_model=UserFilePermission, summary='查询用户在本平台的文件权限',
             description='平台 Bearer 认证后按 userid 查询 Redis 当前权威权限，仅返回调用平台等级；不要求用户 token 或管理员身份，不创建或续期用户登录。',
             responses={401: {'description': '平台令牌缺失、无效或已过期'},
                        422: {'description': '用户标识不合法或携带额外字段'},
                        503: {'description': '用户存储不可用或权限数据异常，调用方必须拒绝放行'}})
async def query_user_file_permission(
    body: UserKey, request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> UserFilePermission:
    # 平台范围只从认证身份获取，不接收平台、等级、登录状态或管理员绕过参数。
    result = await request.app.state.users.get_file_permission(body.userid, platform.code)
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.put("/login-cache", response_model=UserLoginResponse, summary="写入用户登录缓存",
            responses={401: {"description": "平台令牌无效"}, 403: {"description": "仅 knowledge-accumulation 可写入登录缓存"},
                       404: {"description": "用户资料不存在，请先同步"}, 409: {"description": "用户令牌已绑定其他用户"}, 422: {"description": "用户标识或请求字段不合法"},
                       503: {"description": "用户存储不可用或写入结果不确定"}})
async def create_user_login(
    body: UserLoginRequest, request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_knowledge_accumulation)],
) -> UserLoginResponse:
    # knowledge-accumulation 已完成实际用户身份验证；本接口只记录成功登录事件，不接受登录布尔状态。
    try:
        status = await request.app.state.users.create_login(body.userid, body.access_token)
    except UserTokenConflictError:
        raise HTTPException(status_code=409, detail="用户访问令牌已绑定其他用户") from None
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="用户不存在，请先同步用户信息") from None
    response.headers["Cache-Control"] = "no-store"
    return UserLoginResponse(userid=status.userid, is_logged_in=status.is_logged_in, ttl=status.ttl)


@router.post("/full-table", response_model=UserTableResponse, summary="重建用户信息总表",
             responses={401: {"description": "平台令牌无效"}, 503: {"description": "用户存储不可用或操作结果不确定"}, 403: {"description": "仅注册中心或 knowledge-accumulation 可维护用户总表"},
                        422: {"description": "用户字段、重复 userid 或权限平台不合法"}})
async def replace_user_table(
    body: UserTableRequest, request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_user_table_writer)],
) -> UserTableResponse:
    try:
        count = await request.app.state.users.replace_all(body.users)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    response.headers["Cache-Control"] = "no-store"
    return UserTableResponse(count=count)


@router.post("/update", response_model=UserInfo, summary="更新单个用户信息",
             responses={401: {"description": "平台令牌无效"}, 503: {"description": "用户存储不可用或操作结果不确定"}, 403: {"description": "仅注册中心可维护"},
                        422: {"description": "更新内容不合法或新增用户缺少完整信息"}})
async def update_user_info(
    body: UserInfoPatch, request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_registry)],
) -> UserInfo:
    try:
        user = await request.app.state.users.update_info(body)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    response.headers["Cache-Control"] = "no-store"
    return UserInfo(userid=user.userid, username=user.username, permissions=dict(user.permissions),
                    departments=list(user.departments))


@router.post("/login-cache", response_model=UserLoginQueryResponse, summary="查询并续期用户登录缓存",
             responses={401: {"description": "平台令牌无效"}, 503: {"description": "用户存储不可用或操作结果不确定"}, 422: {"description": "用户标识不合法"}})
async def query_user_login(
    body: UserToken, request: Request, response: Response,
    platform: Annotated[AuthenticatedPlatform, Depends(require_platform)],
) -> UserLoginQueryResponse:
    status = await request.app.state.users.touch_login(body.access_token)
    response.headers["Cache-Control"] = "no-store"
    return UserLoginQueryResponse(userid=status.userid, username=status.username,
                                  departments=list(status.departments) if status.departments is not None else None,
                                  is_logged_in=status.is_logged_in, ttl=status.ttl)
